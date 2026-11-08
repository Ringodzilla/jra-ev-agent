"""SOURCE_ROUTE_UNAVAILABLE: diagnostic only, never a coverage pass.

A jurisdiction with no registered official source route (e.g. Qatar) keeps
the gate blocked. The classification exists so diagnostics can distinguish
'no route exists in this system' from 'a route ran and its source does not
observe the field' — it must never be treated as NOT_APPLICABLE, because the
field itself exists (the race was timed; sectionals were recorded locally).
"""
import hashlib
from dataclasses import replace

from src.acquisition_coverage import field_coverage
from src.data_integrity import verify_source_artifacts


def test_pipeline_records_route_determination_only_for_unregistered_overseas(tmp_path):
    from jra_scraper.config import ScrapeConfig
    from jra_scraper.pipeline import JRAPipeline

    config = ScrapeConfig()
    config = replace(config, raw_dir=tmp_path / 'raw', output_csv=tmp_path / 'o.csv',
                     entries_csv=tmp_path / 'e.csv', odds_snapshots_csv=tmp_path / 's.csv',
                     combo_odds_csv=tmp_path / 'c.csv', state_path=tmp_path / 'state.json',
                     quality_report_path=tmp_path / 'q.json',
                     missing_history_requests_path=tmp_path / 'm.json',
                     manual_history_template_csv=tmp_path / 't.csv',
                     manual_history_csv=tmp_path / 'manual.csv', stages_dir=tmp_path / 'stages')
    pipeline = JRAPipeline(config)
    try:
        qatar = dict(ROW, course='華', race_name='アミールT')
        france = dict(ROW, course='仏', race_name='凱旋門賞')
        aichi = dict(ROW, course='中京', race_name='愛知杯')
        domestic = dict(ROW, course='阪神', race_name='宝塚記念')
        pipeline._record_route_determinations([qatar, france, aichi, domestic])
        attempts = pipeline.acquisition_recorder.attempts()
    finally:
        pipeline.close()
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt['route'] == 'route_registry'
    assert attempt['route_status'] == 'no_official_source_route'
    assert attempt['race_id'] == 'R' and attempt['horse_id'] == 'H'
    assert attempt['date'] == ROW['date'] and 'time' in attempt['fields']
    assert 'qatar' in attempt['reason']


ROW = {'race_id': 'R', 'horse_id': 'H', 'horse_number': '2', 'run_index': '2',
       'date': '2026年2月14日', 'horse_name': 'ビザンチンドリーム'}


def _attempt(field, *, route_status='no_official_source_route', reason=None, sha=''):
    attempt = {'race_id': 'R', 'horse_id': 'H', 'date': '2026年2月14日',
               'fields': [field], 'route': 'jra_horse_history', 'outcome': 'fetched'}
    if route_status:
        attempt['route_status'] = route_status
    if reason is not None:
        attempt['reason'] = reason
    if sha:
        attempt['artifact_sha256'] = sha
    return attempt


def _collected(attempts, artifacts=()):
    return {'source_audit': {'attempts': attempts, 'artifacts': list(artifacts)}}


def test_not_observed_original_with_route_status_is_route_unavailable():
    row = dict(ROW, last_3f='', last_3f_status='NOT_OBSERVED')
    original = {'last_3f': '', 'last_3f_status': 'NOT_OBSERVED',
                'last_3f_missing_reason': 'OFFICIAL_SECTIONAL_CELL_EMPTY',
                '_source_artifact': {'path': '/raw/horse.html', 'sha256': 'x', 'kind': 'horse_history'}}
    reason = ('No official source route is registered in this system for qatar; '
              'JRA official history is the only route and lists no value')
    record = field_coverage(row, row, 'last_3f', [original],
                            _collected([_attempt('last_3f', reason=reason)]))
    assert record['state'] == 'SOURCE_ROUTE_UNAVAILABLE'
    assert record['semantic_status'] == 'NOT_OBSERVED'
    assert record['reason'] == reason


def test_not_observed_original_without_route_status_stays_field_missing():
    row = dict(ROW, last_3f='', last_3f_status='NOT_OBSERVED')
    original = {'last_3f': '', 'last_3f_status': 'NOT_OBSERVED',
                'last_3f_missing_reason': 'OFFICIAL_SECTIONAL_CELL_EMPTY',
                '_source_artifact': {'path': '/raw/horse.html', 'sha256': 'x', 'kind': 'horse_history'}}
    record = field_coverage(row, row, 'last_3f', [original],
                            _collected([_attempt('last_3f', route_status='')]))
    assert record['state'] == 'SOURCE_FIELD_MISSING'


def test_statusless_field_with_route_status_is_route_unavailable():
    # `time` carries no *_status on JRA originals; the attempts branch applies.
    row = dict(ROW, time='')
    original = {'time': '', '_source_artifact': {'path': '/raw/horse.html', 'sha256': 'x',
                                                 'kind': 'horse_history'}}
    record = field_coverage(row, row, 'time', [original],
                            _collected([_attempt('time', reason='no route')]))
    assert record['state'] == 'SOURCE_ROUTE_UNAVAILABLE'
    assert record['reason'] == 'no route'


def test_fetch_failed_takes_precedence_over_route_unavailable():
    row = dict(ROW, time='')
    html = '<html><body>403 Forbidden</body></html>'
    sha = hashlib.sha256(html.encode()).hexdigest()
    artifact = {'path': '/raw/horse.html', 'sha256': sha, 'kind': 'horse_history', 'html': html}
    original = {'time': '', '_source_artifact': {'path': '/raw/horse.html', 'sha256': sha,
                                                 'kind': 'horse_history'}}
    record = field_coverage(row, row, 'time', [original],
                            _collected([_attempt('time', reason='no route', sha=sha)], [artifact]))
    assert record['state'] == 'FETCH_FAILED'


def test_route_unavailable_keeps_full_audit_coverage_blocked():
    html = '''<div class="cell date">2026年2月14日 ドーハ競馬場</div>
    <table><tr><td class="place">7</td><td class="horse">ビザンチンドリーム(JPN)</td>
    <td class="weight">57.0</td><td class="pop">3</td></tr></table>'''
    artifact = dict(kind='overseas_jra_result', url='https://jra.jp/result', html=html,
                    sha256=hashlib.sha256(html.encode()).hexdigest(), race_id='r',
                    horse_id='h', horse_name='ビザンチンドリーム', date='2026-02-19')
    # NOTE: date intentionally after the run is impossible for overseas_jra
    # identity (it binds artifact['date'] to the page date 2026-02-14); use the
    # page's own date so reparse succeeds.
    artifact['date'] = '2026-02-14'
    row = dict(race_id='r', horse_id='h', horse_name='ビザンチンドリーム',
               date='2026-02-14', target_race_date='2026-11-08', position='7',
               popularity='3', weight='57')
    attempts = [{'race_id': 'r', 'horse_id': 'h', 'date': '2026-02-14',
                 'fields': ['time'], 'route': 'jra_horse_history', 'outcome': 'fetched',
                 'route_status': 'no_official_source_route',
                 'reason': 'No official source route is registered in this system for qatar'}]
    collected = dict(rows=[row], entries=[row], quality_report={},
                     source_audit={'artifacts': [artifact], 'attempts': attempts})
    check = verify_source_artifacts(collected)
    coverage = check['acquisition_coverage']
    assert coverage['status'] == 'BLOCKED'
    time_records = [r for r in coverage['records'] if r['field'] == 'time']
    assert time_records and time_records[0]['state'] == 'SOURCE_ROUTE_UNAVAILABLE'
