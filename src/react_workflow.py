from __future__ import annotations

import json
from pathlib import Path

from analysis.ev import EVWeights
from jra_scraper.config import ScrapeConfig
from src.agents import (
    AnalyzerAgent,
    ArticleWriterAgent,
    BetBuilderAgent,
    DataCollectorAgent,
    DomesticDataCollectorAgent,
    EVCalculatorAgent,
    OverseasDataCollectorAgent,
    ReviewerAgent,
    SimulatorAgent,
    WorkflowSettings,
    apply_ticket_repair_actions,
)
from src.artifacts import atomic_write_json, file_sha256


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
            atomic_write_json(self.config.stages_dir / filename, body or {})
        manifest = {
            "schema_version": CANONICAL_STAGE_SCHEMA_VERSION,
            "producer": CANONICAL_STAGE_PRODUCER,
            "stage_order": list(CANONICAL_STAGE_ORDER),
            "attempt": payload.get("attempt"),
            "artifacts": {
                filename: {"sha256": file_sha256(self.config.stages_dir / filename)}
                for filename in CANONICAL_STAGE_ORDER
            },
        }
        atomic_write_json(self.config.stages_dir / "run_manifest.json", manifest)
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
        actual = file_sha256(path)
        if not expected or expected != actual:
            errors.append(f"{filename} digest does not match run_manifest.json")
    return errors


def assert_canonical_stage_manifest(stages_dir: Path) -> None:
    errors = validate_canonical_stage_manifest(stages_dir)
    if errors:
        raise ValueError("invalid canonical stage artifacts: " + "; ".join(errors))


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
