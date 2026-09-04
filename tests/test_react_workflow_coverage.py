from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from jra_scraper.config import ScrapeConfig
from src.agents.settings import WorkflowSettings
from src.react_workflow import (
    CANONICAL_STAGE_ORDER,
    ReactiveRaceWorkflow,
    _downgrade_ticket_plan_for_review,
    assert_canonical_stage_manifest,
    validate_canonical_stage_manifest,
)


def _config(tmp_path: Path) -> ScrapeConfig:
    return ScrapeConfig(
        output_csv=tmp_path / "race.csv",
        entries_csv=tmp_path / "entries.csv",
        odds_snapshots_csv=tmp_path / "odds.csv",
        combo_odds_csv=tmp_path / "combo.csv",
        raw_dir=tmp_path / "raw",
        state_path=tmp_path / "state.json",
        quality_report_path=tmp_path / "quality.json",
        missing_history_requests_path=tmp_path / "missing.json",
        manual_history_template_csv=tmp_path / "manual-template.csv",
        manual_history_csv=tmp_path / "manual.csv",
        stages_dir=tmp_path / "stages",
    )


def test_reactive_workflow_retries_then_writes_canonical_stages(tmp_path: Path) -> None:
    workflow = ReactiveRaceWorkflow(_config(tmp_path), settings=WorkflowSettings(max_repair_attempts=1))
    collector = Mock()
    collector.run.return_value = {
        "rows": [{"horse": "A"}],
        "odds_snapshots": [{"odds": "2"}],
        "combo_odds": [{"combination": "1-2"}],
        "quality_report": {"ok": True},
    }
    workflow.domestic_collector = collector
    workflow.analyzer = Mock()
    workflow.analyzer.run.return_value = {"feature_rows": [{"feature": 1}]}
    workflow.simulator = Mock()
    workflow.simulator.run.return_value = {"scenario_rows": [{"scenario": 1}]}
    workflow.ev_calculator = Mock()
    workflow.ev_calculator.run.return_value = {"ev_rows": [{"ev": "1.2"}]}
    workflow.bet_builder = Mock()
    workflow.bet_builder.run.return_value = {
        "tickets": [{"bet_type": "win", "stake": 100}],
        "races": [{"tickets": [{"bet_type": "win", "stake": 100}]}],
    }
    workflow.reviewer = Mock()
    workflow.reviewer.run.side_effect = [
        {"status": "NG", "reason": "repair", "repair_actions": [{"action": "retry"}]},
        {"status": "OK", "reason": "done", "repair_actions": []},
    ]
    workflow.article_writer = Mock()
    workflow.article_writer.run.return_value = {"status": "published"}

    payload = workflow.run([{"race_id": "R"}], force_rebuild=False, race_limit=1, horse_limit=2, reprocess_raw=True)
    assert payload["attempt"] == 1
    assert collector.run.call_count == 2
    assert collector.run.call_args.kwargs["force_rebuild"] is True
    assert validate_canonical_stage_manifest(workflow.config.stages_dir) == []
    assert all((workflow.config.stages_dir / filename).exists() for filename in CANONICAL_STAGE_ORDER)


def test_collector_resolution_covers_every_signal(tmp_path: Path) -> None:
    workflow = ReactiveRaceWorkflow(_config(tmp_path))
    assert workflow.resolve_collector_key([]) == "domestic"
    assert workflow.resolve_collector_key([{"collector_mode": "overseas"}]) == "overseas"
    assert workflow.resolve_collector_key([{"collector_mode": "domestic"}]) == "domestic"
    assert workflow.resolve_collector_key([{"source_url": "/JRADB/accessSD.html"}]) == "overseas"
    assert workflow.resolve_collector_key([{"track": "香港"}]) == "overseas"
    assert workflow.resolve_collector_key([{"race_name": "ドバイ国際競走"}]) == "overseas"
    assert workflow._select_collector([{"track": "香港"}]) is workflow.overseas_collector
    assert workflow._select_collector([{"track": "札幌"}]) is workflow.domestic_collector


def test_manifest_validation_reports_all_metadata_and_file_errors(tmp_path: Path) -> None:
    stages = tmp_path / "stages"
    stages.mkdir()
    assert validate_canonical_stage_manifest(stages) == ["run_manifest.json is missing"]
    (stages / "run_manifest.json").write_text("not json", encoding="utf-8")
    assert "invalid" in validate_canonical_stage_manifest(stages)[0]

    (stages / "run_manifest.json").write_text(
        json.dumps({"schema_version": "bad", "producer": "bad", "stage_order": [], "artifacts": {}}),
        encoding="utf-8",
    )
    errors = validate_canonical_stage_manifest(stages)
    assert "stage schema version does not match the canonical workflow" in errors
    assert "stage producer is not the canonical workflow" in errors
    assert "stage order does not match the repository workflow" in errors
    assert any("is missing" in error for error in errors)
    with pytest.raises(ValueError, match="invalid canonical"):
        assert_canonical_stage_manifest(stages)


def test_downgrade_without_tickets_is_identity() -> None:
    plan = {"tickets": [], "portfolio_summary": {"total_stake": 0}}
    assert _downgrade_ticket_plan_for_review(plan, {"reason": "none"}) is plan
