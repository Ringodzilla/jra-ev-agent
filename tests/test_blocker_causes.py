"""NO_GO blocker-cause classification and collection recovery.

User request 2026-11-14: a NO_GO must say what data is missing and
whether that is a collection miss (recoverable by re-collecting) or a
value the source does not carry / only a substitution could invent.
"""
from types import SimpleNamespace

from analysis.ev import EVWeights
from src.data_integrity import _classify_blocker_causes
from src.react_workflow import ReactiveRaceWorkflow
from src.skill_workflow import SkillGateBlocked


def _never_excepted(_item):
    return False


def classify(**kwargs):
    defaults = dict(unapproved=[], coverage_records=[],
                    source_verified=True, excepted=_never_excepted)
    defaults.update(kwargs)
    return _classify_blocker_causes(**defaults)


def test_collection_miss_is_recoverable():
    causes = classify(coverage_records=[
        {'field': 'pace', 'state': 'AVAILABLE_BUT_NOT_COLLECTED'},
        {'field': 'pace', 'state': 'NOT_ATTEMPTED'},
    ])
    assert [c['cause'] for c in causes] == ['collection_miss']
    assert causes[0]['recoverable'] is True
    assert causes[0]['fields'] == {'pace': 2}
    assert '取得ミス' in causes[0]['summary']


def test_fetch_failed_is_recoverable():
    causes = classify(coverage_records=[
        {'field': 'time', 'state': 'FETCH_FAILED'}])
    assert [c['cause'] for c in causes] == ['fetch_failed']
    assert causes[0]['recoverable'] is True


def test_source_missing_is_not_recoverable():
    causes = classify(coverage_records=[
        {'field': 'passing_order', 'state': 'SOURCE_FIELD_MISSING'},
        {'field': 'passing_order', 'state': 'SOURCE_ROUTE_UNAVAILABLE'},
    ])
    assert [c['cause'] for c in causes] == ['source_missing']
    assert causes[0]['recoverable'] is False


def test_imputation_finding_suppresses_duplicate_source_missing():
    causes = classify(
        unapproved=[{'field': 'last_3f', 'method': 'parser_last3f_fallback',
                     'required_missing': False}],
        coverage_records=[
            {'field': 'last_3f', 'state': 'SOURCE_FIELD_MISSING'}])
    assert [c['cause'] for c in causes] == ['imputation_required']
    assert causes[0]['recoverable'] is False
    assert '捏造' in causes[0]['summary']


def test_unsupported_and_required_missing_findings_are_source_missing():
    causes = classify(unapproved=[
        {'field': 'passing_order', 'method': 'unsupported_source_observation',
         'required_missing': True},
        {'field': 'time', 'method': 'none', 'required_missing': True},
    ])
    assert [c['cause'] for c in causes] == ['source_missing']
    assert causes[0]['fields'] == {'passing_order': 1, 'time': 1}


def test_unverified_source_is_its_own_cause():
    causes = classify(source_verified=False)
    assert [c['cause'] for c in causes] == ['source_unverified']
    assert causes[0]['recoverable'] is False


def test_excepted_records_and_findings_are_skipped():
    record = {'field': 'pace', 'state': 'FETCH_FAILED'}
    finding = {'field': 'last_3f', 'method': 'parser_last3f_fallback',
               'required_missing': False}
    causes = classify(unapproved=[finding], coverage_records=[record],
                      excepted=lambda item: item in (record, finding))
    assert causes == []


class _FakeSequence:
    def __init__(self, _stages_dir, **_kwargs):
        self.records = []

    def bind(self, payload):
        return payload

    def record(self, step_id, output, *, checks, failure_code='X'):
        self.records.append(step_id)
        return {}

    def summary(self):
        return {'steps': list(self.records)}


def _workflow(monkeypatch, tmp_path):
    monkeypatch.setattr('src.react_workflow.SkillSequence', _FakeSequence)
    workflow = ReactiveRaceWorkflow.__new__(ReactiveRaceWorkflow)
    workflow.enforce_skill_gates = True
    workflow.config = SimpleNamespace(stages_dir=tmp_path)
    workflow.settings = SimpleNamespace()
    workflow.ev_calculator = SimpleNamespace(weights=SimpleNamespace())
    workflow._audit_collected = {}
    workflow._write_stage_outputs = lambda payload: None
    return workflow


def _race_configs():
    return [{'race_date': '2026-11-14', 'track': '京都', 'race_number': 11,
             'source_url': 'https://example.invalid/race'}]


def test_recoverable_block_retries_once_with_aggressive_repair(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        'src.react_workflow.asdict', lambda obj: {})
    workflow = _workflow(monkeypatch, tmp_path)
    calls = []

    def fake_run(configs, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            workflow._audit_collected = {'data_integrity_audit': {
                'gate': 'BLOCKED_ACQUISITION_INCOMPLETE',
                'blocker_causes': [{
                    'cause': 'collection_miss', 'recoverable': True,
                    'fields': {'pace': 1}, 'count': 1,
                    'summary': 'pace×1 はソースに存在するが未取得（取得ミス）'}]}}
            raise SkillGateBlocked('BLOCKED_ACQUISITION_INCOMPLETE')
        return {'data_collector': {'data_integrity_audit': {'gate': 'PASS'}}}

    workflow._run = fake_run
    payload = workflow.run(_race_configs())
    assert len(calls) == 2
    assert calls[1]['force_rebuild'] is True
    assert calls[1]['initial_aggressive_repair'] is True
    assert payload['collection_recovery']['attempted'] is True
    assert payload['collection_recovery']['outcome_gate'] == 'PASS'


def test_unrecoverable_block_does_not_retry(monkeypatch, tmp_path):
    monkeypatch.setattr('src.react_workflow.asdict', lambda obj: {})
    workflow = _workflow(monkeypatch, tmp_path)
    calls = []

    def fake_run(configs, **kwargs):
        calls.append(kwargs)
        workflow._audit_collected = {'data_integrity_audit': {
            'gate': 'BLOCKED_UNAPPROVED_IMPUTATION',
            'blocker_causes': [{
                'cause': 'imputation_required', 'recoverable': False,
                'fields': {'last_3f': 16}, 'count': 16,
                'summary': 'last_3f×16 は補完（値の捏造）が必要なため対象外'}]}}
        raise SkillGateBlocked('BLOCKED_UNAPPROVED_IMPUTATION')

    workflow._run = fake_run
    payload = workflow.run(_race_configs())
    assert len(calls) == 1
    assert 'collection_recovery' not in payload
    assert payload['reviewer']['status'] == 'NG'


def test_recovery_retry_that_still_blocks_annotates_outcome(
        monkeypatch, tmp_path):
    monkeypatch.setattr('src.react_workflow.asdict', lambda obj: {})
    workflow = _workflow(monkeypatch, tmp_path)
    calls = []

    def fake_run(configs, **kwargs):
        calls.append(kwargs)
        workflow._audit_collected = {'data_integrity_audit': {
            'gate': 'BLOCKED_ACQUISITION_INCOMPLETE',
            'blocker_causes': [{
                'cause': 'fetch_failed', 'recoverable': True,
                'fields': {'time': 1}, 'count': 1, 'summary': 'x'}]}}
        raise SkillGateBlocked('BLOCKED_ACQUISITION_INCOMPLETE')

    workflow._run = fake_run
    payload = workflow.run(_race_configs())
    assert len(calls) == 2
    assert payload['collection_recovery']['attempted'] is True
    assert payload['collection_recovery']['outcome_gate'] == 'BLOCKED'


def test_final_workflow_reason_lists_blocker_causes(monkeypatch, tmp_path):
    from src.agents.settings import WorkflowSettings
    from src.deadline import DeadlineSettings
    from src.final_workflow import FinalPredictionWorkflow

    class RaisingSequence(_FakeSequence):
        def record(self, step_id, output, *, checks, failure_code='X'):
            if step_id == 'approval_gate':
                raise SkillGateBlocked('BLOCKED_UNAPPROVED_IMPUTATION')
            return super().record(step_id, output, checks=checks)

    monkeypatch.setattr('src.final_workflow.SkillSequence', RaisingSequence)
    monkeypatch.setattr(
        'src.final_workflow._attach_baseline_history_sources',
        lambda *args, **kwargs: None)
    workflow = FinalPredictionWorkflow.__new__(FinalPredictionWorkflow)
    workflow.enforce_skill_gates = True
    workflow.output_dir = tmp_path
    workflow.history_raw_dir = tmp_path
    workflow.integrity_approvals = []
    workflow.workflow_settings = WorkflowSettings()
    workflow.deadline_settings = DeadlineSettings()
    workflow.ev_calculator = SimpleNamespace(weights=EVWeights())
    captured = {}
    workflow._finish_after_live = (
        lambda plan, *, collected, code, reason:
        captured.update(code=code, reason=reason) or {})
    row = dict(race_id='r1', horse_number='1', horse_id='h1', run_index='1',
               date='2026-10-01', course='東京', race_name='新馬',
               distance='1600', history_surface='芝', track_condition='良',
               position='1', time='95.4', last_3f='', passing_order='4-3',
               popularity='2', weight='55', jockey='騎手', pace='M',
               weather='晴', odds='5.0')
    collected = {'rows': [row],
                 'entries': [dict(race_id='r1', horse_number='1',
                                  assigned_weight='55', current_popularity='3',
                                  current_odds='120')],
                 'quality_report': {}}
    race_config = {'race_id': 'r1',
                   'career_starts_by_horse_number': {'1': 1}}
    workflow._apply_intake_skill_gates(
        SimpleNamespace(), race_config, {}, collected)
    assert captured['code'] == 'NO_GO_DATA_INTEGRITY_BLOCKED'
    assert 'last_3f' in captured['reason']
    assert '捏造' in captured['reason']


def test_saved_input_is_never_recollected(monkeypatch, tmp_path):
    monkeypatch.setattr('src.react_workflow.asdict', lambda obj: {})
    workflow = _workflow(monkeypatch, tmp_path)
    calls = []

    def fake_run(configs, **kwargs):
        calls.append(kwargs)
        workflow._audit_collected = {'data_integrity_audit': {
            'gate': 'BLOCKED_ACQUISITION_INCOMPLETE',
            'blocker_causes': [{
                'cause': 'fetch_failed', 'recoverable': True,
                'fields': {'time': 1}, 'count': 1, 'summary': 'x'}]}}
        raise SkillGateBlocked('BLOCKED_ACQUISITION_INCOMPLETE')

    workflow._run = fake_run
    workflow.run(_race_configs(), collected_input={'entries': []})
    assert len(calls) == 1
