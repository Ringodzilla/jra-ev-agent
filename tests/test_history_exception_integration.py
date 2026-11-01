"""Scoped approvals cannot waive source, identity or required-field failures."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.data_integrity import audit_collected_data
from src.feature_engineering import (_comparable_last3f, _history_front_score,
                                     UnsupportedHistoryObservation,
                                     validate_explicit_history_observations)
from src.history_exceptions import HistoryExceptionError
from src.agents.analyzer import AnalyzerAgent
from src.skill_stage_contracts import _history_exception_projection


def _data():
    row = dict(race_id='r', horse_id='h', horse_number='1', run_index='1',
               date='2025-08-20', target_race_date='2026-11-01', course='海外',
               race_name='Flat race', distance='2000', history_surface='芝',
               track_condition='良', position='5', time='120', last_3f='34',
               passing_order='', passing_order_status='NOT_OBSERVED', popularity='3',
               weight='57', jockey='J', pace='36', weather='晴', odds='3.6')
    data = dict(rows=[row], entries=[dict(row, assigned_weight='57', current_odds='4',
                                        current_popularity='3')], quality_report={})
    config = {'r': {'career_starts_by_horse_number': {'1': 1}}}
    coverage = dict(status='BLOCKED', counts={'SOURCE_FIELD_MISSING': 1}, records=[
        dict(row, field='passing_order', state='SOURCE_FIELD_MISSING',
             semantic_status='NOT_OBSERVED')])
    source = dict(status='PASS', mismatches=[], acquisition_coverage=coverage)
    return data, config, source


def test_only_exact_approved_unavailable_field_can_pass_audit():
    data, config, source = _data()
    with patch('src.data_integrity.verify_source_artifacts', return_value=source):
        blocked = audit_collected_data(data, config)
        finding = blocked['findings'][0]
        record = dict(finding, finding_ids=[finding['finding_id']])
        approval = dict(approved_by='user', status='APPROVED', race_id='r', purpose='prediction',
                        evidence='Explicit exception', input_sha256=blocked['input_sha256'],
                        finding_ids=[finding['finding_id']])
        with patch('src.history_exceptions.validate_history_exceptions', return_value=[record]):
            passed = audit_collected_data(data, config, [approval])
            assert passed['gate'] == 'PASS'
            assert passed['missing_field_counts']['passing_order'] == 1
            data['rows'][0]['time'] = ''
            assert audit_collected_data(data, config, [approval])['gate'] == 'BLOCKED_MISSING_DATA'
            data['rows'][0]['time'] = '120'
            source['status'] = 'MISMATCH'
            assert audit_collected_data(data, config, [approval])['gate'] == 'UNVERIFIED'
            source['status'] = 'PASS'
            record['finding_ids'] = []
            assert audit_collected_data(data, config, [approval])['gate'] == 'UNVERIFIED'
        with patch('src.history_exceptions.validate_history_exceptions', side_effect=HistoryExceptionError('bad source')):
            assert audit_collected_data(data, config, [approval])['history_exception_error'] == 'bad source'


def test_exception_cannot_replace_a_source_observation_even_when_input_is_blank():
    data, config, source = _data()
    with patch('src.data_integrity.verify_source_artifacts', return_value=source):
        finding = audit_collected_data(data, config)['findings'][0]
        source['acquisition_coverage']['records'][0]['state'] = 'AVAILABLE_BUT_NOT_COLLECTED'
        with patch('src.history_exceptions.validate_history_exceptions', return_value=[
                dict(finding, finding_ids=[finding['finding_id']])]):
            result = audit_collected_data(data, config)
    assert result['gate'] == 'UNVERIFIED'
    assert 'Source observation' in result['history_exception_error']


def test_model_only_values_keep_source_statuses_and_reject_malformed_values():
    source = dict(last_3f='', last_3f_status='NOT_OBSERVED', passing_order='',
                  passing_order_status='NOT_OBSERVED')
    with pytest.raises(UnsupportedHistoryObservation):
        validate_explicit_history_observations(source)
    row = dict(source, last_3f='38.26', approved_front_rate=.5,
               approved_history_exceptions={
                   'last_3f': {'method': 'imperial_3f_proxy_seconds', 'value': 38.26},
                   'passing_order': {'method': 'approved_front_rate', 'value': .5}})
    validate_explicit_history_observations(row)
    assert _comparable_last3f(row) == 38.26 and _history_front_score(row) == .5
    assert row['last_3f_status'] == 'NOT_OBSERVED' and row['passing_order'] == ''
    for changed in (dict(row, last_3f='nan'), dict(row, passing_order='1-1')):
        with pytest.raises(UnsupportedHistoryObservation):
            validate_explicit_history_observations(changed)
    row = deepcopy(row)
    row['approved_history_exceptions']['last_3f']['method'] = 'invented'
    with pytest.raises(UnsupportedHistoryObservation):
        _comparable_last3f(row)


def test_analyzer_requires_original_evidence_and_records_the_projected_input():
    rows = [{'horse_id': 'h'}]
    audit = {'gate': 'PASS', 'input_sha256': 'source', 'approved_history_exceptions': []}
    analyzer = AnalyzerAgent()
    for kwargs in ({'collected_evidence': {'rows': rows}},
                   {'integrity_audit': audit},
                   {'collected_evidence': {'rows': []}, 'integrity_audit': audit}):
        with pytest.raises(ValueError):
            analyzer.run(rows, **kwargs)
    with pytest.raises(ValueError):
        analyzer.run([{'approved_history_exceptions': {'passing_order': {'value': .5}}}])
    with patch('src.history_exceptions.model_history_rows', return_value=rows), \
            patch('src.agents.analyzer.build_feature_rows', return_value=[]), \
            patch('src.agents.analyzer.apply_environment_features', return_value=([], {})):
        output = analyzer.run(rows, collected_evidence={'rows': rows}, integrity_audit=audit)
    assert output['history_exception_application']['source_input_sha256'] == 'source'
    assert output['history_exception_application']['observed_source_rows_preserved']


def test_analysis_gate_reproduces_features_instead_of_trusting_exception_tags():
    expected = {'history_exception_application': {'source_input_sha256': 'bound'},
                'feature_rows': [{'ability_score': .7}]}
    audit = {'approved_history_exceptions': [{}]}
    with patch.object(AnalyzerAgent, 'run', return_value=expected):
        output = dict(expected, environment_application={'production_model_path': '/model.json'})
        assert _history_exception_projection(output, {'rows': []}, audit)
        output['feature_rows'] = [{'ability_score': .9}]
        assert not _history_exception_projection(output, {'rows': []}, audit)
    assert _history_exception_projection({}, {}, {})
    assert not _history_exception_projection(expected, {}, {})
