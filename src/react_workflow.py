from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from analysis.ev import EVWeights, build_feature_rows, compute_ev, simulate_race_scenarios
from jra_scraper.config import ScrapeConfig
from jra_scraper.pipeline import JRAPipeline
from report.note import build_note_article
from strategy.betting import generate_tickets
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

        reasons: list[str] = []
        repair_actions: list[str] = []

        high_issues = int(dict(quality_report.get("issues_by_severity") or {}).get("high", 0))
        if high_issues > 0:
            reasons.append(f"high severity parser issues: {high_issues}")
            if attempt < self.settings.max_repair_attempts:
                repair_actions.append("retry_aggressive_parse")

        missing_odds = int(quality_report.get("missing_current_odds_entries", 0) or 0)
        if entry_rows and missing_odds == len(entry_rows):
            reasons.append("current odds are missing for every entry")
            if attempt < self.settings.max_repair_attempts:
                repair_actions.append("retry_aggressive_parse")

        prob_sums = _probability_sums(ev_rows)
        bad_prob_races = [race_id for race_id, total in prob_sums.items() if abs(total - 1.0) > 0.025]
        if bad_prob_races:
            reasons.append(f"probability normalization drift detected: {bad_prob_races}")

        if str(ticket_plan.get("bet_type", "")) == "win5":
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
        if divergent_rows and str(ticket_plan.get("bet_type", "")) != "win5":
            reasons.append(
                "predicted/current EV divergence detected: "
                + ", ".join(
                    f"{row['horse_name']}@{row['race_id']}"
                    for row in divergent_rows[:3]
                )
            )

        status = "OK" if not reasons else "NG"
        return {
            "status": status,
            "reason": "; ".join(reasons) if reasons else "quality gates passed",
            "fix": "; ".join(repair_actions) if repair_actions else "",
            "repair_actions": repair_actions,
            "probability_sums": {race_id: _fmt(total) for race_id, total in prob_sums.items()},
            "divergent_rows": divergent_rows,
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
    return settings.min_ev


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
