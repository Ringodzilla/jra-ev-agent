"""Source units must survive collection, coalescing, CSV and model boundaries."""
import csv
import hashlib
import io
import json

import pytest

from jra_scraper.models import HorseEntry
from jra_scraper.pipeline import _coalesce_history_rows, _rows_from_embedded_history
from jra_scraper.validation import OUTPUT_COLUMNS, _normalize_row
from src.data_integrity import _history_result_observations, audit_collected_data, verify_source_artifacts
from src.feature_engineering import UnsupportedHistoryObservation, summarize_history_rows


def source_page(header="平均1F", value="14.0", course="障害3,000メートル（芝）"):
    return f'''<div class="cell date">2026年9月20日</div>
    <div class="cell course">{course}</div><li class="weather"><span class="txt">晴</span></li>
    <table><thead><tr><th class="f_time">{header}</th></tr></thead>
    <tbody><tr><td class="horse"><a>A</a></td><td class="f_time">{value}</td>
    <td class="place">1</td><td class="num">1</td><td class="time">200.0</td>
    <td class="weight">55.0</td><td class="jockey">J</td><td class="pop">1</td></tr></tbody></table>'''


def collected_observation(html):
    artifact = dict(kind="history_result", race_id="r", horse_id="h", horse_name="A",
                    html=html, sha256=hashlib.sha256(html.encode()).hexdigest())
    row = _history_result_observations(html, artifact)[0]
    row.update(horse_number="1", run_index="1", target_race_date="2026-10-25")
    return {"rows": [row], "entries": [dict(race_id="r", horse_number="1", assigned_weight="55",
            current_odds="5", current_popularity="1")], "source_audit": {"artifacts": [artifact]}}


def test_average_furlong_survives_csv_and_cannot_enter_common_features():
    history = collected_observation(source_page())["rows"][0]
    horse = HorseEntry(race_id="r", race_name="R", horse_id="h", horse_name="A",
                       horse_url="https://jra.jp/horse", embedded_history=[history])
    row = _rows_from_embedded_history(horse)[0]
    assert row["last_3f"] == "" and row["last_3f_source"] != "fallback"
    normalized = _normalize_row(row)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=OUTPUT_COLUMNS)
    writer.writeheader()
    writer.writerow(normalized)
    buffer.seek(0)
    restored = next(csv.DictReader(buffer))
    assert restored["last_3f_status"] == "NOT_APPLICABLE"
    assert restored["average_1f"] == "14"
    assert restored["sectional_header"] == "平均1F"
    with pytest.raises(UnsupportedHistoryObservation, match="last_3f"):
        summarize_history_rows([restored], group_keys=("race_id", "horse_id"))


@pytest.mark.parametrize("status", ["NOT_APPLICABLE", "NOT_OBSERVED", "UNVERIFIED"])
@pytest.mark.parametrize("reverse", [False, True])
def test_merge_never_reinjects_legacy_36_into_a_typed_empty_observation(status, reverse):
    typed = dict(last_3f="", last_3f_status=status, last_3f_missing_reason="SOURCE_REASON")
    legacy = dict(last_3f="36.0", last_3f_source="fallback")
    a, b = (legacy, typed) if reverse else (typed, legacy)
    result = _coalesce_history_rows(a, b)
    assert result["last_3f"] == ""
    assert result["last_3f_status"] == status


def test_source_observed_recovery_restores_the_status_as_well_as_the_number():
    result = _coalesce_history_rows(
        dict(last_3f="", last_3f_status="NOT_OBSERVED"),
        dict(last_3f="35.1", last_3f_status="OBSERVED", last_3f_source="observed"),
    )
    assert result["last_3f"] == "35.1" and result["last_3f_status"] == "OBSERVED"


def test_nonapplicable_corner_conflict_is_retained_and_stops_features():
    result = _coalesce_history_rows(
        dict(passing_order="", passing_order_status="NOT_APPLICABLE"),
        dict(passing_order="2-1", passing_order_status="OBSERVED"),
    )
    assert result["passing_order"] == ""
    assert result["passing_order_status"] == "UNVERIFIED"
    assert json.loads(result["history_semantic_conflicts"])[0]["field"] == "passing_order"
    with pytest.raises(UnsupportedHistoryObservation, match="passing_order"):
        summarize_history_rows([result], group_keys=("race_id", "horse_id"))


def test_verified_nonapplicability_is_acquisition_complete_but_not_a_numeric_default():
    data = collected_observation(source_page())
    source = verify_source_artifacts(data)
    state = next(x for x in source["acquisition_coverage"]["records"] if x["field"] == "last_3f")
    assert state["state"] == "NOT_APPLICABLE"
    audit = audit_collected_data(data, {"r": {"career_starts_by_horse_number": {"1": 1}}})
    finding = next(x for x in audit["findings"] if x["field"] == "last_3f")
    assert finding["method"] == "unsupported_source_observation" and finding["required_missing"]
    assert audit["gate"] == "BLOCKED_MISSING_DATA"
    assert next(x for x in audit["missing_fields"] if x["field"] == "last_3f")["state"] == "NOT_APPLICABLE"


def test_caller_cannot_declare_a_real_observation_nonapplicable():
    data = collected_observation(source_page("推定上り", "35.1", "芝1,600メートル"))
    data["rows"][0].update(last_3f="", last_3f_status="NOT_APPLICABLE")
    result = verify_source_artifacts(data)
    assert result["status"] == "MISMATCH"
    state = next(x for x in result["acquisition_coverage"]["records"] if x["field"] == "last_3f")
    assert state["state"] == "AVAILABLE_BUT_NOT_COLLECTED"


def test_average_furlong_itself_cannot_be_silently_omitted_or_forged():
    data = collected_observation(source_page())
    data["rows"][0]["average_1f"] = ""
    result = verify_source_artifacts(data)
    state = next(x for x in result["acquisition_coverage"]["records"] if x["field"] == "average_1f")
    assert result["status"] == "MISMATCH" and state["state"] == "AVAILABLE_BUT_NOT_COLLECTED"


def test_primary_observed_value_preserves_its_own_units_and_status():
    result = _coalesce_history_rows(
        dict(last_3f="35.1", last_3f_status="OBSERVED", sectional_header="上り3F"),
        dict(last_3f="", last_3f_status="NOT_OBSERVED"),
    )
    assert result["last_3f"] == "35.1"
    assert result["last_3f_status"] == "OBSERVED"
    assert result["sectional_header"] == "上り3F"


def test_legacy_observation_is_not_marked_missing_by_an_empty_secondary():
    result = _coalesce_history_rows(
        dict(last_3f="35.1", last_3f_source="observed"),
        dict(last_3f="", last_3f_status="NOT_OBSERVED"),
    )
    assert result["last_3f"] == "35.1"
    assert result["last_3f_status"] == ""


@pytest.mark.parametrize("previous,expected", [
    ('[{"field": "older"}]', {"field": "older"}),
    ('{"legacy": true}', {"previous": {"legacy": True}}),
    ("invalid json", {"unparsed_previous": "invalid json"}),
])
def test_new_source_conflict_keeps_prior_conflict_evidence(previous, expected):
    result = _coalesce_history_rows(
        dict(passing_order="", passing_order_status="NOT_APPLICABLE",
             history_semantic_conflicts=previous),
        dict(passing_order="2-1", passing_order_status="OBSERVED"),
    )
    conflicts = json.loads(result["history_semantic_conflicts"])
    assert conflicts[0] == expected
    assert conflicts[-1]["reason"] == "NONAPPLICABLE_OBSERVATION_CONFLICT"


def test_source_proven_nonapplicability_rejects_an_injected_sectional():
    data = collected_observation(source_page())
    data["rows"][0]["last_3f"] = "36"
    result = verify_source_artifacts(data)
    assert any(x.get("reason_code") == "SOURCE_SEMANTIC_CONFLICT" for x in result["mismatches"])
    assert result["status"] == "MISMATCH"


def test_explicit_jump_observation_still_requires_a_supported_model():
    data = collected_observation(source_page())
    data["rows"][0]["history_surface"] = "障害"
    audit = audit_collected_data(data, {"r": {"career_starts_by_horse_number": {"1": 1}}})
    assert audit["gate"] == "BLOCKED_MISSING_DATA"
    assert any(f["method"] == "unsupported_source_observation" for f in audit["findings"])
