from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from analysis.ev import EVWeights
from jra_scraper.config import ScrapeConfig
from jra_scraper.live_snapshot import LiveSnapshotCollector, LiveSnapshotDeadlineExceeded
from strategy.live_odds import live_combo_key
from src.agents import (
    AnalyzerAgent,
    BetBuilderAgent,
    EVCalculatorAgent,
    ReviewerAgent,
    SimulatorAgent,
    WorkflowSettings,
    apply_ticket_repair_actions,
)
from src.artifacts import atomic_write_json as _atomic_json
from src.artifacts import file_sha256 as _file_sha256
from src.deadline import DeadlinePlan, DeadlineSettings, build_deadline_plan


FINAL_STAGE_ORDER = (
    "01_data_collector.json",
    "02_analyzer.json",
    "03_simulator.json",
    "04_ev_calculator.json",
    "05_bet_builder.json",
    "06_reviewer.json",
    "final_decision.json",
)


class FinalPredictionWorkflow:
    """Fast race-day path using cached history and one coherent JRA snapshot."""

    def __init__(
        self,
        live_config: ScrapeConfig,
        *,
        output_dir: Path,
        workflow_settings: WorkflowSettings | None = None,
        deadline_settings: DeadlineSettings | None = None,
        weights: EVWeights | None = None,
        now: Callable[[], datetime] | None = None,
        live_collector: LiveSnapshotCollector | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_settings = workflow_settings or WorkflowSettings(max_repair_attempts=0)
        self.deadline_settings = deadline_settings or DeadlineSettings()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.live_collector = live_collector or LiveSnapshotCollector(live_config, now=self._now)
        self.analyzer = AnalyzerAgent()
        self.simulator = SimulatorAgent()
        self.ev_calculator = EVCalculatorAgent(weights=weights)
        self.bet_builder = BetBuilderAgent(self.workflow_settings)
        self.quantitative_reviewer = ReviewerAgent(self.workflow_settings)
        self.final_reviewer = FinalReviewerAgent(
            settings=self.deadline_settings,
            now=self._now,
        )

    def run(
        self,
        race_config: dict[str, object],
        *,
        baseline: dict[str, object] | list[dict[str, object]],
    ) -> dict[str, object]:
        plan = build_deadline_plan(
            race_config,
            now=self._now(),
            settings=self.deadline_settings,
        )
        if not plan.may_start_network_refresh:
            cutoff_reached = plan.execution_mode == "too_late"
            return self._finish_early(
                plan,
                code=(
                    "NO_GO_CUTOFF_REACHED"
                    if cutoff_reached
                    else "NO_GO_INSUFFICIENT_TIME_BUDGET"
                ),
                reason=(
                    "final prediction cannot start after the T-5 output deadline"
                    if cutoff_reached
                    else "remaining time is below the measured live-refresh safety budget"
                ),
            )

        baseline_rows = extract_baseline_rows(baseline, race_config)
        if not baseline_rows:
            return self._finish_early(
                plan,
                code="NO_GO_BASELINE_MISSING",
                reason="cached pre-race history is missing; final mode will not start a full scrape",
            )

        try:
            live = self.live_collector.collect(
                race_config,
                deadline_at=plan.output_deadline,
                emit_reserve_seconds=self.deadline_settings.emit_reserve_seconds,
            )
        except LiveSnapshotDeadlineExceeded:
            return self._finish_early(
                plan,
                code="NO_GO_REFRESH_BUDGET_EXHAUSTED",
                reason="live refresh stopped to preserve the T-5 output deadline",
            )
        except Exception as exc:  # keep a failed refresh from producing bets
            return self._finish_early(
                plan,
                code="NO_GO_LIVE_REFRESH_FAILED",
                reason=f"live JRA refresh failed: {type(exc).__name__}: {exc}",
            )
        finally:
            self._close_live_collector()

        merged_rows, lineup = merge_live_entries(
            baseline_rows,
            list(live.get("entries") or []),
            zero_history_horse_numbers=_configured_zero_history_numbers(race_config),
        )
        baseline_quality = build_baseline_quality(
            baseline,
            baseline_rows,
            race_config=race_config,
        )
        collected = dict(live)
        collected["rows"] = merged_rows
        collected["entries"] = list(live.get("entries") or [])
        collected["odds_snapshots"] = live_odds_snapshots(live)
        collected["lineup"] = lineup
        collected["baseline_quality"] = baseline_quality
        collected["deadline"] = plan.to_dict()

        analyzed = self.analyzer.run(
            merged_rows,
            odds_snapshots=list(collected.get("odds_snapshots") or []),
        )
        analyzed["snapshot_id"] = live.get("snapshot_id", "")
        simulated = self.simulator.run(list(analyzed.get("feature_rows") or []))
        simulated["snapshot_id"] = live.get("snapshot_id", "")
        calculated = self.ev_calculator.run(list(simulated.get("scenario_rows") or []))
        calculated["snapshot_id"] = live.get("snapshot_id", "")
        scenario_rows = list(simulated.get("scenario_rows") or [])
        bet_plan = self.bet_builder.run(
            list(calculated.get("ev_rows") or []),
            combo_odds=list(live.get("combo_odds") or []),
            race_configs=[race_config],
        )
        bet_plan["snapshot_id"] = live.get("snapshot_id", "")
        bet_plan["reviewer_ticket_repair_enabled"] = True
        quantitative_review = self.quantitative_reviewer.run(
            collected,
            list(simulated.get("scenario_rows") or []),
            list(calculated.get("ev_rows") or []),
            bet_plan,
            attempt=0,
        )
        initial_quantitative_review = dict(quantitative_review)
        repaired_plan = apply_ticket_repair_actions(
            bet_plan,
            list(quantitative_review.get("repair_actions") or []),
        )
        if repaired_plan is not bet_plan:
            bet_plan = repaired_plan
            quantitative_review = self.quantitative_reviewer.run(
                collected,
                scenario_rows,
                list(calculated.get("ev_rows") or []),
                bet_plan,
                attempt=0,
            )
            quantitative_review["repair_applied"] = True
            quantitative_review["repair_actions"] = list(
                initial_quantitative_review.get("repair_actions") or []
            )
            quantitative_review["pre_repair_status"] = initial_quantitative_review.get("status", "")
            quantitative_review["pre_repair_reason"] = initial_quantitative_review.get("reason", "")
        review = self.final_reviewer.run(
            plan=plan,
            collected=collected,
            ticket_plan=bet_plan,
            quantitative_review=quantitative_review,
        )
        if review["decision"] != "GO":
            bet_plan = invalidate_ticket_plan(bet_plan, str(review.get("reason", "")))

        decision = {
            "status": review["status"],
            "decision": review["decision"],
            "decision_code": review["decision_code"],
            "race_id": lineup.get("race_id", ""),
            "race_name": str(race_config.get("race_name", "")),
            "post_time": plan.post_time.isoformat(),
            "output_deadline": plan.output_deadline.isoformat(),
            "issued_at": review["issued_at"],
            "execution_mode": plan.execution_mode,
            "snapshot_id": live.get("snapshot_id", ""),
            "weather": dict(live.get("conditions") or {}).get("weather", ""),
            "track_condition": dict(live.get("conditions") or {}).get("track_condition", ""),
            "reason": review["reason"],
            "checks": review["checks"],
            "tickets": list(bet_plan.get("tickets") or []),
        }
        payload = {
            "data_collector": collected,
            "analyzer": analyzed,
            "simulator": simulated,
            "ev_calculator": calculated,
            "bet_builder": bet_plan,
            "reviewer": review,
            "final_decision": decision,
        }
        self._write_outputs(payload)
        return payload

    def _finish_early(self, plan: DeadlinePlan, *, code: str, reason: str) -> dict[str, object]:
        self._close_live_collector()
        issued_at = _as_utc(self._now()).isoformat()
        review = {
            "status": "NG",
            "decision": "NO_GO",
            "decision_code": code,
            "reason": reason,
            "fix": "prepare baseline earlier or wait for the next race",
            "issued_at": issued_at,
            "deadline": plan.to_dict(),
            "checks": {
                "deadline": code != "NO_GO_CUTOFF_REACHED",
                "minimum_time_budget": code != "NO_GO_INSUFFICIENT_TIME_BUDGET",
            },
        }
        decision = {
            "status": "NG",
            "decision": "NO_GO",
            "decision_code": code,
            "post_time": plan.post_time.isoformat(),
            "output_deadline": plan.output_deadline.isoformat(),
            "issued_at": issued_at,
            "execution_mode": plan.execution_mode,
            "reason": reason,
            "tickets": [],
        }
        skipped = {"status": "skipped", "reason": reason}
        payload = {
            "data_collector": skipped,
            "analyzer": skipped,
            "simulator": skipped,
            "ev_calculator": skipped,
            "bet_builder": {**skipped, "tickets": []},
            "reviewer": review,
            "final_decision": decision,
        }
        self._write_outputs(payload)
        return payload

    def _close_live_collector(self) -> None:
        try:
            self.live_collector.close()
        except Exception:
            pass

    def _write_outputs(self, payload: dict[str, object]) -> None:
        stage_map = {
            "01_data_collector.json": payload.get("data_collector"),
            "02_analyzer.json": payload.get("analyzer"),
            "03_simulator.json": payload.get("simulator"),
            "04_ev_calculator.json": payload.get("ev_calculator"),
            "05_bet_builder.json": payload.get("bet_builder"),
            "06_reviewer.json": payload.get("reviewer"),
            "final_decision.json": payload.get("final_decision"),
        }
        for filename, body in stage_map.items():
            _atomic_json(self.output_dir / filename, body or {})
        manifest = {
            "schema_version": "1",
            "producer": "src.final_workflow.FinalPredictionWorkflow",
            "stage_order": list(FINAL_STAGE_ORDER),
            "artifacts": {
                filename: {"sha256": _file_sha256(self.output_dir / filename)}
                for filename in FINAL_STAGE_ORDER
            },
        }
        _atomic_json(self.output_dir / "run_manifest.json", manifest)


class FinalReviewerAgent:
    def __init__(
        self,
        *,
        settings: DeadlineSettings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._now = now or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        *,
        plan: DeadlinePlan,
        collected: dict[str, object],
        ticket_plan: dict[str, object],
        quantitative_review: dict[str, object],
    ) -> dict[str, object]:
        now = _as_utc(self._now())
        entries = [dict(row) for row in list(collected.get("entries") or [])]
        tickets = [dict(row) for row in list(ticket_plan.get("tickets") or [])]
        quality = dict(collected.get("quality_report") or {})
        lineup = dict(collected.get("lineup") or {})
        baseline_quality = dict(collected.get("baseline_quality") or {})
        conditions = dict(collected.get("conditions") or {})
        combo_odds = [dict(row) for row in list(collected.get("combo_odds") or [])]
        snapshot_id = str(collected.get("snapshot_id", ""))

        odds_age = _age_seconds(now, collected.get("official_odds_as_of"))
        conditions_age = _age_seconds(now, conditions.get("captured_at"))
        body_statuses = {str(row.get("body_weight_status", "unpublished")) for row in entries}
        selected_numbers = _ticket_horse_numbers(tickets, entries=entries)
        extreme_selected = [
            str(row.get("horse_number", ""))
            for row in entries
            if str(row.get("horse_number", "")) in selected_numbers
            and abs(_to_int(row.get("body_weight_change")))
            > self.settings.max_abs_body_weight_change_kg
        ]
        checks = {
            "deadline": now <= _as_utc(plan.output_deadline),
            "snapshot_complete": bool(collected.get("snapshot_complete")),
            "combination_coverage": bool(
                dict(quality.get("combination_coverage") or {}).get("complete")
            ),
            "snapshot_coherent": bool(combo_odds)
            and bool(snapshot_id)
            and all(
                str(row.get("snapshot_id", "")) == snapshot_id
                and _is_true(row.get("snapshot_complete"))
                for row in combo_odds
            ),
            "all_bet_types": not list(quality.get("bet_types_missing") or []),
            "all_entry_win_odds": bool(entries)
            and int(quality.get("missing_current_odds_entries", 0) or 0) == 0,
            "official_odds_timestamp": odds_age is not None,
            "official_odds_timestamps_complete": bool(
                quality.get("official_odds_timestamps_complete")
            ),
            "odds_fresh": odds_age is not None
            and -60 <= odds_age <= self.settings.odds_max_age_seconds,
            "conditions_present": bool(conditions.get("weather")) and bool(conditions.get("track_condition")),
            "conditions_fresh": conditions_age is not None
            and -60 <= conditions_age <= self.settings.conditions_max_age_seconds,
            "body_weights_released": bool(entries)
            and body_statuses.issubset({"published", "not_measured"})
            and all(
                row.get("body_weight_status") != "published"
                or _to_int(row.get("current_body_weight")) > 0
                for row in entries
            ),
            "selected_body_weight_change_safe": not extreme_selected,
            "lineup_matches_baseline": bool(lineup.get("matches")),
            "baseline_history_complete": bool(baseline_quality.get("history_complete")),
            "baseline_review": bool(baseline_quality.get("review_ok")),
            "baseline_parser_quality": bool(baseline_quality.get("parser_quality_ok")),
            "baseline_manifest": bool(baseline_quality.get("manifest_ok")),
            "quantitative_review": quantitative_review.get("status") == "OK",
            "tickets_present": bool(tickets),
            "tickets_use_jra_live_odds": _tickets_have_exact_live_odds(tickets, combo_odds),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if not failed:
            code = "GO_ALL_GATES_PASSED"
            decision = "GO"
            status = "OK"
            reason = "deadline, freshness, body weight, live odds, lineup, and EV gates passed"
        else:
            decision = "NO_GO"
            status = "NG"
            if not checks["deadline"]:
                code = "NO_GO_DEADLINE_MISSED"
            elif not checks["tickets_present"] and not (
                set(failed) - {"tickets_present", "tickets_use_jra_live_odds"}
            ):
                code = "NO_GO_NO_VALUE_TICKETS"
            else:
                code = "NO_GO_FINAL_REVIEW_FAILED"
            reason = "failed final gates: " + ", ".join(failed)

        return {
            "status": status,
            "decision": decision,
            "decision_code": code,
            "reason": reason,
            "fix": "" if decision == "GO" else "do not purchase tickets",
            "issued_at": now.isoformat(),
            "snapshot_id": collected.get("snapshot_id", ""),
            "deadline": plan.to_dict(),
            "checks": checks,
            "odds_age_seconds": odds_age,
            "conditions_age_seconds": conditions_age,
            "extreme_selected_horse_numbers": extreme_selected,
            "quantitative_review": quantitative_review,
        }


def extract_baseline_rows(
    baseline: dict[str, object] | list[dict[str, object]],
    race_config: dict[str, object],
) -> list[dict[str, str]]:
    if isinstance(baseline, list):
        candidates = baseline
    else:
        collector = dict(baseline.get("data_collector") or {})
        candidates = list(collector.get("rows") or baseline.get("rows") or [])
    rows = [{str(key): str(value) for key, value in dict(row).items()} for row in candidates]
    if not rows:
        return []

    race_id = str(race_config.get("race_id", "")).strip()
    if race_id:
        exact = [row for row in rows if row.get("race_id") == race_id]
        if exact:
            return exact
    race_date = str(race_config.get("race_date", "")).strip()
    track = str(race_config.get("track", "")).strip()
    race_number = str(race_config.get("race_number", "")).strip()
    matched = [
        row
        for row in rows
        if str(row.get("target_race_date", "")) == race_date
        and str(row.get("target_track", "")) == track
        and _same_number(row.get("target_race_number"), race_number)
    ]
    return matched


def merge_live_entries(
    baseline_rows: list[dict[str, str]],
    live_entries: list[dict[str, object]],
    *,
    zero_history_horse_numbers: set[str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    zero_history_horse_numbers = {
        str(_to_int(value)) for value in (zero_history_horse_numbers or set())
    }
    live_by_id = {str(row.get("horse_id", "")): row for row in live_entries if row.get("horse_id")}
    live_by_number = {
        str(row.get("horse_number", "")): row for row in live_entries if row.get("horse_number")
    }
    merged: list[dict[str, str]] = []
    for baseline_row in baseline_rows:
        row = dict(baseline_row)
        horse_id = str(row.get("horse_id", ""))
        horse_number = str(row.get("horse_number", ""))
        live = live_by_id.get(horse_id) or live_by_number.get(horse_number)
        if live is None:
            continue
        for key in (
            "frame_number",
            "horse_number",
            "current_jockey",
            "assigned_weight",
            "current_body_weight",
            "body_weight_change",
            "body_weight_status",
            "current_odds",
            "current_popularity",
            "target_track",
            "target_race_date",
            "target_race_number",
            "target_surface",
            "target_distance",
            "target_weather",
            "target_track_condition",
            "target_conditions_captured_at",
            "snapshot_id",
        ):
            row[key] = str(live.get(key, ""))
        merged.append(row)

    baseline_numbers = {
        str(_to_int(row.get("horse_number")))
        for row in baseline_rows
        if str(row.get("horse_number", "")).strip()
    }
    neutral_numbers: list[str] = []
    for live in live_entries:
        horse_number = str(_to_int(live.get("horse_number")))
        if horse_number in baseline_numbers or horse_number not in zero_history_horse_numbers:
            continue
        merged.append(_neutral_history_row(live))
        neutral_numbers.append(horse_number)

    live_keys = {
        str(row.get("horse_id") or row.get("horse_number"))
        for row in live_entries
        if row.get("horse_id") or row.get("horse_number")
    }
    baseline_horses = {
        str(row.get("horse_id") or row.get("horse_number"))
        for row in baseline_rows
        if row.get("horse_id") or row.get("horse_number")
    }
    represented = {
        str(row.get("horse_id") or row.get("horse_number"))
        for row in merged
        if row.get("horse_id") or row.get("horse_number")
    }
    allowed_zero_history_keys = {
        str(row.get("horse_id") or row.get("horse_number"))
        for row in live_entries
        if str(_to_int(row.get("horse_number"))) in zero_history_horse_numbers
    }
    expected_baseline_horses = baseline_horses | allowed_zero_history_keys
    race_id = str(live_entries[0].get("race_id", "")) if live_entries else ""
    lineup = {
        "race_id": race_id,
        "matches": bool(live_keys)
        and represented == live_keys
        and expected_baseline_horses == live_keys,
        "baseline_horse_count": len(baseline_horses),
        "live_horse_count": len(live_keys),
        "merged_horse_count": len(represented),
        "missing_from_live": sorted(baseline_horses - live_keys),
        "missing_from_baseline": sorted(live_keys - expected_baseline_horses),
        "neutral_history_horse_numbers": sorted(neutral_numbers, key=int),
    }
    return merged, lineup


def _neutral_history_row(live: dict[str, object]) -> dict[str, str]:
    row = {str(key): str(value) for key, value in live.items()}
    row.update(
        {
            "run_index": "0",
            "history_count": "0",
            "neutral_history_fallback": "true",
            "history_source": "configured_zero_start",
        }
    )
    return row


def build_baseline_quality(
    baseline: dict[str, object] | list[dict[str, object]],
    rows: list[dict[str, str]],
    *,
    race_config: dict[str, object] | None = None,
) -> dict[str, object]:
    counts: dict[str, int] = {}
    horse_numbers: dict[str, str] = {}
    for row in rows:
        key = str(row.get("horse_id") or row.get("horse_number") or row.get("horse_name"))
        if key:
            counts[key] = counts.get(key, 0) + 1
            horse_numbers[key] = str(row.get("horse_number", "")).strip()

    configured_career_starts = dict(
        (race_config or {}).get("career_starts_by_horse_number") or {}
    )
    configured_career_starts = {
        str(_to_int(number)): max(0, _to_int(starts))
        for number, starts in configured_career_starts.items()
    }
    required_counts: dict[str, int] = {}
    actual_counts = dict(counts)
    for key in counts:
        horse_number = horse_numbers.get(key, "")
        required_counts[key] = (
            min(5, configured_career_starts[horse_number])
            if horse_number in configured_career_starts
            else 5
        )

    configured_keys: dict[str, str] = {}
    for horse_number, career_starts in configured_career_starts.items():
        existing_key = next(
            (key for key, number in horse_numbers.items() if number == horse_number),
            "",
        )
        key = existing_key or f"horse_number:{horse_number}"
        configured_keys[horse_number] = key
        actual_counts.setdefault(key, 0)
        required_counts[key] = min(5, career_starts)

    if isinstance(baseline, list):
        source_kind = "csv_rows"
        review_status = "NOT_APPLICABLE"
        review_ok = True
        manifest_ok = True
        parser_quality_ok = True
        high_issues = 0
    else:
        source_kind = "pipeline_run" if baseline.get("data_collector") else "rows_payload"
        review_status = str(dict(baseline.get("reviewer") or {}).get("status", ""))
        review_ok = review_status == "OK" if source_kind == "pipeline_run" else True
        manifest_ok = (
            bool(baseline.get("_baseline_manifest_valid"))
            if source_kind == "pipeline_run"
            else True
        )
        quality = dict(dict(baseline.get("data_collector") or {}).get("quality_report") or {})
        high_issues = int(dict(quality.get("issues_by_severity") or {}).get("high", 0) or 0)
        parser_quality_ok = high_issues == 0 if source_kind == "pipeline_run" else True

    return {
        "source_kind": source_kind,
        "review_status": review_status,
        "review_ok": review_ok,
        "manifest_ok": manifest_ok,
        "high_parser_issue_count": high_issues,
        "parser_quality_ok": parser_quality_ok,
        "history_counts": actual_counts,
        "required_history_counts": required_counts,
        "career_starts_by_horse_number": configured_career_starts,
        "history_complete": bool(required_counts)
        and all(actual_counts.get(key, 0) >= required for key, required in required_counts.items()),
        "minimum_history_rows": 5,
    }


def _configured_zero_history_numbers(race_config: dict[str, object]) -> set[str]:
    configured = dict(race_config.get("career_starts_by_horse_number") or {})
    return {
        str(_to_int(number))
        for number, starts in configured.items()
        if _to_int(starts) == 0
    }


def live_odds_snapshots(live: dict[str, object]) -> list[dict[str, object]]:
    captured_at = str(live.get("official_odds_as_of") or live.get("completed_at") or "")
    return [
        {
            "race_id": row.get("race_id", ""),
            "horse_id": row.get("horse_id", ""),
            "horse_name": row.get("horse_name", ""),
            "horse_number": row.get("horse_number", ""),
            "current_odds": row.get("current_odds", ""),
            "current_popularity": row.get("current_popularity", ""),
            "captured_at": captured_at,
            "snapshot_id": live.get("snapshot_id", ""),
            "snapshot_complete": live.get("snapshot_complete", False),
        }
        for row in list(live.get("entries") or [])
    ]


def invalidate_ticket_plan(ticket_plan: dict[str, object], reason: str) -> dict[str, object]:
    out = dict(ticket_plan)
    invalidated = [dict(ticket) for ticket in list(ticket_plan.get("tickets") or [])]
    out["invalidated_tickets"] = invalidated
    out["tickets"] = []
    out["ticket_status"] = "invalidated_by_final_reviewer"
    out["invalidation_reason"] = reason
    return out


def _ticket_horse_numbers(
    tickets: list[dict[str, object]],
    *,
    entries: list[dict[str, object]] | None = None,
) -> set[str]:
    numbers: set[str] = set()
    for ticket in tickets:
        value = ticket.get("horse_number")
        if value not in (None, "") and str(value).replace(".", "", 1).isdigit():
            numbers.add(str(_to_int(value)))
        for value in list(ticket.get("horse_numbers") or []):
            if str(value).strip():
                numbers.add(str(_to_int(value)))
        for leg in list(ticket.get("legs") or []):
            leg = dict(leg)
            value = leg.get("horse_number")
            if value not in (None, ""):
                numbers.add(str(_to_int(value)))
            for horse in list(leg.get("horses") or []):
                value = dict(horse).get("horse_number")
                if value not in (None, ""):
                    numbers.add(str(_to_int(value)))

        if str(ticket.get("bet_type", "")) == "wakuren":
            frames = {str(value) for value in list(ticket.get("frame_numbers") or [])}
            for entry in entries or []:
                if str(entry.get("frame_number", "")) in frames:
                    numbers.add(str(_to_int(entry.get("horse_number"))))
    return numbers


def _tickets_have_exact_live_odds(
    tickets: list[dict[str, object]],
    combo_odds: list[dict[str, object]],
) -> bool:
    if not tickets:
        return False
    live_keys = {
        (str(row.get("bet_type", "")), str(row.get("combination", "")))
        for row in combo_odds
        if str(row.get("bet_type", "")) and str(row.get("combination", ""))
    }
    for ticket in tickets:
        bet_type = str(ticket.get("bet_type", ""))
        if str(ticket.get("odds_source", "")) != "jra_live":
            return False
        points = [dict(point) for point in list(ticket.get("points") or [])]
        if points:
            for point in points:
                numbers = [str(value) for value in list(point.get("horse_numbers") or [])]
                key = live_combo_key(bet_type, numbers)
                if str(point.get("odds_source", "")) != "jra_live" or (bet_type, key) not in live_keys:
                    return False
            continue

        if bet_type == "wakuren":
            numbers = [str(value) for value in list(ticket.get("frame_numbers") or [])]
        else:
            numbers = [str(value) for value in list(ticket.get("horse_numbers") or [])]
            if not numbers and ticket.get("horse_number") not in (None, ""):
                numbers = [str(ticket.get("horse_number"))]
        key = live_combo_key(bet_type, numbers)
        if not key or (bet_type, key) not in live_keys:
            return False
    return True


def _age_seconds(now: datetime, value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    age = (_as_utc(now) - _as_utc(parsed)).total_seconds()
    return round(age, 3)


def _same_number(left: object, right: object) -> bool:
    try:
        return int(float(str(left))) == int(float(str(right)))
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _is_true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "complete"}


def _to_int(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
