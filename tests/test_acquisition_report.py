"""Acquisition recorder/report: honest attempts only, exact-scope observations."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jra_scraper.acquisition_report import (
    AUDITED_FIELDS,
    ROUTE_REGISTRY,
    AcquisitionRecorder,
    build_acquisition_report,
    compute_unresolved_observations,
    iso_date,
    jurisdiction_for_row,
    write_acquisition_bundle,
)
from src.data_integrity import _RAW_FIELDS
from src.history_exceptions import HistoryExceptionError, _attempts, _unavailable_scope


def _ref(path: Path) -> dict:
    return {"path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _row(**overrides) -> dict:
    row = {
        "race_id": "R", "horse_id": "H", "horse_name": "Horse", "horse_number": "1",
        "run_index": "1", "date": "2025年10月5日", "course": "東京",
        "race_name": "テストステークス", "distance": "1600", "history_surface": "芝",
        "track_condition": "良", "position": "1", "time": "1:33.0",
        "last_3f": "34.0", "last_3f_source": "observed", "passing_order": "1-1",
        "popularity": "1", "weight": "55", "jockey": "騎手", "pace": "M",
        "weather": "晴", "odds": "2.0",
    }
    row.update(overrides)
    return row


def test_audited_fields_mirror_data_integrity_raw_fields():
    assert tuple(AUDITED_FIELDS) == tuple(_RAW_FIELDS)


def test_recorder_attempt_shape_and_optional_keys():
    recorder = AcquisitionRecorder()
    recorder.record("jra_horse_history", outcome="fetched", race_id="R", horse_id="H",
                    fields=("time", "last_3f"), artifact_sha256="a" * 64,
                    artifact_path="/tmp/raw/horse_H.html")
    recorder.record("france_galop_tracking", outcome="error", race_id="R", horse_id="H",
                    reason="boom", route_status="degraded")
    attempts = recorder.attempts()
    assert attempts[0] == {
        "race_id": "R", "horse_id": "H", "horse_number": "", "run_index": "",
        "date": "", "fields": ["time", "last_3f"], "route": "jra_horse_history",
        "url": "", "outcome": "fetched", "artifact_sha256": "a" * 64, "reason": "",
        "artifact_path": "/tmp/raw/horse_H.html",
    }
    assert "artifact_path" not in attempts[1]
    assert attempts[1]["route_status"] == "degraded"
    assert "route_status" not in attempts[0]


def test_recorder_routes_aggregate_unique_outcomes():
    recorder = AcquisitionRecorder()
    recorder.record("jra_horse_history", outcome="fetched", race_id="R")
    recorder.record("jra_horse_history", outcome="unavailable", race_id="R")
    recorder.record("jra_horse_history", outcome="fetched", race_id="R")
    recorder.record("jra_race_detail", outcome="cache_hit", race_id="R")
    assert recorder.routes() == [
        {"route": "jra_horse_history", "outcomes": ["fetched", "unavailable"], "attempts": 3},
        {"route": "jra_race_detail", "outcomes": ["cache_hit"], "attempts": 1},
    ]


def test_recorder_artifact_registry_dedupes():
    recorder = AcquisitionRecorder()
    recorder.record("jra_horse_history", outcome="fetched", race_id="R",
                    artifact_sha256="b" * 64, artifact_path="/tmp/raw/horse_H.html",
                    url="https://horse/H")
    recorder.register_artifact("/tmp/raw/france.pdf", "c" * 64,
                               kind="france_tracking_pdf", race_id="R")
    recorder.register_artifact("/tmp/raw/france.pdf", "c" * 64,
                               kind="france_tracking_pdf", race_id="R")
    artifacts = recorder.artifacts_for_race("R")
    assert artifacts == [
        {"path": "/tmp/raw/france.pdf", "sha256": "c" * 64,
         "kind": "france_tracking_pdf", "url": ""},
        {"path": "/tmp/raw/horse_H.html", "sha256": "b" * 64,
         "kind": "jra_horse_history", "url": "https://horse/H"},
    ]
    assert recorder.artifacts_for_race("OTHER") == []


@pytest.mark.parametrize("row,expected", [
    ({"course": "ドーハ", "race_name": "アミールトロフィー"}, "qatar"),
    ({"course": "メイダン", "race_name": "ドバイターフ"}, "uae"),
    ({"course": "シャティン", "race_name": "香港マイル"}, "hong_kong"),
    ({"course": "ロンシャン", "race_name": "凱旋門賞"}, "france"),
    ({"course": "ニューマーケット", "race_name": "イギリス2000ギニー"}, "uk_ireland"),
    ({"course": "サンタアニタ", "race_name": "アメリカンダービー"}, "usa"),
    ({"course": "東京", "race_name": "テストステークス"}, "japan"),
])
def test_jurisdiction_for_row(row, expected):
    assert jurisdiction_for_row(row) == expected


def test_route_registry_registration_status():
    assert ROUTE_REGISTRY["france"]["official_route_registered"] is True
    assert "france_galop_tracking" in ROUTE_REGISTRY["france"]["routes"]
    assert ROUTE_REGISTRY["hong_kong"]["official_route_registered"] is True
    assert "hkjc_sectionals" in ROUTE_REGISTRY["hong_kong"]["routes"]
    for jurisdiction in ("uk_ireland", "qatar", "uae", "saudi_arabia", "usa", "other_overseas"):
        assert ROUTE_REGISTRY[jurisdiction]["official_route_registered"] is False
        assert ROUTE_REGISTRY[jurisdiction]["routes"] == ["jra_horse_history"]


def test_complete_row_has_no_unresolved_observations():
    assert compute_unresolved_observations([_row()], AcquisitionRecorder()) == []


def test_unresolved_observation_iso_date_and_exactly_one_per_field():
    recorder = AcquisitionRecorder()
    observations = compute_unresolved_observations([_row(time="")], recorder)
    assert observations == [{
        "race_id": "R", "horse_id": "H", "horse_number": "1", "run_index": "1",
        "date": "2025-10-05", "field": "time",
        "reason": "No observed value was collected for this field and no "
                  "missing reason or route attempt was recorded for this scope",
    }]


def test_fallback_last_3f_is_unresolved_even_with_a_value():
    observations = compute_unresolved_observations(
        [_row(last_3f="36.0", last_3f_source="fallback")], AcquisitionRecorder())
    assert [obs["field"] for obs in observations] == ["last_3f"]


def test_jump_last_3f_absence_is_not_an_observation():
    row = _row(history_surface="障害", race_name="障害テスト", last_3f="", last_3f_source="")
    assert compute_unresolved_observations([row], AcquisitionRecorder()) == []


def test_not_applicable_field_is_not_an_observation():
    row = _row(last_3f="", last_3f_source="", last_3f_status="NOT_APPLICABLE")
    assert compute_unresolved_observations([row], AcquisitionRecorder()) == []


def test_reason_includes_row_missing_reason_and_route_outcomes():
    recorder = AcquisitionRecorder()
    recorder.record("france_galop_tracking", outcome="identity_source_unavailable",
                    race_id="R", horse_id="H", date="2025年10月5日", fields=("last_3f",))
    row = _row(course="ロンシャン", race_name="凱旋門賞", last_3f="", last_3f_source="",
               last_3f_missing_reason="OFFICIAL_SECTIONAL_CELL_EMPTY")
    observations = compute_unresolved_observations([row], recorder)
    assert len(observations) == 1
    reason = observations[0]["reason"]
    assert "OFFICIAL_SECTIONAL_CELL_EMPTY" in reason
    assert "france_galop_tracking: identity_source_unavailable" in reason


def test_duplicate_rows_dedupe_and_merge_reasons_deterministically():
    recorder = AcquisitionRecorder()
    first = _row(last_3f="", last_3f_source="", last_3f_missing_reason="REASON_B")
    second = _row(last_3f="", last_3f_source="", last_3f_missing_reason="REASON_A")
    observations = compute_unresolved_observations([first, second, dict(first)], recorder)
    assert len(observations) == 1
    assert observations[0]["reason"] == "REASON_A; REASON_B"


def test_qatar_row_reason_states_no_registered_route():
    row = _row(course="ドーハ", race_name="アミールトロフィー", time="")
    observations = compute_unresolved_observations([row], AcquisitionRecorder())
    assert len(observations) == 1
    assert observations[0]["reason"] == (
        "no official source route registered in this system for qatar; "
        "JRA official history is the only route and lists no value")


def test_build_report_refuses_to_fabricate_routes():
    with pytest.raises(ValueError, match="fabricate"):
        build_acquisition_report(race_id="R", rows=[_row()], recorder=AcquisitionRecorder(),
                                 generated_at="2026-11-08T00:00:00+00:00")


def test_build_report_shape_and_scope_strings():
    recorder = AcquisitionRecorder()
    recorder.record("jra_horse_history", outcome="fetched", race_id="R", horse_id="H",
                    fields=AUDITED_FIELDS)
    report = build_acquisition_report(race_id="R", rows=[_row(time="")], recorder=recorder,
                                      generated_at="2026-11-08T00:00:00+00:00")
    assert report["role"] == "data_collector"
    assert report["race_id"] == "R"
    assert report["routes"] == [
        {"route": "jra_horse_history", "outcomes": ["fetched"], "attempts": 1}]
    assert report["unresolved_fields"] == {"time": ["Horse 2025-10-05 run1"]}
    assert report["evidence_files"] == []
    assert [obs["field"] for obs in report["unresolved_observations"]] == ["time"]
    assert "jra_horse_history: fetched" in report["unresolved_observations"][0]["reason"]


def _bundle(tmp_path: Path, evidence_payload: dict | None = None):
    raw = tmp_path / "raw" / "horse_H.html"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("<html>official horse history</html>", encoding="utf-8")
    recorder = AcquisitionRecorder()
    recorder.record("jra_horse_history", outcome="fetched", race_id="R", horse_id="H",
                    fields=AUDITED_FIELDS, artifact_sha256=_ref(raw)["sha256"],
                    artifact_path=str(raw.resolve()), url="https://horse/H")
    report = build_acquisition_report(race_id="R", rows=[_row(passing_order="")],
                                      recorder=recorder,
                                      generated_at="2026-11-08T00:00:00+00:00")
    if evidence_payload is None:
        evidence_payload = {"artifacts": recorder.artifacts_for_race("R")}
    out_dir = tmp_path / "acquisition"
    ref = write_acquisition_bundle(out_dir, report, {"artifact_index.json": evidence_payload})
    return out_dir, ref, recorder


def test_bundle_passes_real_history_exception_validation(tmp_path):
    out_dir, ref, _ = _bundle(tmp_path)
    evidence_ref = _ref(out_dir / "artifact_index.json")
    record = {"race_id": "R", "horse_id": "H", "horse_number": "1", "run_index": "1",
              "date": "2025-10-05", "field": "passing_order",
              "source_attempts": ref, "source_artifacts": [evidence_ref]}
    loaded = _attempts(record)
    assert loaded["role"] == "data_collector"
    assert loaded["evidence_files"] == ["artifact_index.json"]
    _unavailable_scope(record, loaded)  # exact scope is covered: must not raise


def test_bundle_scope_not_in_report_raises(tmp_path):
    out_dir, ref, _ = _bundle(tmp_path)
    record = {"race_id": "R", "horse_id": "H", "horse_number": "1", "run_index": "1",
              "date": "2025-10-05", "field": "time",
              "source_attempts": ref,
              "source_artifacts": [_ref(out_dir / "artifact_index.json")]}
    loaded = _attempts(record)
    with pytest.raises(HistoryExceptionError):
        _unavailable_scope(record, loaded)


def test_bundle_bogus_evidence_reference_fails_validation(tmp_path):
    bogus = {"artifacts": [{"path": str((tmp_path / "raw" / "horse_H.html").resolve()),
                            "sha256": "0" * 64, "kind": "jra_horse_history", "url": ""}]}
    out_dir, ref, _ = _bundle(tmp_path, bogus)
    record = {"race_id": "R", "horse_id": "H", "horse_number": "1", "run_index": "1",
              "date": "2025-10-05", "field": "passing_order",
              "source_attempts": ref,
              "source_artifacts": [_ref(out_dir / "artifact_index.json")]}
    with pytest.raises(HistoryExceptionError):
        _attempts(record)


def test_bundle_writer_rejects_path_traversal_evidence_names(tmp_path):
    with pytest.raises(ValueError, match="plain file name"):
        write_acquisition_bundle(tmp_path / "out", {"role": "data_collector"},
                                 {"../escape.json": {}})


def test_iso_date_normalizes_japanese_and_iso_and_passes_through_the_rest():
    assert iso_date("2025年10月5日") == "2025-10-05"
    assert iso_date("2025-10-05") == "2025-10-05"
    # Unparseable text is passed through unchanged, never guessed at.
    assert iso_date("未定") == "未定"
    assert iso_date("") == ""


def test_attempt_recorded_for_another_race_does_not_support_a_reason():
    recorder = AcquisitionRecorder()
    recorder.record("jra_horse_history", outcome="fetched", race_id="OTHER",
                    horse_id="H", fields=("time",))
    observations = compute_unresolved_observations([_row(time="")], recorder)
    assert observations[0]["reason"] == (
        "No observed value was collected for this field and no "
        "missing reason or route attempt was recorded for this scope")


def test_attempt_recorded_on_another_date_does_not_support_a_reason():
    recorder = AcquisitionRecorder()
    recorder.record("jra_horse_history", outcome="fetched", race_id="R",
                    horse_id="H", date="2024年1月1日", fields=("time",))
    observations = compute_unresolved_observations([_row(time="")], recorder)
    assert observations[0]["reason"] == (
        "No observed value was collected for this field and no "
        "missing reason or route attempt was recorded for this scope")


def test_uk_row_reason_includes_the_registry_reason():
    row = _row(course="ニューマーケット", race_name="イギリス2000ギニー", time="")
    observations = compute_unresolved_observations([row], AcquisitionRecorder())
    assert len(observations) == 1
    reason = observations[0]["reason"]
    assert "no official source route registered in this system for uk_ireland" in reason
    assert "Imperial sectional proxy is research-only" in reason
