from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from jra_scraper.config import ScrapeConfig
from jra_scraper.live_snapshot import LiveSnapshotDeadlineExceeded
from src.deadline import DeadlineSettings, build_deadline_plan
from src.final_workflow import (
    FinalPredictionWorkflow,
    FinalReviewerAgent,
    _age_seconds,
    _as_utc,
    _same_number,
    _is_true,
    _ticket_horse_numbers,
    _tickets_have_exact_live_odds,
    _to_float_value,
    _to_int,
    _unique_rows_by_horse_number,
    build_baseline_quality,
    build_fixed_analysis_ev_rows,
    extract_baseline_rows,
    invalidate_ticket_plan,
    merge_live_entries,
)


JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 8, 29, 14, 0, tzinfo=JST)
RACE = {
    "race_id": "R",
    "race_name": "Race",
    "race_date": "2026-08-29",
    "post_time": "15:00",
    "track": "札幌",
    "race_number": 7,
    "source_url": "https://race",
}


def _baseline() -> list[dict[str, object]]:
    return [
        {
            "race_id": "R", "horse_id": "H1", "horse_number": "1", "horse_name": "Alpha",
            "target_race_date": "2026-08-29", "target_track": "札幌", "target_race_number": "7",
            "current_jockey": "J", "assigned_weight": "55", "target_surface": "芝",
            "target_distance": "1200", "target_track_condition": "良", "run_index": "1",
        }
    ]


def _entry() -> dict[str, object]:
    return {
        "race_id": "R", "horse_id": "H1", "horse_number": "1", "horse_name": "Alpha", "frame_number": "1",
        "current_jockey": "J", "assigned_weight": "55", "target_track": "札幌", "target_race_date": "2026-08-29",
        "target_race_number": "7", "target_surface": "芝", "target_distance": "1200",
        "target_track_condition": "良", "target_weather": "晴", "current_odds": "2", "current_popularity": "1",
        "current_body_weight": "470", "body_weight_change": "0", "body_weight_status": "published",
        "target_conditions_captured_at": NOW.isoformat(), "snapshot_id": "S",
    }


def _live() -> dict[str, object]:
    return {
        "snapshot_id": "S",
        "snapshot_complete": True,
        "official_odds_as_of": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "conditions": {"weather": "晴", "track_condition": "良", "captured_at": NOW.isoformat()},
        "entries": [_entry()],
        "combo_odds": [
            {"race_id": "R", "bet_type": "win", "combination": "1", "odds": "2", "snapshot_id": "S", "snapshot_complete": True}
        ],
        "quality_report": {
            "issues_by_severity": {}, "missing_current_odds_entries": 0, "bet_types_missing": [],
            "official_odds_timestamps_complete": True, "combination_coverage": {"complete": True},
        },
    }


def _workflow(tmp_path: Path, now: datetime, collector: Mock) -> FinalPredictionWorkflow:
    return FinalPredictionWorkflow(
        ScrapeConfig(raw_dir=tmp_path / "raw"),
        output_dir=tmp_path / f"out-{len(list(tmp_path.glob('out-*')))}",
        now=lambda: now,
        live_collector=collector,
    )


def test_workflow_early_deadline_baseline_and_fixed_load_failures(tmp_path: Path) -> None:
    collector = Mock()
    too_late = _workflow(tmp_path, datetime(2026, 8, 29, 14, 56, tzinfo=JST), collector)
    assert too_late.run(RACE, baseline=_baseline())["final_decision"]["decision_code"] == "NO_GO_CUTOFF_REACHED"

    low_budget = _workflow(tmp_path, datetime(2026, 8, 29, 14, 54, 30, tzinfo=JST), Mock())
    assert low_budget.run(RACE, baseline=_baseline())["final_decision"]["decision_code"] == "NO_GO_INSUFFICIENT_TIME_BUDGET"

    missing = _workflow(tmp_path, NOW, Mock())
    assert missing.run(RACE, baseline=[])["final_decision"]["decision_code"] == "NO_GO_BASELINE_MISSING"

    fixed_error = _workflow(tmp_path, NOW, Mock())
    result = fixed_error.run(RACE, baseline=_baseline(), fixed_analysis={"_load_error": "bad hash"})
    assert result["final_decision"]["decision_code"] == "NO_GO_FIXED_ANALYSIS_INVALID"


def test_workflow_refresh_exceptions_and_close_failure(tmp_path: Path) -> None:
    collector = Mock()
    collector.collect.side_effect = LiveSnapshotDeadlineExceeded("late")
    result = _workflow(tmp_path, NOW, collector).run(RACE, baseline=_baseline())
    assert result["final_decision"]["decision_code"] == "NO_GO_REFRESH_BUDGET_EXHAUSTED"

    collector = Mock()
    collector.collect.side_effect = RuntimeError("network")
    result = _workflow(tmp_path, NOW, collector).run(RACE, baseline=_baseline())
    assert result["final_decision"]["decision_code"] == "NO_GO_LIVE_REFRESH_FAILED"

    collector = Mock()
    collector.close.side_effect = RuntimeError("close")
    _workflow(tmp_path, NOW, collector)._close_live_collector()


def test_workflow_invalid_fixed_analysis_finishes_after_live(tmp_path: Path) -> None:
    collector = Mock()
    collector.collect.return_value = _live()
    workflow = _workflow(tmp_path, NOW, collector)
    payload = workflow.run(
        RACE,
        baseline=_baseline(),
        fixed_analysis={"analyzer": {"race_id": "OTHER"}, "simulator": {"race_id": "R"}},
    )
    assert payload["final_decision"]["decision_code"] == "NO_GO_FIXED_ANALYSIS_INVALID"
    assert payload["final_decision"]["analysis_mode"] == "fixed_reprice"


def test_workflow_applies_quantitative_repair_then_finally_invalidates(tmp_path: Path) -> None:
    collector = Mock()
    collector.collect.return_value = _live()
    workflow = _workflow(tmp_path, NOW, collector)
    workflow.analyzer = Mock()
    workflow.analyzer.run.return_value = {"feature_rows": [{"x": 1}]}
    workflow.simulator = Mock()
    workflow.simulator.run.return_value = {"scenario_rows": [{"x": 1}]}
    workflow.ev_calculator = Mock()
    workflow.ev_calculator.run.return_value = {"ev_rows": [{"race_id": "R", "horse_number": "1", "win_prob": "1"}]}
    original = {"tickets": [{"bet_type": "win", "horse_number": "1", "odds_source": "jra_live"}]}
    repaired = {"tickets": [{"bet_type": "win", "horse_number": "1", "odds_source": "jra_live", "repaired": True}]}
    workflow.bet_builder = Mock()
    workflow.bet_builder.run.return_value = original
    workflow.quantitative_reviewer = Mock()
    workflow.quantitative_reviewer.run.side_effect = [
        {"status": "NG", "reason": "repair", "repair_actions": [{"action": "x"}]},
        {"status": "OK", "reason": "fixed", "repair_actions": []},
    ]
    workflow.final_reviewer = Mock()
    workflow.final_reviewer.run.return_value = {
        "status": "NG", "decision": "NO_GO", "decision_code": "NO_GO_FINAL_REVIEW_FAILED",
        "issued_at": NOW.isoformat(), "reason": "stop", "checks": {},
    }
    with patch("src.final_workflow.apply_ticket_repair_actions", return_value=repaired):
        payload = workflow.run(RACE, baseline=_baseline())
    assert workflow.quantitative_reviewer.run.call_count == 2
    assert payload["bet_builder"]["ticket_status"] == "invalidated_by_final_reviewer"
    assert payload["final_decision"]["tickets"] == []


def _valid_final_inputs() -> tuple[dict[str, object], dict[str, object]]:
    collected = _live()
    collected["lineup"] = {"matches": True}
    collected["baseline_quality"] = {"history_complete": True, "review_ok": True, "parser_quality_ok": True, "manifest_ok": True}
    ticket = {"tickets": [{"bet_type": "win", "horse_number": "1", "odds_source": "jra_live"}]}
    return collected, ticket


def test_final_reviewer_deadline_and_no_value_codes() -> None:
    settings = DeadlineSettings()
    plan = build_deadline_plan(RACE, now=NOW, settings=settings)
    collected, ticket = _valid_final_inputs()
    after_deadline = plan.output_deadline + timedelta(seconds=1)
    collected["official_odds_as_of"] = after_deadline.isoformat()
    collected["conditions"] = {"weather": "晴", "track_condition": "良", "captured_at": after_deadline.isoformat()}
    result = FinalReviewerAgent(settings=settings, now=lambda: after_deadline).run(
        plan=plan, collected=collected, ticket_plan=ticket, quantitative_review={"status": "OK"}
    )
    assert result["decision_code"] == "NO_GO_DEADLINE_MISSED"

    collected, _ = _valid_final_inputs()
    result = FinalReviewerAgent(settings=settings, now=lambda: NOW).run(
        plan=plan, collected=collected, ticket_plan={"tickets": []}, quantitative_review={"status": "OK"}
    )
    assert result["decision_code"] == "NO_GO_NO_VALUE_TICKETS"


def test_baseline_merge_and_quality_dictionary_edges() -> None:
    payload = {"data_collector": {"rows": _baseline()}}
    assert extract_baseline_rows(payload, RACE)
    assert extract_baseline_rows({}, RACE) == []
    fallback_race = {"race_date": "2026-08-29", "track": "札幌", "race_number": "7"}
    assert extract_baseline_rows(_baseline(), fallback_race)

    merged, lineup = merge_live_entries(_baseline(), [], zero_history_horse_numbers=set())
    assert merged == [] and not lineup["matches"]
    extra = dict(_entry(), horse_id="H2", horse_number="2")
    merged, _ = merge_live_entries(_baseline(), [_entry(), extra], zero_history_horse_numbers=set())
    assert len(merged) == 1

    pipeline = {
        "data_collector": {"rows": _baseline(), "quality_report": {"issues_by_severity": {"high": 1}}},
        "reviewer": {"status": "NG"},
        "_baseline_manifest_valid": False,
    }
    quality = build_baseline_quality(pipeline, _baseline(), race_config=RACE)
    assert quality["source_kind"] == "pipeline_run"
    assert not quality["review_ok"] and not quality["manifest_ok"] and not quality["parser_quality_ok"]


def _fixed_one() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], list[dict[str, str]]]:
    fixed = {
        "analyzer": {"race_id": "R", "scores": [{"horse_number": 1, "horse": "Alpha", "ability": 50, "course": 0, "pace": 0, "weight": 0, "jockey": 0, "S": 50}]},
        "simulator": {"race_id": "R", "probabilities": [{"horse_number": 1, "high": 1, "mid": 1, "slow": 1, "final": 1}]},
    }
    return fixed, [_entry()], [{"race_id": "R", "bet_type": "win", "combination": "1", "odds": "2"}], [dict(row) for row in _baseline()]


def test_fixed_analysis_validation_matrix() -> None:
    fixed, entries, odds, baseline = _fixed_one()
    cases = []
    bad = deepcopy(fixed); bad["analyzer"]["race_id"] = "OTHER"; cases.append(bad)
    bad = deepcopy(fixed); bad["simulator"]["race_id"] = "OTHER"; cases.append(bad)
    bad = deepcopy(fixed); bad["analyzer"]["scores"] = [{"horse_number": 2, "horse": "Beta", "S": 0}]; cases.append(bad)
    bad = deepcopy(fixed); bad["simulator"]["probabilities"] = [{"horse_number": 2, "high": 1, "mid": 1, "slow": 1, "final": 1}]; cases.append(bad)
    bad = deepcopy(fixed); bad["analyzer"]["scores"][0]["S"] = 49; cases.append(bad)
    bad = deepcopy(fixed); bad["analyzer"]["scores"][0]["horse"] = "Other"; cases.append(bad)
    bad = deepcopy(fixed); bad["simulator"]["probabilities"][0]["high"] = 2; cases.append(bad)
    bad = deepcopy(fixed); bad["simulator"]["probabilities"][0]["mid"] = 0.5; cases.append(bad)
    for candidate in cases:
        with pytest.raises(ValueError):
            build_fixed_analysis_ev_rows(
                race_config=RACE, fixed_analysis=candidate, live_entries=entries, combo_odds=odds, baseline_rows=baseline
            )

    with pytest.raises(ValueError, match="baseline row"):
        build_fixed_analysis_ev_rows(
            race_config=RACE, fixed_analysis=fixed, live_entries=entries, combo_odds=odds, baseline_rows=[]
        )
    with pytest.raises(ValueError, match="duplicate official"):
        build_fixed_analysis_ev_rows(
            race_config=RACE, fixed_analysis=fixed, live_entries=entries, combo_odds=odds + odds, baseline_rows=baseline
        )
    with pytest.raises(ValueError, match="do not cover"):
        build_fixed_analysis_ev_rows(
            race_config=RACE, fixed_analysis=fixed, live_entries=entries, combo_odds=[], baseline_rows=baseline
        )

    filtered_odds = [
        {"race_id": "OTHER", "bet_type": "win", "combination": "1", "odds": "2"},
        {"race_id": "R", "bet_type": "place", "combination": "1", "odds": "2"},
        *odds,
    ]
    result = build_fixed_analysis_ev_rows(
        race_config=RACE,
        fixed_analysis=fixed,
        live_entries=entries,
        combo_odds=filtered_odds,
        baseline_rows=[{}, *baseline],
    )
    assert result[0]["horse_number"] == "1"


def test_fixed_and_ticket_low_level_helpers() -> None:
    with pytest.raises(ValueError, match="missing horse"):
        _unique_rows_by_horse_number([{}], label="rows")
    with pytest.raises(ValueError, match="duplicate"):
        _unique_rows_by_horse_number([{"horse_number": 1}, {"horse_number": "1"}], label="rows")
    with pytest.raises(ValueError, match="empty"):
        _unique_rows_by_horse_number([], label="rows")
    assert _to_float_value(None, 4.0) == 4.0
    assert _to_float_value(object(), 5.0) == 5.0

    plan = invalidate_ticket_plan({"tickets": [{"horse": "A"}]}, "stop")
    assert plan["tickets"] == [] and plan["invalidated_tickets"] == [{"horse": "A"}]
    numbers = _ticket_horse_numbers(
        [{"horse_numbers": ["2"], "legs": [{"horse_number": "3", "horses": [{"horse_number": "4"}]}]}]
    )
    assert numbers == {"2", "3", "4"}
    assert not _tickets_have_exact_live_odds([], [])
    assert not _tickets_have_exact_live_odds(
        [{"bet_type": "win", "horse_number": "1", "odds_source": "jra_live"}], []
    )
    assert _age_seconds(NOW, "") is None
    assert _age_seconds(NOW, "invalid") is None
    assert _same_number("x", "x")
    assert _is_true("yes")
    assert _to_int(object()) == 0
    assert _as_utc(datetime(2026, 1, 1)).tzinfo is not None

    formation = {
        "bet_type": "sanrentan",
        "odds_source": "jra_live",
        "points": [
            {
                "horse_numbers": ["1", "2", "3"],
                "odds_source": "jra_live",
            }
        ],
    }
    live_point = {
        "bet_type": "sanrentan",
        "combination": "1>2>3",
        "odds": "20",
    }
    assert _tickets_have_exact_live_odds([formation], [live_point])
