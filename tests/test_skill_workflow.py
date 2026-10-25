from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jra_scraper.config import ScrapeConfig
from src.react_workflow import ReactiveRaceWorkflow
from src.skill_workflow import (SkillSequence, SkillGateBlocked, STEPS, row_checks,
                                probability_checks, ev_checks, attach_history_sources)


def test_order_and_tampering_fail_closed(tmp_path):
    sequence = SkillSequence(tmp_path)
    with pytest.raises(SkillGateBlocked, match='out of order'):
        sequence.record('analysis', {}, checks={'ok': True})
    sequence.record('intake', {}, checks={'ok': True})
    artifact = Path(sequence.records[0]['output_artifacts'][0]['path'])
    artifact.write_text('{} changed')
    with pytest.raises(SkillGateBlocked, match='evidence changed'):
        sequence.record('collection', {}, checks={'ok': True})
    assert len(sequence.records) == 1


def test_failed_gate_records_stop_and_never_completes_next(tmp_path):
    sequence = SkillSequence(tmp_path)
    with pytest.raises(SkillGateBlocked):
        sequence.record('intake', {}, checks={'ok': False})
    assert sequence.summary()['blocked_step'] == 'intake'
    assert sequence.summary()['not_executed'][0] == 'collection'
    assert sequence.verify()


def test_complete_trace_and_numerical_checks(tmp_path):
    sequence = SkillSequence(tmp_path, mode='final')
    for step, _ in STEPS:
        sequence.record(step, {'value': step}, checks={'ok': True})
    assert sequence.verify()
    assert sequence.summary()['not_executed'] == []
    with pytest.raises(SkillGateBlocked):
        sequence.record('report', {}, checks={'ok': True})
    with pytest.raises(ValueError):
        SkillSequence(tmp_path, mode='unknown')
    rows = [{'race_id': 'R', 'horse_id': 'H', 'win_prob': '1', 'current_odds': '2', 'ev_current': '2'}]
    assert all(probability_checks(rows).values())
    assert all(ev_checks(rows).values())
    assert not all(probability_checks([{**rows[0], 'win_prob': 'nan'}]).values())
    assert not all(probability_checks([{**rows[0], 'win_prob': '0.4'}]).values())
    assert not all(probability_checks([{}]).values())
    assert not all(ev_checks([{**rows[0], 'ev_current': 'nan'}]).values())
    assert not all(ev_checks([{}]).values())
    assert not all(row_checks(rows * 2).values())
    assert not all(row_checks([{**rows[0], 'feature': float('inf')}]).values())
    assert not all(row_checks(rows, []).values())


@pytest.mark.parametrize("probability,odds,ev", [
    ("0.046002", "224.5", "10.327426"),
    ("0.052112", "130.4", "6.795458"),
    # The serialization bound is just above one EV quantum here.
    ("0", "1", "0.000001"),
    ("1", "1", "0.999999"),
])
def test_ev_checks_accept_six_decimal_rounding(probability, odds, ev):
    assert ev_checks([dict(win_prob=probability, current_odds=odds, ev_current=ev)]) == {
        "ev_recalculation_matches": True}


@pytest.mark.parametrize("probability,odds,ev", [
    ("0.046002", "224.5", "10.327626"),
    ("0.052112", "130.4", "6.795658"),
    ("0", "1", "0.000002"),
    ("1", "1", "0.999998"),
    ("-0.1", "1", "0"),
    ("1.1", "1", "1.1"),
    ("0.5", "0", "0"),
    ("0.5", "-1", "0"),
    ("0.5", "Infinity", "1"),
    ("NaN", "2", "1"),
    ("0.5", "2", "Infinity"),
    ("0.5000001", "2", "1"),
    ("0.5", "2.0000001", "1"),
    ("0.5", "2", "1.0000001"),
])
def test_ev_checks_reject_outside_rounding_contract(probability, odds, ev):
    assert not ev_checks([dict(win_prob=probability, current_odds=odds, ev_current=ev)])[
        "ev_recalculation_matches"]


def test_integrity_blocks_before_analyzer_and_preserves_audit(tmp_path):
    workflow = ReactiveRaceWorkflow(ScrapeConfig(stages_dir=tmp_path / 'stages'))
    workflow.enforce_skill_gates = True
    collected = {'rows': [{'race_id': 'R', 'horse_id': 'H', 'horse_number': '8',
                           'passing_order': ''}],
                 'entries': [{'race_id': 'R', 'horse_id': 'H', 'horse_number': '8'}]}
    workflow.domestic_collector.run = Mock(return_value=collected)
    workflow.analyzer.run = Mock(side_effect=AssertionError('must not analyze'))
    result = workflow.run([{'race_id': 'R', 'race_date': '2026-10-24', 'track': '東京',
                            'race_number': 11, 'source_url': 'https://jra.jp/test'}])
    workflow.analyzer.run.assert_not_called()
    assert result['bet_builder']['tickets'] == []
    assert result['skill_workflow']['blocked_step'] == 'approval_gate'
    assert 'data_integrity_audit' in result['data_collector']
    assert (tmp_path / 'stages' / 'run_manifest.json').exists()


def test_gated_workflow_runs_roles_in_sequence_after_verified_audit(tmp_path):
    workflow = ReactiveRaceWorkflow(ScrapeConfig(stages_dir=tmp_path / 'stages'))
    workflow.enforce_skill_gates = True
    row = {'race_id': 'R', 'horse_id': 'H', 'horse_number': '8', 'win_prob': '1',
           'current_odds': '2', 'ev_current': '2'}
    workflow.domestic_collector.run = Mock(return_value={'rows': [row], 'entries': [row]})
    workflow.analyzer.run = Mock(return_value={'feature_rows': [row]})
    workflow.simulator.run = Mock(return_value={'scenario_rows': [row]})
    workflow.ev_calculator.run = Mock(return_value={'ev_rows': [row]})
    workflow.bet_builder.run = Mock(return_value={'tickets': []})
    workflow.reviewer.run = Mock(return_value={'status': 'OK'})
    workflow.article_writer.run = Mock(return_value={'status': 'ready'})
    with patch('src.react_workflow.audit_collected_data', return_value={'gate': 'PASS', 'input_sha256': 'abc'}):
        result = workflow.run([{'race_id': 'R', 'race_date': '2026-10-24', 'track': '東京',
                                'race_number': 11, 'source_url': 'https://jra.jp/test'}])
    assert result['skill_workflow']['completed_steps'] == [step for step, _ in STEPS]


def test_cached_history_sources_are_supplied_not_asserted(tmp_path):
    from jra_scraper.scraper import safe_filename
    path = tmp_path / f'horse_{safe_filename("H")}.html'
    path.write_text('<html>original</html>')
    collected = {'rows': [{'race_id': 'R', 'horse_id': 'H'}] * 2 + [{'race_id': 'R', 'horse_id': 'MISSING'}]}
    attach_history_sources(collected, tmp_path)
    assert len(collected['source_audit']['artifacts']) == 1
    assert 'verified' not in collected['source_audit']


def test_skill_manifest_drift_and_race_source(tmp_path, monkeypatch):
    import json
    import src.skill_workflow as module
    original = json.loads((module.ROOT / 'config/skill_workflow.json').read_text())
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'config').mkdir()
    path = tmp_path / 'config/skill_workflow.json'
    path.write_text(json.dumps({**original, 'schema_version': 2}))
    with pytest.raises(ValueError, match='order mismatch'):
        module.validate_skill_contracts()
    original['modes']['final'] = []
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match='mode order mismatch'):
        module.validate_skill_contracts()
    original['modes']['final'] = original['modes']['prediction']
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match='missing or misplaced'):
        module.validate_skill_contracts()
    from jra_scraper.scraper import safe_filename
    (tmp_path / f'race_{safe_filename("R")}.html').write_text('<html>race</html>')
    collected = {'rows': [{'race_id': 'R', 'horse_id': 'H'}]}
    attach_history_sources(collected, tmp_path)
    assert collected['source_audit']['artifacts'][0]['kind'] == 'race_detail'


def _strict_final(tmp_path):
    from test_final_workflow_coverage import _workflow, _live, NOW
    collector = Mock()
    collector.collect.return_value = _live()
    workflow = _workflow(tmp_path, NOW, collector)
    workflow.enforce_skill_gates = True
    workflow.history_raw_dir = tmp_path / 'raw'
    return workflow


def test_final_integrity_gate_blocks_recompute_and_fixed_reuse(tmp_path):
    from test_final_workflow_coverage import RACE, _baseline
    for fixed in (None, {'analyzer': {}, 'simulator': {}}):
        workflow = _strict_final(tmp_path)
        workflow.analyzer.run = Mock(side_effect=AssertionError('must not analyze'))
        result = workflow.run(RACE, baseline={'data_collector': {'rows': _baseline()}}, fixed_analysis=fixed)
        workflow.analyzer.run.assert_not_called()
        assert result['final_decision']['decision_code'] == 'NO_GO_DATA_INTEGRITY_BLOCKED'
        assert result['final_decision']['tickets'] == []


@pytest.mark.parametrize('failure', ['analysis', 'simulation', 'probability', 'ev', 'tickets', 'review', 'report', None, 'fixed', 'repair'])
def test_final_sequential_gates_and_failures(tmp_path, failure):
    from test_final_workflow_coverage import RACE, _baseline, NOW
    workflow = _strict_final(tmp_path)
    row = {'race_id': 'R', 'horse_id': 'H1', 'horse_number': '1', 'win_prob': '1',
           'current_odds': '2', 'ev_current': '2'}
    analysis = {'feature_rows': [] if failure == 'analysis' else [row]}
    simulation = {'scenario_rows': [] if failure == 'simulation' else [row]}
    ev_row = {**row, **({'win_prob': '0.2'} if failure == 'probability' else {}),
              **({'ev_current': '5'} if failure == 'ev' else {})}
    workflow.analyzer.run = Mock(return_value=analysis)
    workflow.simulator.run = Mock(return_value=simulation)
    workflow.ev_calculator.run = Mock(return_value={'ev_rows': [ev_row]})
    workflow.bet_builder.run = Mock(return_value={'tickets': None if failure == 'tickets' else []})
    workflow.quantitative_reviewer.run = Mock(return_value={'status': 'OK', 'repair_actions': []})
    if failure == 'repair':
        workflow.quantitative_reviewer.run = Mock(side_effect=[{'status': 'NG', 'repair_actions': [{'type': 'repair'}]}, {'status': 'OK'}])
    workflow.final_reviewer.run = Mock(return_value={
        'status': 'INVALID' if failure == 'review' else 'OK', 'decision': 'NO_GO',
        'decision_code': 'NO_GO_AWAITING_FINAL_WINDOW', 'reason': 'wait for final window',
        'issued_at': '' if failure == 'report' else NOW.isoformat(), 'checks': {},
    })
    with patch('src.final_workflow.audit_collected_data', return_value={'gate': 'PASS', 'input_sha256': 'abc'}), patch('src.final_workflow.build_fixed_analysis_ev_rows', return_value=[row]), patch('src.final_workflow.apply_ticket_repair_actions', side_effect=lambda plan, actions: dict(plan, revision='repaired') if failure == 'repair' and actions else plan):
        result = workflow.run(RACE, baseline=_baseline(), fixed_analysis={'analyzer': analysis, 'simulator': simulation} if failure == 'fixed' else None)
    if failure in {None, 'fixed', 'repair'}:
        assert result['final_decision']['decision_code'] == 'NO_GO_AWAITING_FINAL_WINDOW'
        assert workflow.skill_sequence.summary()['not_executed'] == []
    else:
        assert result['final_decision']['decision_code'] == 'NO_GO_SKILL_VALIDATION_FAILED'
        assert workflow.skill_sequence.summary()['blocked_step'] == failure
        assert result['final_decision']['tickets'] == []
    if failure == 'repair':
        import json
        review_artifact = Path(workflow.skill_sequence.records[9]['output_artifacts'][0]['path'])
        assert json.loads(review_artifact.read_text())['reviewed_ticket_plan']['revision'] == 'repaired'
    if failure == 'analysis':
        workflow.simulator.run.assert_not_called()
    if failure == 'simulation':
        workflow.ev_calculator.run.assert_not_called()


def test_artifact_digest_canonical_and_rejects_nonfinite_values():
    from src.skill_workflow import digest
    assert digest({'a': 1, 'b': 2}) == digest({'b': 2, 'a': 1})
    assert digest({'a': 1}) != digest({'a': 2})
    with pytest.raises(ValueError):
        digest({'a': float('nan')})
