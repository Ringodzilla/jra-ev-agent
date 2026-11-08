"""Issue #38 audit-rule extensions.

- Jump histories: an absent final-3F sectional is NOT_APPLICABLE, never a
  required-missing stop and never consumed as a sectional by the model.
- passing_order: a source-bound exception may mark present-but-semantically
  different distance-marker positions as not used for one run.
- last_3f: a metric final-600m proxy binds an official tracking observation
  whose reparse must reproduce the exception value exactly.
- time: no exception method exists; a clock field is recovered only as an
  observed value at collection time.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from unittest.mock import patch

import pytest

from src.acquisition_coverage import field_coverage
from src.data_integrity import audit_collected_data, data_input_sha256
from src.feature_engineering import (DEFAULT_LAST3F, _comparable_last3f,
                                     _history_front_score,
                                     validate_explicit_history_observations)
from src.history_exceptions import (HistoryExceptionError, model_history_rows,
                                    source_manifest_sha256, validate_history_exceptions)
from jra_scraper.foreign_history import parse_metric_600m_observation


@pytest.fixture
def isolated_metric_adapter(monkeypatch):
    # The adapter's own reparse is verified against real HKJC data below;
    # exception-scope tests isolate binding and replay checks.
    monkeypatch.setattr("jra_scraper.foreign_history.parse_metric_600m_observation",
                        lambda item, row: Decimal(str(item["individual_final_600m_seconds"])),
                        raising=False)


def _ref(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return _ref(path)


def _fixture(tmp_path, field="last_3f"):
    raw = tmp_path / "original.txt"
    raw.write_text("Raw official metric tracking observations", encoding="utf-8")
    nested = tmp_path / "extract.txt"
    nested.write_text("source definitions", encoding="utf-8")
    obs = [{"horse_name": "A", "race_date": "2025-08-20", "source": _ref(raw),
            "individual_final_600m_seconds": 34.5, "section_distance_m": 600}]
    observation = _save(tmp_path / "observations.json", obs)
    evidence = _save(tmp_path / "definitions.json", {
        "extracted_path": str(nested.resolve()), "extracted_sha256": _ref(nested)["sha256"]})
    report_data = {"role": "data_collector", "routes": [{"route": "checked"}],
        "unresolved_fields": {"actual_corner_positions": ["A 2025-08-20 Fixture"],
                              "exact_metric600m": ["A 2025-08-20 Fixture"]},
        "evidence_files": ["observations.json", "definitions.json"]}
    report_data["unresolved_observations"] = [{"race_id": "R", "horse_id": "H", "horse_number": "1",
        "run_index": "5", "date": "2025-08-20", "field": field,
        "reason": "Corner observation unavailable; only distance-marker positions exist"}]
    report = _save(tmp_path / "attempts.json", report_data)
    config = [{"race_id": "R", "race_date": "2026-11-01", "track": "東京", "race_number": 11}]
    row = {"race_id": "R", "horse_id": "H", "horse_name": "A", "horse_number": "1", "run_index": "5",
           "date": "2025年8月20日", "last_3f": "", "last_3f_status": "NOT_OBSERVED",
           "last_3f_source": "", "passing_order": "", "passing_order_status": "NOT_OBSERVED",
           "history_surface": "芝"}
    if field == "passing_order":
        row.update(passing_order="4-3-2", passing_order_status="UNVERIFIED",
                   last_3f="34.5", last_3f_status="OBSERVED")
    collected = {"rows": [row], "entries": [{"race_id": "R", "horse_id": "H"}], "race_configs": config}
    digest = data_input_sha256(collected, config)
    record = {k: row[k] for k in ("race_id", "horse_id", "horse_number", "run_index")}
    record.update(date="2025-08-20", field=field,
                  method="metric_600m_proxy_seconds" if field == "last_3f"
                         else "distance_marker_positions_not_used",
                  value=34.5 if field == "last_3f" else None, input_sha256=digest,
                  evidence="User explicitly approved these unavailable history exceptions.",
                  finding_ids=["a" * 24], source_attempts=report,
                  source_artifacts=[observation, evidence], proxy_observations=observation,
                  source_distance_m=600, target_distance_m=600,
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


# --- metric final-600m proxy -------------------------------------------------

def test_metric_proxy_projects_value_without_touching_source(tmp_path, isolated_metric_adapter):
    data, config, approval, _ = _fixture(tmp_path, "last_3f")
    before = deepcopy(data)
    audit = _validated(data, config, approval)
    projected = model_history_rows(data, audit)
    assert data == before
    assert projected[0]["last_3f"] == "34.5"
    assert projected[0]["last_3f_status"] == "NOT_OBSERVED"
    assert projected[0]["approved_history_exceptions"]["last_3f"]["method"] == "metric_600m_proxy_seconds"
    assert _comparable_last3f(projected[0]) == 34.5


@pytest.mark.parametrize("change", ["imperial_distance", "target_not_600", "value_mismatch",
                                    "wrong_horse", "tampered_observations", "unapproved",
                                    "foreign_finding_ids", "observed_row"])
def test_metric_proxy_fails_closed(tmp_path, monkeypatch, change, isolated_metric_adapter):
    data, config, approval, record = _fixture(tmp_path, "last_3f")
    if change == "imperial_distance":
        record["source_distance_m"] = 603.504
    elif change == "target_not_600":
        record["target_distance_m"] = 603.504
    elif change == "value_mismatch":
        monkeypatch.setattr("jra_scraper.foreign_history.parse_metric_600m_observation",
                            lambda item, row: Decimal("99"), raising=False)
    elif change == "wrong_horse":
        path = tmp_path / "observations.json"
        obs = json.loads(path.read_text())
        obs[0]["horse_name"] = "B"
        ref = _save(path, obs)
        record["source_artifacts"][0] = record["proxy_observations"] = ref
    elif change == "tampered_observations":
        (tmp_path / "observations.json").write_text("tampered")
    elif change == "unapproved":
        approval["approved_by"] = "assistant"
    elif change == "foreign_finding_ids":
        record["finding_ids"] = ["b" * 24]
    else:
        data["rows"][0].update(last_3f="34.0", last_3f_source="official", last_3f_status="OBSERVED")
        approval["input_sha256"] = record["input_sha256"] = data_input_sha256(data, config)
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


# --- metric parser against a real official-source fixture --------------------

HKJC = '''<p>Meeting Date:26/04/2026, Sha Tin</p><div id="Race9">
<div class="Race"><p class="f_fl"><span>Group One - 2000M - Turf - A - Good To Firm</span><span>THE FWD QEII CUP</span></p></div>
<table class="race_table"><tbody><tr><td>2</td><td>1</td><td><a>MASQUERADE BALL (L919)</a></td>
<td><p class="f_clear"><span class="f_fl">7</span></p><p>26.93</p></td>
<td><p class="f_clear"><span class="f_fl">8</span></p><p>25.03</p></td>
<td><p class="f_clear"><span class="f_fl">8</span></p><p>24.25</p></td>
<td><p class="f_clear"><span class="f_fl">8</span></p><p class="sectional_200">22.92
<span class="color_blue2"><span>11.63</span><span>11.29</span></span></p></td>
<td><p class="f_clear"><span class="f_fl">2</span></p><p class="sectional_200">21.67
<span class="color_blue2"><span>10.72</span><span>10.95</span></span></p></td>
<td><img/></td><td>2:00.80</td></tr></tbody></table></div>'''


def _hkjc_identity_source():
    text = '<div class="cell date">2026年4月26日 シャティン競馬場</div><div class="cell course">2,000メートル</div><h2>クイーンエリザベス2世カップ</h2>'
    text += ('<table><tr><td class="place">2</td><td class="num"><span class="cell num">1</span></td>'
             '<td class="horse"><a href="/JRADB/accessU.html?CNAME=official-jra-id">マスカレードボール</a></td></tr></table>')
    return {'html': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'url': 'https://www.jra.go.jp/JRADB/result'}


def _metric_item(tmp_path, html=HKJC, kind="hkjc_history_sectionals"):
    artifact = {'kind': kind, 'url': 'https://racing.hkjc.com/result', 'race_id': 'target',
                'horse_id': 'official-jra-id', 'horse_name': 'マスカレードボール',
                'date': '2026-04-26', 'official_horse_name': 'MASQUERADE BALL', 'race_number': 9,
                'identity_source': _hkjc_identity_source(), 'html': html}
    source = _save(tmp_path / "artifact.json", artifact)
    item = {"horse_name": "マスカレードボール", "race_date": "2026-04-26",
            "individual_final_600m_seconds": 32.96, "section_distance_m": 600, "source": source}
    row = {"horse_name": "マスカレードボール", "date": "2026年4月26日", "distance": "2000"}
    return item, row


def test_metric_parser_reparses_bound_hkjc_artifact(tmp_path):
    item, row = _metric_item(tmp_path)
    assert parse_metric_600m_observation(item, row) == Decimal("32.96")


@pytest.mark.parametrize("change", ["tampered_split", "wrong_kind", "wrong_row_horse", "changed_file"])
def test_metric_parser_fails_closed(tmp_path, change):
    if change == "tampered_split":
        item, row = _metric_item(tmp_path, html=HKJC.replace("11.63", "11.64"))
    elif change == "wrong_kind":
        item, row = _metric_item(tmp_path, kind="nar_history_result")
    elif change == "wrong_row_horse":
        item, row = _metric_item(tmp_path)
        row["horse_name"] = "別馬"
    else:
        item, row = _metric_item(tmp_path)
        (tmp_path / "artifact.json").write_text("changed after binding")
    with pytest.raises(ValueError):
        parse_metric_600m_observation(item, row)


# --- distance-marker passing_order exception ---------------------------------

def test_distance_marker_positions_are_not_consumed_as_corners(tmp_path):
    data, config, approval, _ = _fixture(tmp_path, "passing_order")
    before = deepcopy(data)
    audit = _validated(data, config, approval)
    projected = model_history_rows(data, audit)
    assert data == before
    assert projected[0]["passing_order"] == "4-3-2"  # observed value preserved
    assert projected[0]["passing_order_not_used"] is True
    assert _history_front_score(projected[0]) == 0.5
    validate_explicit_history_observations(projected[0])


@pytest.mark.parametrize("change", ["assigns_value", "absent_order", "observed_corners",
                                    "no_findings", "unapproved"])
def test_distance_marker_exception_fails_closed(tmp_path, change):
    data, config, approval, record = _fixture(tmp_path, "passing_order")
    if change == "assigns_value":
        record["value"] = 0.5
    elif change == "absent_order":
        data["rows"][0]["passing_order"] = ""
    elif change == "observed_corners":
        data["rows"][0]["passing_order_status"] = "OBSERVED"
    elif change == "no_findings":
        record["finding_ids"] = []
    else:
        approval["status"] = "PROPOSED"
    if change in {"absent_order", "observed_corners"}:
        approval["input_sha256"] = record["input_sha256"] = data_input_sha256(data, config)
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


def test_distance_marker_marker_cannot_be_smuggled_into_source_rows(tmp_path):
    data, config, approval, _ = _fixture(tmp_path, "passing_order")
    data["rows"][0]["passing_order_not_used"] = True
    approval["input_sha256"] = data_input_sha256(data, config)
    with pytest.raises(HistoryExceptionError, match="projection markers"):
        validate_history_exceptions(data, config, [approval])
    with pytest.raises(HistoryExceptionError, match="projection markers"):
        model_history_rows(data, {"gate": "PASS"})


def test_front_score_rejects_marker_exception_without_the_model_marker(tmp_path):
    data, config, approval, _ = _fixture(tmp_path, "passing_order")
    audit = _validated(data, config, approval)
    projected = model_history_rows(data, audit)
    del projected[0]["passing_order_not_used"]
    with pytest.raises(Exception, match="distance-marker"):
        _history_front_score(projected[0])


# --- time has no exception method ---------------------------------------------

def test_time_cannot_be_excepted(tmp_path):
    data, config, approval, record = _fixture(tmp_path, "passing_order")
    record.update(field="time", method="not_used", value=None)
    report_path = tmp_path / "attempts.json"
    report = json.loads(report_path.read_text())
    report["unresolved_observations"][0]["field"] = "time"
    record["source_attempts"] = _save(report_path, report)
    with pytest.raises(HistoryExceptionError):
        validate_history_exceptions(data, config, [approval])


# --- jump sectional non-applicability ------------------------------------------

def _audit_fixture(row, semantic_state, semantic_status):
    coverage = dict(status='PASS', counts={}, records=[
        dict(race_id='r', horse_id='h', horse_number='1', run_index='1', date='2026-09-01',
             field='last_3f', state=semantic_state, semantic_status=semantic_status)])
    source = dict(status='PASS', mismatches=[], acquisition_coverage=coverage,
                  detected_imputations=[])
    data = dict(rows=[row], entries=[dict(row, assigned_weight='55', current_odds='4',
                                          current_popularity='3')], quality_report={})
    config = {'r': {'career_starts_by_horse_number': {'1': 1}}}
    return data, config, source


def _flat_row(**changes):
    row = dict(race_id='r', horse_id='h', horse_number='1', run_index='1',
               date='2026-09-01', target_race_date='2026-11-01', course='東京',
               race_name='Flat race', distance='2000', history_surface='芝',
               track_condition='良', position='5', time='120', last_3f='34',
               passing_order='3-3', popularity='3', weight='57', jockey='J',
               pace='36', weather='晴', odds='3.6')
    row.update(changes)
    return row


def _approval_for(result):
    return dict(approved_by='user', status='APPROVED', race_id='r', purpose='prediction',
                evidence='Explicit approval', input_sha256=result['input_sha256'],
                finding_ids=[f['finding_id'] for f in result['findings']])


def test_jump_sectional_absence_is_not_a_required_stop():
    row = _flat_row(race_name='障害未勝利', history_surface='障害', distance='2880',
                    last_3f='', last_3f_status='UNVERIFIED')
    data, config, source = _audit_fixture(row, 'SOURCE_FIELD_MISSING', 'UNVERIFIED')
    with patch('src.data_integrity.verify_source_artifacts', return_value=source):
        result = audit_collected_data(data, config)
        assert not any(f['required_missing'] for f in result['findings'])
        assert any(f['method'] == 'neutralize_jump_sectional_36' for f in result['findings'])
        assert result['gate'] == 'BLOCKED_UNAPPROVED_IMPUTATION'
        assert audit_collected_data(data, config, [_approval_for(result)])['gate'] == 'PASS'
        assert next(m for m in result['missing_fields'] if m['field'] == 'last_3f')['state'] == 'NOT_APPLICABLE'


@pytest.mark.parametrize("changes", [
    dict(last_3f='34.0', last_3f_status='OBSERVED'),   # claimed observed sectional
    dict(last_3f='14.0', last_3f_status='NOT_APPLICABLE'),  # average-1F figure in the cell
])
def test_jump_sectional_claims_still_stop(changes):
    row = _flat_row(race_name='障害未勝利', history_surface='障害', distance='2880', **changes)
    data, config, source = _audit_fixture(row, 'OBSERVED', None)
    with patch('src.data_integrity.verify_source_artifacts', return_value=source):
        result = audit_collected_data(data, config)
        assert any(f['field'] == 'last_3f' and f['required_missing'] for f in result['findings'])
        assert result['gate'] == 'BLOCKED_MISSING_DATA'


def test_flat_sectional_absence_still_stops():
    row = _flat_row(last_3f='', last_3f_status='NOT_OBSERVED')
    data, config, source = _audit_fixture(row, 'SOURCE_FIELD_MISSING', 'NOT_OBSERVED')
    with patch('src.data_integrity.verify_source_artifacts', return_value=source):
        result = audit_collected_data(data, config)
        assert any(f['field'] == 'last_3f' and f['required_missing'] for f in result['findings'])
        assert result['gate'] == 'BLOCKED_MISSING_DATA'


def test_coverage_marks_jump_sectional_not_applicable():
    row = _flat_row(race_name='障害未勝利', history_surface='障害', last_3f='',
                    last_3f_status='UNVERIFIED')
    original = dict(row, _source_artifact={'path': 'x', 'kind': 'horse_history'})
    record = field_coverage(row, row, 'last_3f', [original], {'rows': [row]})
    assert record['state'] == 'NOT_APPLICABLE' and record['semantic_status'] == 'NOT_APPLICABLE'


def test_coverage_does_not_call_a_units_conflict_not_applicable():
    row = _flat_row(race_name='障害未勝利', history_surface='障害', last_3f='',
                    last_3f_status='UNVERIFIED',
                    last_3f_missing_reason='CONFLICTING_SECTIONAL_UNITS')
    original = dict(row, _source_artifact={'path': 'x', 'kind': 'horse_history'})
    record = field_coverage(row, row, 'last_3f', [original], {'rows': [row]})
    assert record['state'] == 'SOURCE_FIELD_MISSING'


@pytest.mark.parametrize("changes", [
    dict(last_3f_status='UNVERIFIED'), dict(last_3f_status='NOT_OBSERVED'),
    dict(last_3f_status='NOT_APPLICABLE'),
])
def test_feature_layer_never_consumes_a_jump_sectional(changes):
    row = _flat_row(race_name='障害未勝利', history_surface='障害', last_3f='', **changes)
    assert _comparable_last3f(row) == DEFAULT_LAST3F


def test_feature_layer_uses_proven_nonapplicability_without_surface_markers():
    row = _flat_row(race_name='Result race', history_surface='', last_3f='',
                    last_3f_status='NOT_APPLICABLE', average_1f='14',
                    last_3f_missing_reason='RESULT_COLUMN_IS_AVERAGE_1F')
    assert _comparable_last3f(row) == DEFAULT_LAST3F
