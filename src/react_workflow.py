from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from itertools import combinations, product
from pathlib import Path

from analysis.ev import EVWeights, build_feature_rows, compute_ev, simulate_race_scenarios
from jra_scraper.config import ScrapeConfig
from jra_scraper.pipeline import JRAPipeline
from report.note import build_note_article
from strategy.betting import MIN_ACTIONABLE_WIN_EV, generate_tickets
from strategy.live_odds import build_live_odds_lookup, live_odds_value, lookup_live_odds
from strategy.portfolio import (
    portfolio_ev,
    portfolio_expected_return,
    portfolio_no_gami,
    portfolio_summary,
    portfolio_total_stake,
    portfolio_total_points,
    ticket_return_if_hit,
    ticket_stake_unit,
    with_adjusted_stake,
)
from strategy.win5 import generate_win5_plan, is_win5_mode


CANONICAL_STAGE_SCHEMA_VERSION = "1"
CANONICAL_STAGE_PRODUCER = "src.react_workflow.ReactiveRaceWorkflow"
CANONICAL_STAGE_ORDER = (
    "01_data_collector.json",
    "02_analyzer.json",
    "03_simulator.json",
    "04_ev_calculator.json",
    "05_bet_builder.json",
    "06_reviewer.json",
    "07_article_writer.json",
    "run_summary.json",
)


@dataclass
class WorkflowSettings:
    max_repair_attempts: int = 1
    bankroll_per_race: int = 1000
    min_ev: float = 1.03
    min_place_ev: float = 1.01
    min_wide_ev: float = 1.01
    min_wakuren_ev: float = 1.03
    min_umaren_ev: float = 1.04
    min_umatan_ev: float = 1.07
    min_sanrenpuku_ev: float = 1.06
    min_sanrentan_ev: float = 1.12
    max_tickets_per_race: int = 5
    max_wide_tickets_per_race: int = 2
    max_exotic_tickets_per_race: int = 4
    min_portfolio_ev: float = 1.0
    min_coverage_ev: float = 0.75
    mode: str = "balanced"
    win5_max_points: int | None = None
    win5_stake_yen_per_point: int = 100
    prefer_wide: bool = True
    max_ev_delta_abs: float = 0.20
    max_ev_delta_ratio: float = 0.18
    max_odds_gap_ratio: float = 0.25
    min_top3_ticket_coverage: int = 2
    max_horse_ticket_dependency_ratio: float = 0.50


class DataCollectorAgent:
    collector_key: str = "domestic"
    default_aggressive_repair: bool = False

    def __init__(self, config: ScrapeConfig) -> None:
        self.config = config

    def run(
        self,
        race_configs: list[dict[str, object]],
        *,
        force_rebuild: bool = False,
        race_limit: int | None = None,
        horse_limit: int | None = None,
        aggressive_repair: bool = False,
        reprocess_raw: bool = False,
    ) -> dict[str, object]:
        effective_aggressive_repair = aggressive_repair or self.default_aggressive_repair
        pipeline = JRAPipeline(self.config)
        try:
            rows = pipeline.run(
                race_specs=race_configs,
                force_rebuild=force_rebuild,
                race_limit=race_limit,
                horse_limit=horse_limit,
                aggressive_repair=effective_aggressive_repair,
                reprocess_raw=reprocess_raw,
            )
        finally:
            pipeline.close()

        return {
            "collector_key": self.collector_key,
            "aggressive_repair": effective_aggressive_repair,
            "race_configs": race_configs,
            "rows": rows,
            "entries": _read_csv(self.config.entries_csv),
            "odds_snapshots": _read_csv(self.config.odds_snapshots_csv),
            "combo_odds": _read_csv(self.config.combo_odds_csv),
            "quality_report": _read_json(self.config.quality_report_path),
        }


class DomesticDataCollectorAgent(DataCollectorAgent):
    collector_key = "domestic"
    default_aggressive_repair = False


class OverseasDataCollectorAgent(DataCollectorAgent):
    collector_key = "overseas"
    default_aggressive_repair = True


class AnalyzerAgent:
    def run(
        self,
        rows: list[dict[str, str]],
        *,
        odds_snapshots: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return {"feature_rows": build_feature_rows(rows, odds_snapshots=odds_snapshots)}


class SimulatorAgent:
    def run(self, feature_rows: list[dict[str, object]]) -> dict[str, object]:
        return {"scenario_rows": simulate_race_scenarios(feature_rows)}


class EVCalculatorAgent:
    def __init__(self, weights: EVWeights | None = None) -> None:
        self.weights = weights or EVWeights()

    def run(self, scenario_rows: list[dict[str, object]]) -> dict[str, object]:
        return {"ev_rows": compute_ev(scenario_rows, weights=self.weights)}


class BetBuilderAgent:
    def __init__(self, settings: WorkflowSettings) -> None:
        self.settings = settings

    def run(
        self,
        ev_rows: list[dict[str, object]],
        *,
        combo_odds: list[dict[str, object]] | list[dict[str, str]] | None = None,
        race_configs: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        if is_win5_mode(self.settings.mode):
            return generate_win5_plan(
                ev_rows,
                mode=self.settings.mode,
                max_points=self.settings.win5_max_points,
                stake_yen_per_point=self.settings.win5_stake_yen_per_point,
                race_order=_race_order_from_configs(race_configs or []),
            )

        return generate_tickets(
            ev_rows,
            odds_rows=list(combo_odds or []),
            mode=self.settings.mode,
            bankroll_per_race=self.settings.bankroll_per_race,
            min_ev=self.settings.min_ev,
            min_place_ev=self.settings.min_place_ev,
            min_wide_ev=self.settings.min_wide_ev,
            min_wakuren_ev=self.settings.min_wakuren_ev,
            min_umaren_ev=self.settings.min_umaren_ev,
            min_umatan_ev=self.settings.min_umatan_ev,
            min_sanrenpuku_ev=self.settings.min_sanrenpuku_ev,
            min_sanrentan_ev=self.settings.min_sanrentan_ev,
            max_tickets_per_race=self.settings.max_tickets_per_race,
            max_wide_tickets_per_race=self.settings.max_wide_tickets_per_race,
            max_exotic_tickets_per_race=self.settings.max_exotic_tickets_per_race,
            min_portfolio_ev=self.settings.min_portfolio_ev,
            min_coverage_ev=self.settings.min_coverage_ev,
            prefer_wide=self.settings.prefer_wide,
        )


class ReviewerAgent:
    def __init__(self, settings: WorkflowSettings) -> None:
        self.settings = settings

    def run(
        self,
        collected: dict[str, object],
        scenario_rows: list[dict[str, object]],
        ev_rows: list[dict[str, object]],
        ticket_plan: dict[str, object],
        *,
        attempt: int,
    ) -> dict[str, object]:
        quality_report = dict(collected.get("quality_report") or {})
        entry_rows = list(collected.get("entries") or [])
        tickets = list(ticket_plan.get("tickets") or [])
        repair_enabled = bool(ticket_plan.get("reviewer_ticket_repair_enabled"))
        repaired_plan = bool(ticket_plan.get("reviewer_ticket_repair_applied"))

        reasons: list[str] = []
        repair_actions: list[object] = []
        ticket_repair_blocked = False

        high_issues = int(dict(quality_report.get("issues_by_severity") or {}).get("high", 0))
        if high_issues > 0:
            reasons.append(f"high severity parser issues: {high_issues}")
            ticket_repair_blocked = True
            if attempt < self.settings.max_repair_attempts:
                repair_actions.append("retry_aggressive_parse")

        missing_odds = int(quality_report.get("missing_current_odds_entries", 0) or 0)
        if entry_rows and missing_odds == len(entry_rows):
            reasons.append("current odds are missing for every entry")
            ticket_repair_blocked = True
            if attempt < self.settings.max_repair_attempts:
                repair_actions.append("retry_aggressive_parse")

        prob_sums = _probability_sums(ev_rows)
        bad_prob_races = [race_id for race_id, total in prob_sums.items() if abs(total - 1.0) > 0.025]
        if bad_prob_races:
            reasons.append(f"probability normalization drift detected: {bad_prob_races}")
            ticket_repair_blocked = True

        if str(ticket_plan.get("bet_type", "")) == "win5":
            ticket_repair_blocked = True
            win5_points = int(_to_float(ticket_plan.get("points"), 0.0))
            if win5_points <= 0:
                reasons.append("WIN5 formation has no valid points")
            if self.settings.win5_max_points is not None and win5_points > self.settings.win5_max_points:
                reasons.append("WIN5 formation exceeds max point constraint")
            if len(list(ticket_plan.get("legs") or [])) != 5:
                reasons.append("WIN5 formation must contain exactly five legs")
            configured_order = _race_order_from_configs(list(collected.get("race_configs") or []))
            actual_order = [str(race_id) for race_id in list(ticket_plan.get("race_order") or [])]
            if configured_order and actual_order[: len(configured_order)] != configured_order:
                reasons.append("WIN5 race order does not match config order")
        else:
            risky_tickets = [
                ticket
                for ticket in tickets
                if _ticket_ev(ticket, default=0.0) < _ticket_min_ev(ticket, self.settings)
                or _ticket_hit_prob(ticket) < _ticket_min_prob(ticket)
            ]
            if risky_tickets:
                reasons.append("ticket plan contains low-confidence or sub-threshold tickets")

            top_rows = sorted(
                ev_rows,
                key=lambda row: _to_float(row.get("win_prob"), 0.0),
                reverse=True,
            )[:3]
            ticket_horses = _ticket_horse_numbers(tickets)
            covered_top = [
                str(row.get("horse_number", ""))
                for row in top_rows
                if str(row.get("horse_number", "")) in ticket_horses
            ]
            top1_missing = bool(
                tickets
                and top_rows
                and str(top_rows[0].get("horse_number", "")) not in ticket_horses
            )
            if top1_missing and not repaired_plan:
                reasons.append("top win-probability horse is missing from every ticket")
            required_top_coverage = min(self.settings.min_top3_ticket_coverage, len(top_rows))
            if tickets and len(covered_top) < required_top_coverage:
                reasons.append(
                    f"top-3 ticket coverage is too low: {len(covered_top)}/{required_top_coverage}"
                )
            dependency_ratio = _max_horse_ticket_dependency_ratio(tickets)
            if tickets and dependency_ratio > self.settings.max_horse_ticket_dependency_ratio:
                reasons.append(
                    f"horse ticket dependency ratio is too high: {dependency_ratio:.3f}"
                )

            if tickets and portfolio_ev(tickets) < self.settings.min_portfolio_ev:
                reasons.append("ticket portfolio EV is below the configured minimum")
            if tickets and not portfolio_no_gami(tickets):
                reasons.append("ticket portfolio contains loss-on-hit tickets")

        if str(ticket_plan.get("bet_type", "")) != "win5":
            longshot_overweight = [
                ticket
                for ticket in tickets
                if _ticket_odds(ticket) >= _longshot_odds_threshold(ticket)
                and int(_to_float(ticket.get("stake"), 0.0)) > _longshot_stake_threshold(ticket)
            ]
            if longshot_overweight:
                reasons.append("ticket plan overweights extreme longshots")

        divergent_rows = _find_divergent_rows(
            ev_rows,
            max_ev_delta_abs=self.settings.max_ev_delta_abs,
            max_ev_delta_ratio=self.settings.max_ev_delta_ratio,
            max_odds_gap_ratio=self.settings.max_odds_gap_ratio,
        )
        selected_divergent_rows = _selected_divergent_rows(tickets, divergent_rows)
        actionable_divergent_rows = selected_divergent_rows if repaired_plan else divergent_rows
        if actionable_divergent_rows and str(ticket_plan.get("bet_type", "")) != "win5":
            reasons.append(
                "predicted/current EV divergence detected: "
                + ", ".join(
                    f"{row['horse_name']}@{row['race_id']}"
                    for row in actionable_divergent_rows[:3]
                )
            )

        missing_eligible_win_candidates = _find_missing_eligible_win_candidates(
            collected,
            ev_rows,
            ticket_plan,
            minimum_win_ev=max(self.settings.min_ev, MIN_ACTIONABLE_WIN_EV),
        )
        if missing_eligible_win_candidates and str(ticket_plan.get("bet_type", "")) != "win5":
            reasons.append(
                "eligible official-live win candidates are missing from candidate universe: "
                + ", ".join(
                    f"{row['horse_name']}@{row['race_id']}"
                    for row in missing_eligible_win_candidates[:3]
                )
            )

        if repair_enabled and tickets and reasons and not ticket_repair_blocked:
            ticket_repair = _build_ticket_repair_action(
                tickets,
                ev_rows=ev_rows,
                divergent_rows=divergent_rows,
                settings=self.settings,
            )
            if ticket_repair:
                repair_actions.append(ticket_repair)

        status = "OK" if not reasons else "NG"
        return {
            "status": status,
            "reason": "; ".join(reasons) if reasons else "quality gates passed",
            "fix": "; ".join(_repair_action_name(action) for action in repair_actions),
            "repair_actions": repair_actions,
            "probability_sums": {race_id: _fmt(total) for race_id, total in prob_sums.items()},
            "divergent_rows": divergent_rows,
            "selected_divergent_rows": selected_divergent_rows,
            "missing_eligible_win_candidates": missing_eligible_win_candidates,
            "stage_counts": {
                "entries": len(entry_rows),
                "feature_rows": len(scenario_rows),
                "ev_rows": len(ev_rows),
                "tickets": len(tickets),
            },
        }


class ArticleWriterAgent:
    def run(
        self,
        race_configs: list[dict[str, object]],
        *,
        ev_rows: list[dict[str, object]],
        ticket_plan: dict[str, object],
        review: dict[str, object],
        quality_report: dict[str, object],
        odds_snapshots: list[dict[str, object]] | list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        primary_race = dict(race_configs[0] if race_configs else {})
        race_name = str(primary_race.get("race_name") or "JRAレース")
        prediction_context = {
            "odds_captured_at_latest": _latest_snapshot_timestamp(list(odds_snapshots or [])),
        }
        return build_note_article(
            race_name,
            ev_rows,
            ticket_plan,
            review=review,
            quality_report=quality_report,
            race_config=primary_race,
            prediction_context=prediction_context,
        )


class ReactiveRaceWorkflow:
    OVERSEAS_URL_MARKERS = ("/JRADB/accessSD.html", "/JRADB/accessSO.html", "/JRADB/accessSK.html")
    OVERSEAS_TRACK_MARKERS = (
        "シャティン",
        "香港",
        "HK",
        "メイダン",
        "ドバイ",
        "ロンシャン",
        "アスコット",
        "チャーチル",
        "海外",
    )

    def __init__(
        self,
        config: ScrapeConfig | None = None,
        *,
        settings: WorkflowSettings | None = None,
        weights: EVWeights | None = None,
    ) -> None:
        self.config = config or ScrapeConfig()
        self.config.ensure_dirs()
        self.settings = settings or WorkflowSettings()
        self.domestic_collector = DomesticDataCollectorAgent(self.config)
        self.overseas_collector = OverseasDataCollectorAgent(self.config)
        self.analyzer = AnalyzerAgent()
        self.simulator = SimulatorAgent()
        self.ev_calculator = EVCalculatorAgent(weights=weights)
        self.bet_builder = BetBuilderAgent(self.settings)
        self.reviewer = ReviewerAgent(self.settings)
        self.article_writer = ArticleWriterAgent()

    def run(
        self,
        race_configs: list[dict[str, object]],
        *,
        force_rebuild: bool = False,
        race_limit: int | None = None,
        horse_limit: int | None = None,
        reprocess_raw: bool = False,
    ) -> dict[str, object]:
        final_payload: dict[str, object] = {}
        collector = self._select_collector(race_configs)

        for attempt in range(self.settings.max_repair_attempts + 1):
            aggressive_repair = attempt > 0
            collected = collector.run(
                race_configs,
                force_rebuild=force_rebuild or aggressive_repair,
                race_limit=race_limit,
                horse_limit=horse_limit,
                aggressive_repair=aggressive_repair,
                reprocess_raw=reprocess_raw,
            )
            analyzed = self.analyzer.run(
                list(collected.get("rows") or []),
                odds_snapshots=list(collected.get("odds_snapshots") or []),
            )
            simulated = self.simulator.run(list(analyzed.get("feature_rows") or []))
            calculated = self.ev_calculator.run(list(simulated.get("scenario_rows") or []))
            bet_plan = self.bet_builder.run(
                list(calculated.get("ev_rows") or []),
                combo_odds=list(collected.get("combo_odds") or []),
                race_configs=race_configs,
            )
            review = self.reviewer.run(
                collected,
                list(simulated.get("scenario_rows") or []),
                list(calculated.get("ev_rows") or []),
                dict(bet_plan),
                attempt=attempt,
            )
            if review.get("status") != "OK":
                bet_plan = _downgrade_ticket_plan_for_review(dict(bet_plan), review)
            article = self.article_writer.run(
                race_configs,
                ev_rows=list(calculated.get("ev_rows") or []),
                ticket_plan=dict(bet_plan),
                review=review,
                quality_report=dict(collected.get("quality_report") or {}),
                odds_snapshots=list(collected.get("odds_snapshots") or []),
            )

            final_payload = {
                "data_collector": collected,
                "analyzer": analyzed,
                "simulator": simulated,
                "ev_calculator": calculated,
                "bet_builder": bet_plan,
                "reviewer": review,
                "article_writer": article,
                "attempt": attempt,
            }
            self._write_stage_outputs(final_payload)

            if review.get("status") == "OK" or not review.get("repair_actions"):
                break

        return final_payload

    @classmethod
    def resolve_collector_key(cls, race_configs: list[dict[str, object]]) -> str:
        if not race_configs:
            return "domestic"

        explicit_modes = {
            str(cfg.get("collector_mode", "")).strip().lower()
            for cfg in race_configs
            if str(cfg.get("collector_mode", "")).strip()
        }
        if "overseas" in explicit_modes:
            return "overseas"
        if "domestic" in explicit_modes:
            return "domestic"

        for cfg in race_configs:
            source_url = str(cfg.get("source_url", "")).strip()
            if any(marker in source_url for marker in cls.OVERSEAS_URL_MARKERS):
                return "overseas"

            track = str(cfg.get("track", "")).strip()
            if any(marker in track for marker in cls.OVERSEAS_TRACK_MARKERS):
                return "overseas"

            race_name = str(cfg.get("race_name", "")).strip()
            if any(marker in race_name for marker in cls.OVERSEAS_TRACK_MARKERS):
                return "overseas"

        return "domestic"

    def _select_collector(self, race_configs: list[dict[str, object]]) -> DataCollectorAgent:
        key = self.resolve_collector_key(race_configs)
        if key == "overseas":
            return self.overseas_collector
        return self.domestic_collector

    def _write_stage_outputs(self, payload: dict[str, object]) -> None:
        stage_map = {
            "01_data_collector.json": payload.get("data_collector"),
            "02_analyzer.json": payload.get("analyzer"),
            "03_simulator.json": payload.get("simulator"),
            "04_ev_calculator.json": payload.get("ev_calculator"),
            "05_bet_builder.json": payload.get("bet_builder"),
            "06_reviewer.json": payload.get("reviewer"),
            "07_article_writer.json": payload.get("article_writer"),
            "run_summary.json": {
                "attempt": payload.get("attempt"),
                "review_status": dict(payload.get("reviewer") or {}).get("status"),
                "article_status": dict(payload.get("article_writer") or {}).get("status"),
            },
        }
        for filename, body in stage_map.items():
            path = self.config.stages_dir / filename
            path.write_text(json.dumps(body or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {
            "schema_version": CANONICAL_STAGE_SCHEMA_VERSION,
            "producer": CANONICAL_STAGE_PRODUCER,
            "stage_order": list(CANONICAL_STAGE_ORDER),
            "attempt": payload.get("attempt"),
            "artifacts": {
                filename: {"sha256": _file_sha256(self.config.stages_dir / filename)}
                for filename in CANONICAL_STAGE_ORDER
            },
        }
        manifest_path = self.config.stages_dir / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        assert_canonical_stage_manifest(self.config.stages_dir)


def validate_canonical_stage_manifest(stages_dir: Path) -> list[str]:
    manifest_path = stages_dir / "run_manifest.json"
    if not manifest_path.exists():
        return ["run_manifest.json is missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"run_manifest.json is invalid: {exc}"]

    errors: list[str] = []
    if manifest.get("schema_version") != CANONICAL_STAGE_SCHEMA_VERSION:
        errors.append("stage schema version does not match the canonical workflow")
    if manifest.get("producer") != CANONICAL_STAGE_PRODUCER:
        errors.append("stage producer is not the canonical workflow")
    if manifest.get("stage_order") != list(CANONICAL_STAGE_ORDER):
        errors.append("stage order does not match the repository workflow")

    artifacts = dict(manifest.get("artifacts") or {})
    for filename in CANONICAL_STAGE_ORDER:
        path = stages_dir / filename
        if not path.exists():
            errors.append(f"{filename} is missing")
            continue
        expected = str(dict(artifacts.get(filename) or {}).get("sha256", ""))
        actual = _file_sha256(path)
        if not expected or expected != actual:
            errors.append(f"{filename} digest does not match run_manifest.json")
    return errors


def assert_canonical_stage_manifest(stages_dir: Path) -> None:
    errors = validate_canonical_stage_manifest(stages_dir)
    if errors:
        raise ValueError("invalid canonical stage artifacts: " + "; ".join(errors))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _probability_sums(ev_rows: list[dict[str, object]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in ev_rows:
        race_id = str(row.get("race_id", ""))
        totals[race_id] = totals.get(race_id, 0.0) + _to_float(row.get("win_prob"))
    return totals


def apply_ticket_repair_actions(
    ticket_plan: dict[str, object],
    repair_actions: list[object],
) -> dict[str, object]:
    action = next(
        (
            dict(value)
            for value in repair_actions
            if isinstance(value, dict) and value.get("action") == "filter_and_reallocate_tickets"
        ),
        None,
    )
    if action is None:
        return ticket_plan

    kept_stakes = {
        str(row.get("ticket_key", "")): int(_to_float(row.get("stake")))
        for row in list(action.get("kept_tickets") or [])
        if str(row.get("ticket_key", ""))
    }
    original_tickets = [dict(ticket) for ticket in list(ticket_plan.get("tickets") or [])]
    repaired = [
        with_adjusted_stake(ticket, kept_stakes[_ticket_repair_key(ticket)])
        for ticket in original_tickets
        if _ticket_repair_key(ticket) in kept_stakes
    ]
    repaired = _annotate_repaired_portfolio(repaired)
    removed = [
        ticket
        for ticket in original_tickets
        if _ticket_repair_key(ticket) not in kept_stakes
    ]

    out = dict(ticket_plan)
    out["tickets"] = repaired
    out["invalidated_tickets"] = removed
    out["portfolio_summary"] = portfolio_summary(repaired)
    out["ticket_status"] = "repaired_by_reviewer"
    out["reviewer_ticket_repair_enabled"] = False
    out["reviewer_ticket_repair_applied"] = True
    out["repair_action"] = action
    out["unused_bankroll"] = int(action.get("unused_bankroll", 0) or 0)
    out["primary_bet_type"] = str(repaired[0].get("bet_type", "")) if repaired else ""

    label_fields = {
        "win": "tansho",
        "place": "fukusho",
        "wide": "wide",
        "wakuren": "wakuren",
        "umaren": "umaren",
        "umatan": "umatan",
        "sanrenpuku": "sanrenpuku",
        "sanrentan": "sanrentan",
    }
    for bet_type, field in label_fields.items():
        out[field] = [
            str(ticket.get("horse_name", ""))
            for ticket in repaired
            if str(ticket.get("bet_type", "")) == bet_type
        ]

    repaired_races: list[dict[str, object]] = []
    for race in list(ticket_plan.get("races") or []):
        race_out = dict(race)
        race_original = [dict(ticket) for ticket in list(race_out.get("tickets") or [])]
        race_tickets = [
            with_adjusted_stake(ticket, kept_stakes[_ticket_repair_key(ticket)])
            for ticket in race_original
            if _ticket_repair_key(ticket) in kept_stakes
        ]
        race_tickets = _annotate_repaired_portfolio(race_tickets)
        race_out["tickets"] = race_tickets
        race_out["invalidated_tickets"] = [
            ticket
            for ticket in race_original
            if _ticket_repair_key(ticket) not in kept_stakes
        ]
        race_out["portfolio"] = portfolio_summary(race_tickets)
        race_out["ticket_status"] = "repaired_by_reviewer"
        repaired_races.append(race_out)
    out["races"] = repaired_races
    return out


def _build_ticket_repair_action(
    tickets: list[dict[str, object]],
    *,
    ev_rows: list[dict[str, object]],
    divergent_rows: list[dict[str, str]],
    settings: WorkflowSettings,
) -> dict[str, object] | None:
    mandatory_removed = {
        _ticket_repair_key(ticket)
        for ticket in tickets
        if _ticket_ev(ticket, default=0.0) < _ticket_min_ev(ticket, settings)
        or _ticket_hit_prob(ticket) < _ticket_min_prob(ticket)
        or (
            _ticket_odds(ticket) >= _longshot_odds_threshold(ticket)
            and int(_to_float(ticket.get("stake"))) > _longshot_stake_threshold(ticket)
        )
        or _ticket_matches_divergent_row(ticket, divergent_rows)
    }
    eligible = [
        dict(ticket)
        for ticket in tickets
        if _ticket_repair_key(ticket) not in mandatory_removed
    ]
    if not eligible:
        return None

    best: tuple[tuple[float, float, int, int], list[dict[str, object]]] | None = None
    for size in range(1, len(eligible) + 1):
        for subset_values in combinations(eligible, size):
            subset = [dict(ticket) for ticket in subset_values]
            stake_options = [
                range(
                    ticket_stake_unit(ticket),
                    _review_max_ticket_stake(ticket, settings.bankroll_per_race)
                    + ticket_stake_unit(ticket),
                    ticket_stake_unit(ticket),
                )
                for ticket in subset
            ]
            for stakes in product(*stake_options):
                allocated = [
                    with_adjusted_stake(ticket, stake)
                    for ticket, stake in zip(subset, stakes)
                ]
                if portfolio_total_stake(allocated) > settings.bankroll_per_race:
                    continue
                if not _repair_portfolio_safe(allocated, ev_rows=ev_rows, settings=settings):
                    continue
                expected_profit = portfolio_expected_return(allocated) - portfolio_total_stake(allocated)
                score = (
                    expected_profit,
                    portfolio_ev(allocated),
                    portfolio_total_stake(allocated),
                    len(allocated),
                )
                if best is None or score > best[0]:
                    best = (score, allocated)

    if best is None:
        return None

    repaired = best[1]
    kept_keys = {_ticket_repair_key(ticket) for ticket in repaired}
    if kept_keys == {_ticket_repair_key(ticket) for ticket in tickets} and all(
        int(_to_float(original.get("stake"))) == int(_to_float(replacement.get("stake")))
        for original in tickets
        for replacement in repaired
        if _ticket_repair_key(original) == _ticket_repair_key(replacement)
    ):
        return None

    total_stake = portfolio_total_stake(repaired)
    return {
        "action": "filter_and_reallocate_tickets",
        "kept_tickets": [
            {
                "ticket_key": _ticket_repair_key(ticket),
                "stake": int(_to_float(ticket.get("stake"))),
            }
            for ticket in repaired
        ],
        "removed_ticket_keys": [
            _ticket_repair_key(ticket)
            for ticket in tickets
            if _ticket_repair_key(ticket) not in kept_keys
        ],
        "mandatory_removed_ticket_keys": sorted(mandatory_removed),
        "pre_repair_ticket_count": len(tickets),
        "post_repair_ticket_count": len(repaired),
        "post_repair_total_stake": total_stake,
        "unused_bankroll": max(0, settings.bankroll_per_race - total_stake),
        "post_repair_portfolio_ev": _fmt(portfolio_ev(repaired)),
        "post_repair_no_gami": portfolio_no_gami(repaired),
        "post_repair_dependency_ratio": _fmt(_max_horse_ticket_dependency_ratio(repaired)),
    }


def _repair_portfolio_safe(
    tickets: list[dict[str, object]],
    *,
    ev_rows: list[dict[str, object]],
    settings: WorkflowSettings,
) -> bool:
    if not tickets or portfolio_total_stake(tickets) <= 0:
        return False
    if portfolio_ev(tickets) < settings.min_portfolio_ev or not portfolio_no_gami(tickets):
        return False
    if _max_horse_ticket_dependency_ratio(tickets) > settings.max_horse_ticket_dependency_ratio:
        return False
    if any(
        _ticket_ev(ticket, default=0.0) < _ticket_min_ev(ticket, settings)
        or _ticket_hit_prob(ticket) < _ticket_min_prob(ticket)
        or (
            _ticket_odds(ticket) >= _longshot_odds_threshold(ticket)
            and int(_to_float(ticket.get("stake"))) > _longshot_stake_threshold(ticket)
        )
        for ticket in tickets
    ):
        return False

    top_rows = sorted(
        ev_rows,
        key=lambda row: _to_float(row.get("win_prob")),
        reverse=True,
    )[:3]
    ticket_horses = _ticket_horse_numbers(tickets)
    covered_top = sum(
        1
        for row in top_rows
        if str(row.get("horse_number", "")) in ticket_horses
    )
    return covered_top >= min(settings.min_top3_ticket_coverage, len(top_rows))


def _review_max_ticket_stake(ticket: dict[str, object], bankroll: int) -> int:
    bet_type = str(ticket.get("bet_type", ""))
    if str(ticket.get("ticket_shape", "")) == "formation":
        share = 0.70
    elif bet_type in {"place", "wide"}:
        share = 0.45
    elif bet_type == "win":
        share = 0.35
    elif bet_type in {"wakuren", "umaren"}:
        share = 0.30
    elif bet_type in {"umatan", "sanrenpuku"}:
        share = 0.24
    else:
        share = 0.20
    unit = ticket_stake_unit(ticket)
    return max(unit, int((bankroll * share) / unit) * unit)


def _annotate_repaired_portfolio(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    summary = portfolio_summary(tickets)
    total_stake = int(summary["total_stake"])
    total_points = portfolio_total_points(tickets)
    no_gami = bool(summary["no_gami"])
    annotated: list[dict[str, object]] = []
    for ticket in tickets:
        out = dict(ticket)
        return_if_hit = ticket_return_if_hit(out)
        out.update(
            {
                "portfolio_total_stake": total_stake,
                "portfolio_total_points": total_points,
                "portfolio_ev": summary["portfolio_ev"],
                "portfolio_expected_return": summary["expected_return"],
                "portfolio_expected_profit": summary["expected_profit"],
                "portfolio_no_gami": no_gami,
                "return_if_hit": return_if_hit,
                "net_return_if_hit": return_if_hit - total_stake,
            }
        )
        annotated.append(out)
    return annotated


def _selected_divergent_rows(
    tickets: list[dict[str, object]],
    divergent_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    return [
        row
        for row in divergent_rows
        if any(_ticket_matches_divergent_row(ticket, [row]) for ticket in tickets)
    ]


def _ticket_matches_divergent_row(
    ticket: dict[str, object],
    divergent_rows: list[dict[str, str]],
) -> bool:
    ticket_ids = {str(ticket.get("horse_id", "")).strip()}
    ticket_ids.update(str(value).strip() for value in list(ticket.get("horse_ids") or []))
    ticket_numbers = _ticket_horse_numbers([ticket])
    return any(
        (
            str(row.get("horse_id", "")).strip()
            and str(row.get("horse_id", "")).strip() in ticket_ids
        )
        or (
            str(row.get("horse_number", "")).strip()
            and str(row.get("horse_number", "")).strip() in ticket_numbers
        )
        for row in divergent_rows
    )


def _ticket_repair_key(ticket: dict[str, object]) -> str:
    race_id = str(ticket.get("race_id", "")).strip()
    bet_type = str(ticket.get("bet_type", "")).strip()
    values = [str(value).strip() for value in list(ticket.get("horse_numbers") or [])]
    if not values:
        values = [str(value).strip() for value in list(ticket.get("frame_numbers") or [])]
    if not values and ticket.get("horse_number") not in (None, ""):
        values = [str(ticket.get("horse_number", "")).strip()]
    return f"{race_id}|{bet_type}|{'-'.join(values)}"


def _repair_action_name(action: object) -> str:
    if isinstance(action, dict):
        return str(action.get("action", "repair"))
    return str(action)


def _find_missing_eligible_win_candidates(
    collected: dict[str, object],
    ev_rows: list[dict[str, object]],
    ticket_plan: dict[str, object],
    *,
    minimum_win_ev: float,
) -> list[dict[str, str]]:
    """Audit live win-EV rows against the bet builder's explicit candidate universe.

    Legacy plans did not expose candidate metadata. Those plans are intentionally
    skipped because an empty or absent legacy ticket list is not evidence that the
    builder evaluated every horse.
    """
    candidate_tokens_by_race: dict[str, set[str]] = {}
    metadata_races: set[str] = set()
    plan_races = [dict(race) for race in list(ticket_plan.get("races") or [])]

    for race in plan_races:
        race_id = str(race.get("race_id", "")).strip()
        if not race_id or "candidates" not in race:
            continue
        metadata_races.add(race_id)
        candidate_tokens_by_race.setdefault(race_id, set()).update(
            _win_candidate_tokens(list(race.get("candidates") or []), race_id=race_id)
        )
        if "candidate_evaluations" in race:
            candidate_tokens_by_race[race_id].update(
                _win_candidate_tokens(
                    list(race.get("candidate_evaluations") or []),
                    race_id=race_id,
                )
            )

    if "candidate_evaluations" in ticket_plan:
        default_race_id = str(ticket_plan.get("race_id", "")).strip()
        if not default_race_id and len(plan_races) == 1:
            default_race_id = str(plan_races[0].get("race_id", "")).strip()
        for evaluation in list(ticket_plan.get("candidate_evaluations") or []):
            candidate = dict(evaluation)
            race_id = str(candidate.get("race_id", "")).strip() or default_race_id
            if not race_id:
                continue
            metadata_races.add(race_id)
            candidate_tokens_by_race.setdefault(race_id, set()).update(
                _win_candidate_tokens([candidate], race_id=race_id)
            )

    if not metadata_races:
        return []

    live_odds_by_race = build_live_odds_lookup(list(collected.get("combo_odds") or []))
    if not live_odds_by_race:
        return []

    missing: list[dict[str, str]] = []
    for row in ev_rows:
        race_id = str(row.get("race_id", "")).strip()
        horse_number = _horse_number_token(row.get("horse_number"))
        if race_id not in metadata_races or not horse_number:
            continue
        live_row = lookup_live_odds(
            live_odds_by_race.get(race_id, {}),
            "win",
            [horse_number],
        )
        official_odds = live_odds_value(live_row)
        win_prob = _to_float(row.get("win_prob"))
        win_ev = win_prob * official_odds
        if official_odds <= 0 or win_prob <= 0 or win_ev < minimum_win_ev:
            continue

        row_tokens = {f"horse_number:{horse_number}"}
        horse_id = str(row.get("horse_id", "")).strip()
        if horse_id:
            row_tokens.add(f"horse_id:{horse_id}")
        if row_tokens & candidate_tokens_by_race.get(race_id, set()):
            continue

        missing.append(
            {
                "race_id": race_id,
                "horse_id": horse_id,
                "horse_number": horse_number,
                "horse_name": str(row.get("horse_name", "")).strip(),
                "win_prob": _fmt(win_prob),
                "official_odds": _fmt(official_odds),
                "win_ev": _fmt(win_ev),
                "minimum_win_ev": _fmt(minimum_win_ev),
                "odds_source": "jra_live",
            }
        )

    return sorted(
        missing,
        key=lambda row: _to_float(row.get("win_ev")),
        reverse=True,
    )


def _win_candidate_tokens(candidates: list[object], *, race_id: str) -> set[str]:
    tokens: set[str] = set()
    for value in candidates:
        if not isinstance(value, dict):
            continue
        candidate = dict(value)
        if str(candidate.get("race_id", "")).strip() not in {"", race_id}:
            continue
        if str(candidate.get("bet_type", "")).strip() != "win":
            continue
        horse_number = _horse_number_token(
            candidate.get("horse_number") or candidate.get("combination")
        )
        if horse_number:
            tokens.add(f"horse_number:{horse_number}")
        horse_id = str(candidate.get("horse_id", "")).strip()
        if horse_id:
            tokens.add(f"horse_id:{horse_id}")
    return tokens


def _horse_number_token(value: object) -> str:
    number = int(_to_float(value, 0.0))
    return str(number) if number > 0 else ""


def _race_order_from_configs(race_configs: list[dict[str, object]]) -> list[str]:
    order: list[str] = []
    for config in race_configs:
        race_id = _race_id_from_config(config)
        if race_id:
            order.append(race_id)
    return order


def _race_id_from_config(config: dict[str, object]) -> str:
    explicit = str(config.get("race_id", "")).strip()
    if explicit:
        return explicit

    race_date = str(config.get("race_date", "")).replace("-", "").strip()
    track = str(config.get("track", "")).strip()
    race_number = str(config.get("race_number", "")).strip()
    if race_date and track and race_number:
        return f"{race_date}_{track}_{int(_to_float(race_number)):02d}"
    return ""


def _downgrade_ticket_plan_for_review(ticket_plan: dict[str, object], review: dict[str, object]) -> dict[str, object]:
    tickets = [dict(ticket) for ticket in list(ticket_plan.get("tickets") or [])]
    if not tickets:
        return ticket_plan

    out = dict(ticket_plan)
    out["invalidated_tickets"] = tickets
    out["tickets"] = []
    out["ticket_status"] = "invalidated_by_reviewer"
    out["invalidation_reason"] = str(review.get("reason", "")).strip()
    out["portfolio_summary"] = {
        "total_stake": 0,
        "total_points": 0,
        "expected_return": 0,
        "expected_profit": 0,
        "portfolio_ev": "0",
        "no_gami": False,
    }

    downgraded_races = []
    for race in list(ticket_plan.get("races") or []):
        race_out = dict(race)
        race_tickets = [dict(ticket) for ticket in list(race_out.get("tickets") or [])]
        race_out["invalidated_tickets"] = race_tickets
        race_out["tickets"] = []
        race_out["ticket_status"] = "invalidated_by_reviewer"
        race_out["portfolio"] = out["portfolio_summary"]
        downgraded_races.append(race_out)
    out["races"] = downgraded_races
    return out


def _latest_snapshot_timestamp(rows: list[dict[str, object]] | list[dict[str, str]]) -> str:
    timestamps = [
        str(row.get("captured_at", "")).strip()
        for row in rows
        if str(row.get("captured_at", "")).strip()
    ]
    return max(timestamps) if timestamps else ""


def _find_divergent_rows(
    ev_rows: list[dict[str, object]],
    *,
    max_ev_delta_abs: float,
    max_ev_delta_ratio: float,
    max_odds_gap_ratio: float,
) -> list[dict[str, str]]:
    divergent: list[dict[str, str]] = []
    for row in ev_rows:
        current_odds = _to_float(row.get("current_odds"))
        predicted_odds = _to_float(row.get("predicted_odds"))
        ev_current = _to_float(row.get("ev_current") or row.get("ev"))
        ev_predicted = _to_float(row.get("ev_predicted"))
        if current_odds <= 0 or predicted_odds <= 0 or ev_current <= 0 or ev_predicted <= 0:
            continue

        odds_gap_ratio = abs((predicted_odds - current_odds) / current_odds)
        ev_delta = abs(ev_predicted - ev_current)
        ev_delta_ratio = ev_delta / max(ev_current, 1e-6)
        thresholds = _thresholds_for_popularity(
            _to_float(row.get("popularity_latest") or row.get("current_popularity")),
            defaults={
                "max_ev_delta_abs": max_ev_delta_abs,
                "max_ev_delta_ratio": max_ev_delta_ratio,
                "max_odds_gap_ratio": max_odds_gap_ratio,
            },
        )
        if (
            ev_delta >= thresholds["max_ev_delta_abs"]
            or ev_delta_ratio >= thresholds["max_ev_delta_ratio"]
            or odds_gap_ratio >= thresholds["max_odds_gap_ratio"]
        ):
            divergent.append(
                {
                    "race_id": str(row.get("race_id", "")),
                    "horse_id": str(row.get("horse_id", "")),
                    "horse_number": str(row.get("horse_number", "")),
                    "horse_name": str(row.get("horse_name", "")),
                    "popularity_band": thresholds["band"],
                    "current_odds": _fmt(current_odds),
                    "predicted_odds": _fmt(predicted_odds),
                    "ev_current": _fmt(ev_current),
                    "ev_predicted": _fmt(ev_predicted),
                    "ev_delta_ratio": _fmt(ev_delta_ratio),
                    "odds_gap_ratio": _fmt(odds_gap_ratio),
                }
            )
    return divergent


def _thresholds_for_popularity(
    popularity: float,
    *,
    defaults: dict[str, float],
) -> dict[str, float | str]:
    if popularity > 0 and popularity <= 3:
        return {
            "band": "favorite",
            "max_ev_delta_abs": min(defaults["max_ev_delta_abs"], 0.12),
            "max_ev_delta_ratio": min(defaults["max_ev_delta_ratio"], 0.12),
            "max_odds_gap_ratio": min(defaults["max_odds_gap_ratio"], 0.15),
        }
    if popularity > 0 and popularity <= 8:
        return {
            "band": "mid",
            "max_ev_delta_abs": min(defaults["max_ev_delta_abs"], 0.20),
            "max_ev_delta_ratio": min(defaults["max_ev_delta_ratio"], 0.18),
            "max_odds_gap_ratio": min(defaults["max_odds_gap_ratio"], 0.25),
        }
    return {
        "band": "longshot",
        "max_ev_delta_abs": max(defaults["max_ev_delta_abs"], 0.28),
        "max_ev_delta_ratio": max(defaults["max_ev_delta_ratio"], 0.28),
        "max_odds_gap_ratio": max(defaults["max_odds_gap_ratio"], 0.36),
    }


def _ticket_hit_prob(ticket: dict[str, object]) -> float:
    return _to_float(ticket.get("hit_prob") or ticket.get("wide_prob") or ticket.get("win_prob"))


def _ticket_odds(ticket: dict[str, object]) -> float:
    return _to_float(
        ticket.get("place_odds_est")
        or ticket.get("wide_odds_est")
        or ticket.get("wakuren_odds_est")
        or ticket.get("umaren_odds_est")
        or ticket.get("umatan_odds_est")
        or ticket.get("trio_odds_est")
        or ticket.get("trifecta_odds_est")
        or ticket.get("predicted_wide_odds")
        or ticket.get("win_odds")
    )


def _ticket_ev(ticket: dict[str, object], *, default: float = 0.0) -> float:
    return _to_float(ticket.get("ev_current") or ticket.get("ev"), default)


def _ticket_min_prob(ticket: dict[str, object]) -> float:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type == "place":
        return 0.16
    if bet_type == "wide":
        return 0.10
    if bet_type == "wakuren":
        return 0.035
    if bet_type == "umaren":
        return 0.035
    if bet_type == "umatan":
        return 0.018
    if bet_type == "sanrenpuku":
        return 0.018
    if bet_type == "sanrentan":
        return 0.006
    return 0.04


def _ticket_min_ev(ticket: dict[str, object], settings: WorkflowSettings) -> float:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type == "place":
        return settings.min_place_ev
    if bet_type == "wide":
        return settings.min_wide_ev
    if bet_type == "wakuren":
        return settings.min_wakuren_ev
    if bet_type == "umaren":
        return settings.min_umaren_ev
    if bet_type == "umatan":
        return settings.min_umatan_ev
    if bet_type == "sanrenpuku":
        return settings.min_sanrenpuku_ev
    if bet_type == "sanrentan":
        return settings.min_sanrentan_ev
    return max(settings.min_ev, 1.05)


def _longshot_odds_threshold(ticket: dict[str, object]) -> float:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type == "place":
        return 8.0
    if bet_type == "wide":
        return 16.0
    if bet_type == "wakuren":
        return 35.0
    if bet_type == "umaren":
        return 35.0
    if bet_type == "umatan":
        return 70.0
    if bet_type == "sanrenpuku":
        return 60.0
    if bet_type == "sanrentan":
        return 120.0
    return 20.0


def _longshot_stake_threshold(ticket: dict[str, object]) -> int:
    bet_type = str(ticket.get("bet_type", ""))
    if bet_type in {"place", "wide"}:
        return 300
    if bet_type in {"wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan"}:
        return 100
    return 100


def _ticket_horse_numbers(tickets: list[dict[str, object]]) -> set[str]:
    numbers: set[str] = set()
    for ticket in tickets:
        explicit = [str(value) for value in list(ticket.get("horse_numbers") or [])]
        value = str(ticket.get("horse_number", "")).replace(">", "-").replace("→", "-")
        numbers.update(part for part in explicit + value.split("-") if part.isdigit())
    return numbers


def _max_horse_ticket_dependency_ratio(tickets: list[dict[str, object]]) -> float:
    if not tickets:
        return 0.0
    counts: dict[str, int] = {}
    for ticket in tickets:
        explicit = [str(value) for value in list(ticket.get("horse_numbers") or [])]
        value = str(ticket.get("horse_number", "")).replace(">", "-").replace("→", "-")
        for horse_number in set(part for part in explicit + value.split("-") if part.isdigit()):
            counts[horse_number] = counts.get(horse_number, 0) + 1
    return max(counts.values(), default=0) / len(tickets)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
