from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.agents.reviewer import ReviewerAgent
from src.agents.settings import WorkflowSettings
from src.deadline import DeadlineSettings, build_deadline_plan
from src.final_workflow import (
    FinalReviewerAgent,
    _baseline_review_revalidation_allowed,
    build_baseline_quality,
)


NOW = datetime(2026, 9, 12, 15, 30, tzinfo=ZoneInfo("Asia/Tokyo"))


def _investment_review():
    return ReviewerAgent(WorkflowSettings()).run(
        {"quality_report": {}, "entries": [{"horse_number": "1"}]},
        [],
        [{"race_id": "R", "horse_number": "1", "win_prob": "1"}],
        {"tickets": [{"bet_type": "win", "horse_number": "1", "hit_prob": "1", "ev": "0.5", "win_odds": "0.5", "stake": 100}]},
        attempt=0,
    )


def _quality(review):
    rows = [{"horse_id": "H", "horse_number": "1", "run_index": str(i)} for i in range(1, 6)]
    return build_baseline_quality({
        "data_collector": {"rows": rows, "quality_report": {}},
        "_baseline_manifest_valid": True,
        "reviewer": review,
    }, rows)


def _final(quality, current_status="OK", tickets_present=True):
    settings = DeadlineSettings()
    plan = build_deadline_plan({"race_date": "2026-09-12", "post_time": "15:45"}, now=NOW, settings=settings)
    collected = {
        "snapshot_id": "S", "snapshot_complete": True,
        "official_odds_as_of": NOW.isoformat(),
        "conditions": {"weather": "晴", "track_condition": "良", "captured_at": NOW.isoformat()},
        "entries": [{"horse_number": "1", "body_weight_status": "published", "current_body_weight": 470, "body_weight_change": 0}],
        "combo_odds": [{"snapshot_id": "S", "snapshot_complete": True, "bet_type": "win", "combination": "1"}],
        "quality_report": {"combination_coverage": {"complete": True}, "official_odds_timestamps_complete": True},
        "lineup": {"matches": True}, "baseline_quality": quality,
    }
    ticket_plan = {"tickets": [{"bet_type": "win", "horse_number": "1", "odds_source": "jra_live"}] if tickets_present else []}
    return FinalReviewerAgent(settings=settings, now=lambda: NOW).run(
        plan=plan, collected=collected, ticket_plan=ticket_plan,
        quantitative_review={"status": current_status},
    )


def test_latest_passing_review_supersedes_old_investment_failure_with_audit():
    old_review = _investment_review()
    original = deepcopy(old_review)
    assert old_review["status"] == "NG"
    assert old_review["review_schema_version"] == 1
    assert old_review["reason_codes"] == [item["code"] for item in old_review["failures"]]
    quality = _quality(old_review)
    assert quality["review_ok"] is False
    assert quality["review_revalidation_allowed"] is True
    final = _final(quality)
    assert final["decision"] == "GO"
    assert final["checks"]["baseline_review"] is True
    audit = final["baseline_review_resolution"]
    assert audit["original_status"] == "NG"
    assert audit["original_reason_codes"] == old_review["reason_codes"]
    assert audit["original_reason"] == old_review["reason"]
    assert audit["revalidated"] is True
    assert old_review == original


@pytest.mark.parametrize("gate", ["history_complete", "parser_quality_ok", "manifest_ok"])
def test_revalidated_investment_review_cannot_override_data_gates(gate):
    quality = _quality(_investment_review())
    quality[gate] = False
    assert _final(quality)["decision"] == "NO_GO"


@pytest.mark.parametrize("status", ["NG", "", "UNKNOWN"])
def test_current_review_must_pass_before_baseline_can_be_superseded(status):
    final = _final(_quality(_investment_review()), current_status=status)
    assert final["decision"] == "NO_GO"
    assert final["checks"]["baseline_review"] is False
    assert final["baseline_review_resolution"]["revalidated"] is False


def test_no_value_tickets_keep_specific_decision_after_successful_revalidation():
    final = _final(_quality(_investment_review()), tickets_present=False)
    assert final["decision_code"] == "NO_GO_NO_VALUE_TICKETS"
    assert final["checks"]["baseline_review"] is True


@pytest.mark.parametrize("mutation", [
    {"status": "UNKNOWN"},
    {"review_schema_version": 2},
    {"review_schema_version": None},
    {"reason_codes": None},
    {"reason_codes": []},
    {"failures": None},
    {"failures": []},
    {"failures": ["invalid"]},
    {"failures": [{"code": [], "message": "invalid"}]},
    {"failures": [{"code": "TOP3_COVERAGE_LOW", "message": ""}]},
    {"reason": "different unexplained failure"},
    {"probability_lineage": {"status": "NG", "errors": []}},
    {"value_integrity": {"status": "OK", "errors": ["mismatch"]}},
    {"value_integrity": None},
])
def test_legacy_incomplete_or_contradictory_review_evidence_stays_closed(mutation):
    review = dict(_investment_review(), **mutation)
    assert _baseline_review_revalidation_allowed(review) is False
    assert _final(_quality(review))["decision"] == "NO_GO"


@pytest.mark.parametrize("code", [
    "PARSER_HIGH_SEVERITY", "ALL_CURRENT_ODDS_MISSING", "PROBABILITY_NORMALIZATION_DRIFT",
    "PROBABILITY_LINEAGE_INVALID", "TICKET_VALUE_INTEGRITY_INVALID", "ELIGIBLE_WIN_CANDIDATE_MISSING",
    "WIN5_NO_VALID_POINTS", "NEW_UNKNOWN_FAILURE",
])
def test_mixed_investment_and_input_or_unknown_failure_stays_closed(code):
    review = _investment_review()
    review["reason_codes"].append(code)
    review["failures"].append({"code": code, "message": "additional failure"})
    review["reason"] += "; additional failure"
    assert _baseline_review_revalidation_allowed(review) is False
    assert _final(_quality(review))["decision"] == "NO_GO"


def test_missing_legacy_review_and_normal_ok_remain_compatible():
    for review in ({}, {"status": "NG", "reason": "top-3 ticket coverage is too low: 1/2"}):
        assert _final(_quality(review))["decision"] == "NO_GO"
    review = ReviewerAgent(WorkflowSettings()).run({}, [], [], {"tickets": []}, attempt=0)
    assert review["status"] == "OK"
    assert review["reason_codes"] == review["failures"] == []
    assert _final(_quality(review))["decision"] == "GO"


def test_reviewer_integrity_failure_is_structured_and_cannot_be_superseded():
    review = ReviewerAgent(WorkflowSettings()).run(
        {}, [], [{"horse_number": "1", "win_prob": "1"}],
        {"tickets": [{"bet_type": "win", "horse_number": "1", "odds_source": "jra_live", "hit_prob": "1", "win_odds": "3", "ev_current": "3", "ev": "4", "stake": 100}]},
        attempt=0,
    )
    assert "TICKET_VALUE_INTEGRITY_INVALID" in review["reason_codes"]
    assert review["value_integrity"]["status"] == "NG"
    assert _baseline_review_revalidation_allowed(review) is False
