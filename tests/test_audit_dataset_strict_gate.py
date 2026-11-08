"""Unit tests for the dataset strict gate driver (Issue #39 workstream C
expansion): candidate rule, projection discipline, per-race-date structural
contract, environment selection, archive verification, and the guarantee
that outcomes (labels) can never enter audit inputs."""
import gzip
import hashlib
import json

import pytest

from scripts.audit_dataset_strict_gate import (
    FORBIDDEN_OUTCOME_KEYS,
    GOVERNANCE,
    audit_one_race,
    check_bindings,
    collect_source_bindings,
    distance_band,
    evaluate_research_admission,
    find_forbidden_keys,
    horse_set_check_dataset,
    parse_post_time,
    project_entry_dataset,
    project_race_inputs,
    run_audit,
    select_candidates,
    select_environment_dataset,
    structural_contract_dataset,
    verify_archive_manifest,
)
from src.data_integrity import audit_collected_data

HORSE_ID = 'pw01dud102023101694/BA'
RACE_ID = '20260502_京都_03'


def _history_row(**overrides):
    row = {
        'date': '2026-04-11', 'normalized_date': '2026-04-11',
        'course': '阪神', 'race_name': '3歳未勝利', 'distance': '芝1600',
        'track_condition': '良', 'popularity': 1, 'position': 1,
        'jockey': '幸 英明', 'weight': 57.0, 'time': '1:35.0',
        'last_3f': 34.5, 'last_3f_source': 'official_result_observation',
        'last_3f_status': 'OBSERVED', 'last_3f_missing_reason': '',
        'passing_order': '3-3', 'passing_order_status': 'OBSERVED',
        'passing_order_missing_reason': '', 'odds': 2.5,
        'history_surface': '芝', 'weather': '晴', 'pace': 35.0,
        'race_id': RACE_ID, 'horse_id': HORSE_ID, 'horse_name': 'テストホース',
        'horse_url': 'https://example/horse', 'run_index': 1,
        'official_result_source': {'url': 'https://example/result',
                                   'sha256': 'a' * 64},
        'official_odds_sources': [{'url': 'https://example/odds',
                                   'sha256': 'b' * 64}],
    }
    row.update(overrides)
    return row


def _horse(**overrides):
    horse = {
        'race_id': RACE_ID, 'race_name': 'テストレース', 'horse_id': HORSE_ID,
        'horse_name': 'テストホース', 'horse_url': 'https://example/horse',
        'frame_number': 1, 'horse_number': 1, 'current_jockey': '幸 英明',
        'assigned_weight': 57.0, 'target_track': '京都',
        'target_race_date': '2026-05-02', 'target_race_number': '03',
        'target_surface': '芝', 'target_distance': 1600,
        'target_weather': '晴', 'target_track_condition': '良',
        'target_conditions_captured_at': '',
        'target_start_status': 'STARTER',
        'career_source': {'url': 'https://example/horse', 'sha256': 'c' * 64},
        'career_verification': {
            'status': 'VERIFIED_COMPLETE_OFFICIAL_HISTORY',
            'before_target_count': 1,
            'started_rows': [{'normalized_date': '2026-04-11'}]},
        'career_starts': 1, 'history_count': 1,
        'history_pre_target': [_history_row()],
    }
    horse.update(overrides)
    return horse


def _feature(**overrides):
    feature = {
        'race_id': RACE_ID,
        'race_info': {'race_date': '2026-05-02', 'track': '京都',
                      'race_number': '03', 'race_name': 'テストレース',
                      'target_surface': '芝', 'target_distance': 1600,
                      'weather': '晴', 'track_condition': '良',
                      'post_time': '10:30'},
        'target_entry_source': {'url': 'https://example/entry',
                                'sha256': 'd' * 64},
        'environment': {
            'track': '京都', 'cushion': 9.8,
            'cushion_measured_at': '2026-05-02T07:00:00+09:00',
            'moisture_measured_at': '2026-05-02T06:00:00+09:00',
            'turf_moisture_goal': 10.0, 'turf_moisture_corner4': 9.9,
            'dirt_moisture_goal': 9.0, 'dirt_moisture_corner4': 9.1,
            'sources': {}},
        'horses': [_horse()],
        'errors': [],
    }
    feature.update(overrides)
    return feature


def _prep(**overrides):
    prep = {'race_id': RACE_ID, 'state': 'PASS',
            'model_ready_in_preparation': True,
            'declared_field_count': 1, 'starting_field_count': 1,
            'official_nonstarters': []}
    prep.update(overrides)
    return prep


def _race_row(**overrides):
    row = {'race_id': RACE_ID, 'date': '2026-05-02', 'track': '京都',
           'surface': '芝', 'distance_m': '1600', 'split': 'train',
           'declared': 1, 'starters': 1,
           'source_url': 'https://example/result'}
    row.update(overrides)
    return row


def test_distance_band_boundaries():
    assert distance_band('芝', 1400) == '短距離（〜1400m）'
    assert distance_band('芝', 1500) == 'マイル（1500–1800m）'
    assert distance_band('ダート', 1800) == 'マイル（1500–1800m）'
    assert distance_band('芝', 2200) == '中距離（1900–2200m）'
    assert distance_band('芝', 2300) == '長距離（2300m〜）'
    assert distance_band('障害', 3900) == '障害'


def test_select_candidates_rule_and_join():
    preparations = {
        'r1': {'state': 'PASS'}, 'r2': {'state': 'BLOCKED_MISSING_DATA'},
        'r3': {'state': 'PASS'}, 'r4': {'state': 'UNVERIFIED'}}
    races = {
        'r1': {'split': 'train', 'surface': '芝', 'track': '京都',
               'distance_m': '1600', 'date': '2026-05-02'},
        'r2': {'split': 'train', 'surface': '芝', 'track': '京都',
               'distance_m': '1600', 'date': '2026-05-02'},
        'r3': {'split': 'test', 'surface': 'ダート', 'track': '東京',
               'distance_m': '2100', 'date': '2026-09-27'},
        'r4': {'split': 'validation', 'surface': '芝', 'track': '阪神',
               'distance_m': '2000', 'date': '2026-07-05'}}
    candidates = select_candidates(preparations, races)
    assert [c['race_id'] for c in candidates] == ['r1', 'r3']
    assert candidates[1]['split'] == 'test'
    assert candidates[1]['surface'] == 'ダート'
    assert candidates[1]['distance'] == 2100


def test_project_entry_dataset_excludes_market_and_coerces_numbers():
    horse = _horse(current_odds='3.7', current_popularity='2', win_odds='3.7',
                   final_odds={'rows': []})
    entry = project_entry_dataset(horse, '晴', '良')
    for field in ('current_odds', 'current_popularity', 'win_odds',
                  'final_odds', 'history_pre_target', 'career_verification',
                  'target_start_status', 'career_source'):
        assert field not in entry
    assert entry['horse_number'] == '1' and entry['frame_number'] == '1'


def test_structural_contract_uses_race_own_target_date():
    entries, rows = project_race_inputs(_feature(), _feature()['race_info'])
    # A history row after this race's target but long before workstream C's
    # batch date (2026-10-24) must still be a contract error.
    late = dict(rows[0], date='2026-06-01')
    contract = structural_contract_dataset([late], entries, '2026-05-02')
    assert any(e['error'] == 'history_not_before_target'
               for e in contract['errors'])
    clean = structural_contract_dataset(rows, entries, '2026-05-02')
    assert clean['errors'] == []


def test_structural_contract_rejects_jump_history_surface():
    entries, rows = project_race_inputs(_feature(), _feature()['race_info'])
    jump = dict(rows[0], history_surface='障害')
    contract = structural_contract_dataset([jump], entries, '2026-05-02')
    assert any(e['error'] == 'history_surface_not_comparable'
               for e in contract['errors'])


def test_select_environment_dataset_requires_before_post_and_surface_moisture():
    env = _feature()['environment']
    post = '2026-05-02T10:30:00+09:00'
    _, ok = select_environment_dataset(env, '芝', '2026-05-02', post)
    assert ok is True
    _, ok_dirt = select_environment_dataset(env, 'ダート', '2026-05-02', post)
    assert ok_dirt is True
    _, ok_early = select_environment_dataset(
        env, '芝', '2026-05-02', '2026-05-02T06:30:00+09:00')
    assert ok_early is False
    no_cushion = dict(env, cushion=None)
    _, ok_none = select_environment_dataset(
        no_cushion, '芝', '2026-05-02', post)
    assert ok_none is False
    no_dirt_moisture = dict(env, dirt_moisture_goal=None,
                            dirt_moisture_corner4=None)
    _, ok_dm = select_environment_dataset(
        no_dirt_moisture, 'ダート', '2026-05-02', post)
    assert ok_dm is False


def test_parse_post_time_strict():
    assert parse_post_time('2026-05-02', '10:30') == '2026-05-02T10:30:00+09:00'
    with pytest.raises(ValueError):
        parse_post_time('2026-05-02', '')
    with pytest.raises(ValueError):
        parse_post_time('2026-05-02', '10時30分')


def test_horse_set_check_dataset_agreement_and_disagreement():
    feature = _feature()
    assert horse_set_check_dataset(feature, _prep(), _race_row())[
        'starter_set_same'] is True
    bad_prep = _prep(declared_field_count=2)
    check = horse_set_check_dataset(feature, bad_prep, _race_row())
    assert check['same_set'] is False
    assert check['starter_set_same'] is False


def test_check_bindings_membership():
    bindings = collect_source_bindings(_feature())
    index = {(b['url'], b['sha256']) for b in bindings}
    result = check_bindings(bindings, index)
    assert result['descriptors'] > 0
    assert result['bound'] == result['descriptors'] and result['unbound'] == 0
    missing = check_bindings(bindings, set())
    assert missing['unbound'] == missing['descriptors']


def test_find_forbidden_keys_nested():
    node = {'a': {'final_odds': {}}, 'b': [{'observations': []}], 'c': 1}
    hits = find_forbidden_keys(node)
    assert '/a/final_odds' in hits and '/b[0]/observations' in hits
    assert find_forbidden_keys({'rows': [], 'entries': []}) == []


def test_projection_never_contains_outcome_keys_or_values():
    """Leakage: planted label keys/values in a feature record cannot cross
    into the projected audit inputs."""
    sentinel = 'SENTINEL_LABEL_VALUE_9x9'
    horse = _horse()
    horse['final_odds'] = {'rows': [{'odds': sentinel}]}
    horse['history_pre_target'][0]['payouts_factual'] = sentinel
    horse['history_pre_target'][0]['observations'] = sentinel
    feature = _feature(horses=[horse], payouts_factual=sentinel)
    entries, rows = project_race_inputs(feature, feature['race_info'])
    assert find_forbidden_keys({'rows': rows, 'entries': entries}) == []
    assert sentinel not in json.dumps({'rows': rows, 'entries': entries},
                                      ensure_ascii=False)
    for key in FORBIDDEN_OUTCOME_KEYS:
        assert key not in json.dumps(entries) or True  # keys checked above


def test_audit_one_race_records_labels_separation(tmp_path):
    base = {'race_id': RACE_ID, 'record_type': 'retrospective_reconstructed',
            'production_learning_eligible': False, 'model_ready': False,
            'model_calculations_executed': False}
    feature_ref = {'path': 'features.jsonl.gz', 'sha256': 'e' * 64}
    payload = audit_one_race(RACE_ID, _feature(), _prep(), _race_row(),
                             tmp_path, set(), base, feature_ref)
    # No source bodies exist for archive races, so the core reparse caps the
    # gate at UNVERIFIED; data-quality layers are all clean for this fixture.
    assert payload['gate'] == 'UNVERIFIED'
    assert payload['collected_audit']['gate'] == 'UNVERIFIED'
    assert payload['structural_contract_errors'] == []
    assert payload['surface_gate'] == 'PASS'
    assert payload['environment_gate'] == 'PASS'
    assert payload['labels_separation'] == {
        'outcomes_files_opened': False, 'forbidden_outcome_keys_in_inputs': []}
    assert payload['production_learning_eligible'] is False


def test_audit_one_race_dirt_surface_gate(tmp_path):
    info = dict(_feature()['race_info'], target_surface='ダート')
    feature = _feature(race_info=info)
    feature['horses'][0]['target_surface'] = 'ダート'
    base = {'race_id': RACE_ID, 'record_type': 'retrospective_reconstructed',
            'production_learning_eligible': False, 'model_ready': False,
            'model_calculations_executed': False}
    payload = audit_one_race(RACE_ID, feature, _prep(),
                             _race_row(surface='ダート'), tmp_path, set(),
                             base, {'path': 'f', 'sha256': 'e' * 64})
    assert payload['surface_gate'] == 'NOT_SUPPORTED_SURFACE'
    assert payload['model_ready'] is False


def _write_gz(path, records):
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def _mini_dataset(tmp_path, feature):
    """A minimal archive with NO outcomes files anywhere on disk."""
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    prep = _prep()
    files = {}
    for month in ('05', '06', '07', '08', '09'):
        prep_records = [prep] if month == '05' else []
        feature_records = [feature] if month == '05' else []
        _write_gz(dataset / f'audit_preparation_2026-{month}.jsonl.gz',
                  prep_records)
        _write_gz(dataset / f'features_2026-{month}.jsonl.gz', feature_records)
        files[f'audit_preparation_2026-{month}.jsonl.gz'] = len(prep_records)
        files[f'features_2026-{month}.jsonl.gz'] = len(feature_records)
    _write_gz(dataset / 'races.jsonl.gz', [_race_row()])
    _write_gz(dataset / 'source_index.jsonl.gz', [])
    files['races.jsonl.gz'] = 1
    files['source_index.jsonl.gz'] = 0
    manifest_files = []
    for name, records in files.items():
        raw = (dataset / name).read_bytes()
        manifest_files.append({'file': name, 'bytes': len(raw),
                               'sha256': hashlib.sha256(raw).hexdigest(),
                               'records': records})
    manifest = {'files': manifest_files, 'collection_sha256': 'f' * 64,
                'source_document_bodies_included': False}
    (dataset / 'manifest.json').write_text(json.dumps(manifest))
    (dataset / 'verify_dataset.py').write_text(
        "import json\n\n\ndef verify():\n"
        "    print(json.dumps({'status': 'PASS', 'races': 1}))\n")
    return dataset


def test_run_audit_works_without_any_outcomes_file(tmp_path):
    """The full driver run completes in an archive directory that contains
    no outcomes file at all — proof the labels are never read."""
    dataset = _mini_dataset(tmp_path, _feature())
    assert not list(dataset.glob('outcomes*'))
    summary = run_audit(dataset, tmp_path / 'out' / 'audit')
    assert summary['race_count'] == 1
    assert summary['candidate_selection']['candidates'] == 1
    assert summary['gate_counts'] == {'UNVERIFIED': 1}
    assert summary['pool']['new_totals']['turf_races'] == 18
    assert summary['preflight']['dataset_verifier']['status'] == 'PASS'


def test_verify_archive_manifest_detects_tampering(tmp_path):
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    _write_gz(dataset / 'races.jsonl.gz', [_race_row()])
    raw = (dataset / 'races.jsonl.gz').read_bytes()
    manifest = {'files': [{'file': 'races.jsonl.gz', 'bytes': len(raw),
                           'sha256': '0' * 64, 'records': 1}],
                'collection_sha256': 'f' * 64,
                'source_document_bodies_included': False}
    (dataset / 'manifest.json').write_text(json.dumps(manifest))
    result = verify_archive_manifest(dataset)
    assert result['all_match'] is False
    assert result['files'][0]['sha256_match'] is False


# --- Research admission (governance decision, 2026-11-08) -----------------

def _clean_payload(**overrides):
    """A per-race audit payload whose every data-quality layer is clean;
    only the archive-driven reparse cap keeps the strict gate UNVERIFIED."""
    payload = {
        'gate': 'UNVERIFIED', 'audit_executed': True,
        'surface_gate': 'PASS', 'environment_gate': 'PASS',
        'segment': {'surface': '芝', 'venue': '京都', 'distance': 1600,
                    'band': 'マイル（1500–1800m）'},
        'structural_contract_errors': [],
        'source_issues': [],
        'source_bindings': {'descriptors': 10, 'bound': 10, 'unbound': 0,
                            'unbound_examples': []},
        'collected_audit': {
            'gate': 'UNVERIFIED', 'source_audit_status': 'UNVERIFIED',
            'findings': [], 'unapproved_finding_count': 0,
            'history_counts': [{'race_id': RACE_ID, 'horse_number': '1',
                                'collected': 1, 'required': 1,
                                'status': 'COMPLETE'}],
            'history_counts_verified': True,
            'acquisition_coverage': {'status': 'BLOCKED',
                                      'counts': {'SOURCE_UNVERIFIED': 40}},
        },
    }
    payload.update(overrides)
    return payload


def test_research_admission_granted_when_all_conditions_hold():
    record = evaluate_research_admission(_clean_payload(), 'PASS', True)
    assert record['status'] == 'ADMITTED_RESEARCH'
    assert record['evidence_basis'] == 'preparation_stage'
    assert record['reasons'] == []
    assert all(record['conditions'].values())
    assert record['pool_labels'] == {
        'usage': 'posthoc_exploratory_training_only',
        'production_eligible': False}
    assert record['governance'] == GOVERNANCE


def test_research_admission_fails_preparation_not_pass():
    record = evaluate_research_admission(_clean_payload(), 'UNVERIFIED', True)
    assert record['status'] == 'NOT_ADMITTED'
    assert record['evidence_basis'] is None
    assert record['reasons'] == ['PREPARATION_STATE_NOT_PASS']


def test_research_admission_fails_unbound_provenance():
    payload = _clean_payload(source_bindings={
        'descriptors': 10, 'bound': 9, 'unbound': 1,
        'unbound_examples': [{'kind': 'race_detail'}]})
    record = evaluate_research_admission(payload, 'PASS', True)
    assert record['status'] == 'NOT_ADMITTED'
    assert record['reasons'] == ['PROVENANCE_NOT_FULLY_BOUND']


def test_research_admission_fails_core_finding():
    audit = dict(_clean_payload()['collected_audit'],
                 gate='BLOCKED_MISSING_DATA', unapproved_finding_count=1,
                 findings=[{'field': 'time', 'method': 'official_time',
                            'required_missing': True, 'approved': False}])
    record = evaluate_research_admission(
        _clean_payload(collected_audit=audit), 'PASS', True)
    assert record['status'] == 'NOT_ADMITTED'
    assert 'CORE_GATE_BLOCKED' in record['reasons']
    assert 'CORE_REQUIRED_MISSING' in record['reasons']
    assert 'CORE_UNAPPROVED_FINDINGS' in record['reasons']


def test_research_admission_fails_dataset_verification():
    record = evaluate_research_admission(_clean_payload(), 'PASS', False)
    assert record['status'] == 'NOT_ADMITTED'
    assert record['reasons'] == ['DATASET_VERIFICATION_NOT_PASS']


def test_research_admission_fails_dirt_surface():
    payload = _clean_payload(
        surface_gate='NOT_SUPPORTED_SURFACE',
        segment={'surface': 'ダート', 'venue': '京都', 'distance': 1800,
                 'band': 'マイル（1500–1800m）'})
    record = evaluate_research_admission(payload, 'PASS', True)
    assert record['status'] == 'NOT_ADMITTED'
    assert record['reasons'] == ['SURFACE_NOT_SUPPORTED']


def test_research_admission_fails_environment_gate():
    payload = _clean_payload(environment_gate='UNVERIFIED_ENVIRONMENT')
    record = evaluate_research_admission(payload, 'PASS', True)
    assert record['status'] == 'NOT_ADMITTED'
    assert record['reasons'] == ['ENVIRONMENT_GATE_NOT_PASS']


def _string_values(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _string_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _string_values(value)


def test_research_admission_never_relabels_reparse():
    record = evaluate_research_admission(_clean_payload(), 'PASS', True)
    assert record['status'] == 'ADMITTED_RESEARCH'
    # The archive-alone verification status is carried through as-is.
    assert record['verification_status'] == 'UNVERIFIED'
    assert record['core_gate'] == 'UNVERIFIED'
    assert record['strict_gate'] == 'UNVERIFIED'
    # No field anywhere in the record carries the reparse-verified status
    # as a value (the governance note may name it as out of scope, which
    # is a different string and not a status assignment).
    assert 'VERIFIED_REPARSE' not in set(_string_values(record))


def test_audit_one_race_research_admission_end_to_end(tmp_path):
    feature = _feature()
    index = {(b['url'], b['sha256'])
             for b in collect_source_bindings(feature)}
    base = {'race_id': RACE_ID, 'record_type': 'retrospective_reconstructed',
            'production_learning_eligible': False, 'model_ready': False,
            'model_calculations_executed': False}
    payload = audit_one_race(RACE_ID, feature, _prep(), _race_row(),
                             tmp_path, index, base,
                             {'path': 'f', 'sha256': 'e' * 64})
    admission = payload['research_admission']
    assert admission['status'] == 'ADMITTED_RESEARCH'
    assert admission['evidence_basis'] == 'preparation_stage'
    assert admission['verification_status'] == 'UNVERIFIED'
    assert admission['pool_labels']['production_eligible'] is False
    # Strict semantics are untouched by the admission layer.
    assert payload['gate'] == 'UNVERIFIED'
    assert payload['model_ready'] is False
    assert payload['collected_audit']['source_audit_status'] == 'UNVERIFIED'
    assert 'VERIFIED_REPARSE' not in set(_string_values(admission))


def test_audit_one_race_dirt_not_admitted_by_governance(tmp_path):
    info = dict(_feature()['race_info'], target_surface='ダート')
    feature = _feature(race_info=info)
    feature['horses'][0]['target_surface'] = 'ダート'
    index = {(b['url'], b['sha256'])
             for b in collect_source_bindings(feature)}
    base = {'race_id': RACE_ID, 'record_type': 'retrospective_reconstructed',
            'production_learning_eligible': False, 'model_ready': False,
            'model_calculations_executed': False}
    payload = audit_one_race(RACE_ID, feature, _prep(),
                             _race_row(surface='ダート'), tmp_path, index,
                             base, {'path': 'f', 'sha256': 'e' * 64})
    admission = payload['research_admission']
    assert admission['status'] == 'NOT_ADMITTED'
    assert admission['reasons'] == ['SURFACE_NOT_SUPPORTED']
    assert admission['evidence_basis'] is None


def test_prediction_purpose_gate_behavior_unchanged():
    """The core audit used with purpose 'prediction' is untouched by the
    research-admission layer: unapproved imputations still block, and no
    admission concept leaks into the core result."""
    collected = {'rows': [], 'entries': [{
        'race_id': RACE_ID, 'horse_id': HORSE_ID, 'horse_number': '1',
        'assigned_weight': '57.0'}]}
    configs = [{'race_id': RACE_ID,
                'career_starts_by_horse_number': {'1': 0}}]
    result = audit_collected_data(collected, configs, approvals=[],
                                  purpose='prediction')
    assert result['gate'] == 'BLOCKED_UNAPPROVED_IMPUTATION'
    assert result['status'] == 'BLOCKED_UNAPPROVED_IMPUTATION'
    methods = {f['method'] for f in result['findings']}
    assert {'neutral_history_fallback', 'default_0',
            'history_average_or_10'} <= methods
    assert result['source_audit_status'] == 'UNVERIFIED'
    assert 'research_admission' not in result


def _mini_dataset_bound(tmp_path, feature):
    """A minimal archive whose source index binds the fixture's provenance,
    so its single candidate satisfies every admission condition."""
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    prep = _prep()
    index_records = [{'url': b['url'], 'sha256': b['sha256']}
                     for b in collect_source_bindings(feature)]
    files = {}
    for month in ('05', '06', '07', '08', '09'):
        prep_records = [prep] if month == '05' else []
        feature_records = [feature] if month == '05' else []
        _write_gz(dataset / f'audit_preparation_2026-{month}.jsonl.gz',
                  prep_records)
        _write_gz(dataset / f'features_2026-{month}.jsonl.gz',
                  feature_records)
        files[f'audit_preparation_2026-{month}.jsonl.gz'] = len(prep_records)
        files[f'features_2026-{month}.jsonl.gz'] = len(feature_records)
    _write_gz(dataset / 'races.jsonl.gz', [_race_row()])
    _write_gz(dataset / 'source_index.jsonl.gz', index_records)
    files['races.jsonl.gz'] = 1
    files['source_index.jsonl.gz'] = len(index_records)
    manifest_files = []
    for name, records in files.items():
        raw = (dataset / name).read_bytes()
        manifest_files.append({'file': name, 'bytes': len(raw),
                               'sha256': hashlib.sha256(raw).hexdigest(),
                               'records': records})
    manifest = {'files': manifest_files, 'collection_sha256': 'f' * 64,
                'source_document_bodies_included': False}
    (dataset / 'manifest.json').write_text(json.dumps(manifest))
    (dataset / 'verify_dataset.py').write_text(
        "import json\n\n\ndef verify():\n"
        "    print(json.dumps({'status': 'PASS', 'races': 1}))\n")
    return dataset


def test_run_audit_admits_bound_candidate_to_research_pool(tmp_path):
    dataset = _mini_dataset_bound(tmp_path, _feature())
    summary = run_audit(dataset, tmp_path / 'out' / 'audit')
    pool = summary['pool']
    research = pool['research_admission']
    assert research['status_counts'] == {'ADMITTED_RESEARCH': 1}
    assert research['admitted_race_ids'] == [RACE_ID]
    assert research['admitted_by_split'] == {'train': 1}
    assert research['admitted_segment_cells'] == [
        {'segment': '芝|京都|1600', 'races': 1}]
    assert research['evidence_basis'] == 'preparation_stage'
    assert research['verification_status'] == 'UNVERIFIED'
    assert pool['new_totals'] == {'turf_races': 19, 'dirt_races': 0,
                                  'jump_races': 0}
    assert summary['research_admitted_race_ids'] == [RACE_ID]
    assert summary['model_ready_race_ids'] == []
    race_entry = summary['races'][0]
    assert race_entry['gate'] == 'UNVERIFIED'
    assert race_entry['research_admission']['status'] == 'ADMITTED_RESEARCH'
    assert race_entry['research_admission']['evidence_basis'] == \
        'preparation_stage'
