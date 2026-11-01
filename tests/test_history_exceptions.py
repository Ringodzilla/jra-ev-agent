"""Explicit exceptions are input-scoped projections, never source observations."""
from copy import deepcopy
import hashlib
import json
from decimal import Decimal

import pytest

from src.data_integrity import data_input_sha256
from src.history_exceptions import (HistoryExceptionError, model_history_rows,
                                    source_manifest_sha256, validate_history_exceptions)


@pytest.fixture(autouse=True)
def isolated_raw_adapter(monkeypatch):
    # The adapter's own tests verify native source parsing. These tests isolate
    # exception scopes and replay; source evidence remains SHA-checked here.
    monkeypatch.setattr("jra_scraper.foreign_history.parse_imperial_proxy_observation",
                        lambda item, row: Decimal(str(item["individual_final_3_imperial_furlongs_seconds"])),
                        raising=False)


def _ref(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return _ref(path)


def _fixture(tmp_path, field="passing_order"):
    raw = tmp_path / "original.txt"
    raw.write_text("Raw official imperial sectional observations", encoding="utf-8")
    nested = tmp_path / "extract.txt"
    nested.write_text("source definitions", encoding="utf-8")
    obs = [{"horse_name": "A", "official_horse_name": "A", "race_date": "2025-08-20", "source": _ref(raw),
            "individual_final_3_imperial_furlongs_splits_seconds": ["11.22", "11.74", "13.04"],
            "individual_final_3_imperial_furlongs_seconds": 36,
            "section_distance_m": 603.504}]
    observation = _save(tmp_path / "observations.json", obs)
    evidence = _save(tmp_path / "definitions.json", {
        "extracted_path": str(nested.resolve()), "extracted_sha256": _ref(nested)["sha256"]})
    report_data = {"role": "data_collector", "routes": [{"route": "checked"}],
        "unresolved_fields": {"actual_corner_positions": ["A 2025-08-20 Fixture"],
                              "exact_metric600m": ["A 2025-08-20 Fixture"]},
        "evidence_files": ["observations.json", "definitions.json"]}
    report_data["unresolved_observations"] = [{"race_id": "R", "horse_id": "H", "horse_number": "1",
        "run_index": "5", "date": "2025-08-20", "field": field, "reason": "Observation unavailable"}]
    report = _save(tmp_path / "attempts.json", report_data)
    config = [{"race_id": "R", "race_date": "2026-11-01", "track": "東京", "race_number": 11}]
    row = {"race_id": "R", "horse_id": "H", "horse_name": "A", "horse_number": "1", "run_index": "5",
           "date": "2025年8月20日", "last_3f": "", "last_3f_status": "NOT_OBSERVED",
           "passing_order": "", "passing_order_status": "NOT_OBSERVED", "history_surface": "芝"}
    collected = {"rows": [row], "entries": [{"race_id": "R", "horse_id": "H"}], "race_configs": config}
    digest = data_input_sha256(collected, config)
    record = {k: row[k] for k in ("race_id", "horse_id", "horse_number", "run_index")}
    record.update(date="2025-08-20", field=field,
                  method="imperial_3f_proxy_seconds" if field == "last_3f" else "approved_front_rate",
                  value=36 if field == "last_3f" else .5, input_sha256=digest,
                  evidence="User explicitly approved these unavailable history exceptions.",
                  finding_ids=["a" * 24], source_attempts=report,
                  source_artifacts=[observation, evidence], proxy_observations=observation,
                  source_distance_m=603.504, target_distance_m=600,
                  source_manifest_sha256=source_manifest_sha256(collected))
    approval = dict(approved_by="user", status="APPROVED", race_id="R", purpose="prediction",
                    input_sha256=digest, evidence=record["evidence"], finding_ids=["a" * 24],
                    scoped_history_exceptions=[record])
    return collected, config, approval, record


def _validated(data, config, approval):
    records = validate_history_exceptions(data, config, [approval], "prediction")
    return {"gate": "PASS", "input_sha256": data_input_sha256(data, config),
            "approval_evidence": [deepcopy(approval)], "purpose": "prediction",
            "approved_history_exceptions": records}


@pytest.mark.parametrize("field", ["last_3f", "passing_order", "weather", "pace"])
def test_approved_projection_preserves_source_rows_states_and_no_fake_corners(tmp_path, field):
    data, config, approval, record = _fixture(tmp_path, field)
    if field in {"weather", "pace"}:
        record.update(method="not_used", value=None, finding_ids=[])
    before = deepcopy(data)
    audit = _validated(data, config, approval)
    projected = model_history_rows(data, audit)
    assert data == before
    assert projected[0]["last_3f_status"] == "NOT_OBSERVED"
    assert projected[0]["passing_order"] == ""
    assert projected[0]["approved_history_exceptions"][field]["method"] == record["method"]
    if field == "last_3f":
        assert projected[0]["last_3f"] == "36"
    elif field == "passing_order":
        assert projected[0]["approved_front_rate"] == .5
    else:
        assert field not in projected[0]


def test_no_scoped_exceptions_is_an_independent_copy(tmp_path):
    data, config, _, _ = _fixture(tmp_path)
    assert validate_history_exceptions(data, config, [{"evidence": "other approval"}]) == []
    rows = model_history_rows(data, {"gate": "BLOCKED"})
    rows[0]["last_3f"] = "999"
    assert data["rows"][0]["last_3f"] == ""


@pytest.mark.parametrize("field,value", [
    ("approved_by", "assistant"), ("status", "PROPOSED"), ("input_sha256", "wrong"),
    ("purpose", "other"), ("evidence", ""), ("evidence", True), ("finding_ids", None),
    ("scoped_history_exceptions", {}),
])
def test_invalid_approval_cannot_authorize_a_projection(tmp_path, field, value):
    data, config, approval, _ = _fixture(tmp_path)
    approval[field] = value
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


@pytest.mark.parametrize("field,value", [
    ("input_sha256", "wrong"), ("evidence", "different human statement"),
    ("purpose", "research"), ("race_id", "other"), ("horse_id", "other"),
    ("run_index", "4"), ("date", "2026-11-01"), ("date", "2025年8月20日"),
    ("field", "time"), ("method", "unapproved_fallback"),
    ("finding_ids", ["bad-id"]), ("finding_ids", ["a" * 24, "a" * 24]),
    ("finding_ids", ["b" * 24]), ("finding_ids", None),
    ("value", .6), ("value", None), ("value", True),
])
def test_record_is_bound_to_exact_identity_evidence_and_supported_method(tmp_path, field, value):
    data, config, approval, record = _fixture(tmp_path)
    record[field] = value
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


@pytest.mark.parametrize("change", ["unknown_config", "duplicate_row", "duplicate_record", "not_object", "missing_field"])
def test_scope_requires_one_requested_race_and_one_history(tmp_path, change):
    data, config, approval, record = _fixture(tmp_path)
    if change == "unknown_config":
        config[0]["race_id"] = "other"
    elif change == "duplicate_row":
        data["rows"].append(deepcopy(data["rows"][0]))
    elif change == "duplicate_record":
        approval["scoped_history_exceptions"].append(deepcopy(record))
    elif change == "not_object":
        approval["scoped_history_exceptions"] = [None]
    else:
        del record["source_attempts"]
    digest = data_input_sha256(data, config)
    approval["input_sha256"] = record["input_sha256"] = digest
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


@pytest.mark.parametrize("field,changes", [
    ("passing_order", {"passing_order": "1-2"}),
    ("passing_order", {"passing_order_status": "NOT_APPLICABLE"}),
    ("last_3f", {"last_3f": "34.5", "last_3f_status": "OBSERVED", "last_3f_source": "official"}),
    ("last_3f", {"history_surface": "障害"}),
    ("last_3f", {"race_name": "障害未勝利"}),
])
def test_real_observations_and_nonapplicability_cannot_be_overridden(tmp_path, field, changes):
    data, config, approval, record = _fixture(tmp_path, field)
    data["rows"][0].update(changes)
    approval["input_sha256"] = record["input_sha256"] = data_input_sha256(data, config)
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


@pytest.mark.parametrize("field,value", [("value", 0), ("value", "NaN"), ("value", "Infinity"),
    ("value", "nonnumeric"), ("source_distance_m", 600), ("target_distance_m", 603.504),
    ("finding_ids", [])])
def test_sectional_proxy_has_explicit_positive_value_and_unequal_distance_units(tmp_path, field, value):
    data, config, approval, record = _fixture(tmp_path, "last_3f")
    record[field] = value
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


def test_a_parser_fallback_can_be_replaced_but_remains_labeled_as_fallback(tmp_path):
    data, config, approval, record = _fixture(tmp_path, "last_3f")
    data["rows"][0].update(last_3f="36", last_3f_source="fallback", last_3f_status="OBSERVED")
    approval["input_sha256"] = record["input_sha256"] = data_input_sha256(data, config)
    view = model_history_rows(data, _validated(data, config, approval))
    assert view[0]["last_3f_source"] == "fallback"
    assert view[0]["last_3f_status"] == "OBSERVED"
    assert view[0]["approved_history_exceptions"]["last_3f"]["method"] == "imperial_3f_proxy_seconds"


@pytest.mark.parametrize("change", ["report_changed", "raw_changed", "badref", "relative", "no_sha", "no_refs", "missing_ref", "bad_report"])
def test_source_attempt_report_and_every_referenced_raw_are_immutable(tmp_path, change):
    data, config, approval, record = _fixture(tmp_path)
    if change == "report_changed":
        (tmp_path / "attempts.json").write_text("changed")
    elif change == "raw_changed":
        (tmp_path / "original.txt").write_text("changed")
    elif change == "badref":
        record["source_attempts"] = None
    elif change == "relative":
        record["source_attempts"]["path"] = "relative.json"
    elif change == "no_sha":
        record["source_attempts"]["sha256"] = "bad"
    elif change == "no_refs":
        record["source_artifacts"] = []
    elif change == "missing_ref":
        record["source_artifacts"] = record["source_artifacts"][:1]
    else:
        record["source_attempts"] = _save(tmp_path / "attempts.json", {"role": "assistant"})
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


@pytest.mark.parametrize("change", ["bad_value", "bad_distance", "wrong_horse", "missing_split", "not_bound"])
def test_proxy_is_reproduced_from_the_bound_horse_specific_observations(tmp_path, change):
    data, config, approval, record = _fixture(tmp_path, "last_3f")
    path = tmp_path / "observations.json"
    obs = json.loads(path.read_text())
    if change == "bad_value":
        obs[0]["individual_final_3_imperial_furlongs_seconds"] = 35
    elif change == "bad_distance":
        obs[0]["section_distance_m"] = 600
    elif change == "wrong_horse":
        obs[0]["horse_name"] = "B"
    elif change == "missing_split":
        obs[0]["individual_final_3_imperial_furlongs_splits_seconds"].pop()
    else:
        record["proxy_observations"] = _save(tmp_path / "other-observations.json", obs)
    if change != "not_bound":
        ref = _save(path, obs)
        record["source_artifacts"][0] = record["proxy_observations"] = ref
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


@pytest.mark.parametrize("change", ["gate", "rows", "entries", "input_sha", "identity"])
def test_projection_requires_unchanged_successfully_audited_inputs(tmp_path, change):
    data, config, approval, _ = _fixture(tmp_path)
    audit = _validated(data, config, approval)
    if change == "gate":
        audit["gate"] = "BLOCKED"
    elif change == "rows":
        data["rows"][0]["last_3f"] = "99"
    elif change == "entries":
        data["entries"][0]["horse_id"] = "other"
    elif change == "input_sha":
        audit["input_sha256"] = "changed"
    else:
        audit["approved_history_exceptions"][0]["horse_id"] = "changed"
    with pytest.raises(HistoryExceptionError):
        model_history_rows(data, audit)


@pytest.mark.parametrize("value", ["晴", None])
def test_not_used_does_not_hide_observations_or_assign_values(tmp_path, value):
    data, config, approval, record = _fixture(tmp_path, "weather")
    record.update(method="not_used", value=value, finding_ids=[])
    if value is None:
        data["rows"][0]["weather"] = "晴"
        approval["input_sha256"] = record["input_sha256"] = data_input_sha256(data, config)
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


@pytest.mark.parametrize("change", ["horse_id", "date", "field"])
def test_an_unrelated_acquisition_report_cannot_authorize_this_horse(tmp_path, change):
    data, config, approval, record = _fixture(tmp_path)
    path = tmp_path / "attempts.json"
    report = json.loads(path.read_text())
    report["unresolved_observations"][0][change] = "OTHER"
    record["source_attempts"] = _save(path, report)
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


def test_duplicate_matching_proxy_observations_are_ambiguous(tmp_path):
    data, config, approval, record = _fixture(tmp_path, "last_3f")
    path = tmp_path / "observations.json"
    observations = json.loads(path.read_text())
    observations.append(deepcopy(observations[0]))
    ref = _save(path, observations)
    record["source_artifacts"][0] = record["proxy_observations"] = ref
    with pytest.raises(HistoryExceptionError, match="horse and date"):
        validate_history_exceptions(data, config, [approval])


def test_audit_tags_cannot_be_modified_after_a_valid_approval(tmp_path):
    data, config, approval, _ = _fixture(tmp_path)
    audit = _validated(data, config, approval)
    audit["approved_history_exceptions"][0]["value"] = .7
    with pytest.raises(HistoryExceptionError, match="revalidated user evidence"):
        model_history_rows(data, audit)


@pytest.mark.parametrize("mutation", [None, "horse", "run", "date", "field", "reason", "duplicate", "not_list"])
def test_structured_acquisition_scope_is_exact_and_mandatory_when_present(tmp_path, mutation):
    data, config, approval, record = _fixture(tmp_path)
    observation = {k: record[k] for k in ("race_id", "horse_id", "horse_number", "run_index", "date", "field")}
    observation["reason"] = "Primary routes checked; actual corner observation not recovered"
    observations = [observation]
    if mutation == "horse":
        observation["horse_id"] = "OTHER"
    elif mutation == "run":
        observation["run_index"] = "1"
    elif mutation == "date":
        observation["date"] = "2025-08-21"
    elif mutation == "field":
        observation["field"] = "last_3f"
    elif mutation == "reason":
        observation["reason"] = ""
    elif mutation == "duplicate":
        observations *= 2
    elif mutation == "not_list":
        observations = {}
    report_path = tmp_path / "attempts.json"
    report = json.loads(report_path.read_text())
    report["unresolved_observations"] = observations
    record["source_attempts"] = _save(report_path, report)
    if mutation:
        with pytest.raises(HistoryExceptionError, match="scope observations|exact history"):
            validate_history_exceptions(data, config, [approval])
    else:
        assert len(validate_history_exceptions(data, config, [approval])) == 1


@pytest.mark.parametrize("marker", ["approved_history_exceptions", "approved_front_rate"])
def test_raw_model_projection_markers_are_never_treated_as_observations(tmp_path, marker):
    data, config, approval, record = _fixture(tmp_path)
    data["rows"][0][marker] = .5
    approval["input_sha256"] = record["input_sha256"] = data_input_sha256(data, config)
    with pytest.raises(HistoryExceptionError, match="projection markers"):
        validate_history_exceptions(data, config, [approval])
    with pytest.raises(HistoryExceptionError, match="projection markers"):
        validate_history_exceptions(data, config, [])
    with pytest.raises(HistoryExceptionError, match="projection markers"):
        model_history_rows(data, {"gate": "PASS"})


def test_not_used_does_not_reuse_an_unstructured_acquisition_failure(tmp_path):
    data, config, approval, record = _fixture(tmp_path, "weather")
    record.update(method="not_used", value=None, finding_ids=[])
    report_path = tmp_path / "attempts.json"
    report = json.loads(report_path.read_text())
    report.pop("unresolved_observations")
    record["source_attempts"] = _save(report_path, report)
    with pytest.raises(HistoryExceptionError, match="structured acquisition"):
        validate_history_exceptions(data, config, [approval])


def test_source_artifact_set_changes_require_new_explicit_manifest_binding(tmp_path):
    data, config, approval, record = _fixture(tmp_path)
    digest = data_input_sha256(data, config)
    audit = _validated(data, config, approval)
    data["source_audit"] = {"artifacts": [{"path": "new-equivalent-source", "html": "same observation"}]}
    assert data_input_sha256(data, config) == digest
    assert source_manifest_sha256(data) != record["source_manifest_sha256"]
    with pytest.raises(HistoryExceptionError, match="artifact manifest changed"):
        validate_history_exceptions(data, config, [approval])
    with pytest.raises(HistoryExceptionError, match="artifact manifest changed"):
        model_history_rows(data, audit)


def test_missing_source_manifest_cannot_reuse_a_generic_input_approval(tmp_path):
    data, config, approval, record = _fixture(tmp_path)
    record.pop("source_manifest_sha256")
    with pytest.raises(HistoryExceptionError, match="artifact manifest changed"):
        validate_history_exceptions(data, config, [approval])
