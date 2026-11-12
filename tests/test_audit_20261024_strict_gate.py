"""Unit tests for the pure helpers of the 2026-10-24 strict gate driver."""
import pytest

from scripts.audit_20261024_strict_gate import (
    ORIG_PREFIX,
    career_check_for,
    compose_gate,
    date_set_check_for,
    horse_set_check,
    odds_provenance,
    parse_post_time,
    project_entry,
    remap_path,
    select_environment,
)


def test_remap_path_translates_original_prefix(tmp_path):
    original = ORIG_PREFIX + 'report/research/x/raw/a.html'
    assert remap_path(original, root=tmp_path) == (
        tmp_path / 'report/research/x/raw/a.html')


def test_remap_path_rejects_foreign_prefix():
    with pytest.raises(ValueError):
        remap_path('/somewhere/else/a.html')


def _horse(number='1', status='VERIFIED_COMPLETE_OFFICIAL_HISTORY', count=2,
           started=('2026-10-11', '2026-09-19'), history=('2026-10-11', '2026-09-19')):
    return {
        'horse_number': number,
        'career_verification': {
            'status': status, 'before_target_count': count,
            'started_rows': [{'normalized_date': d} for d in started]},
        'history_pre_target': [{'normalized_date': d} for d in history],
    }


def test_career_check_maps_verified_status_to_pass():
    check = career_check_for(_horse())
    assert check['status'] == 'PASS'
    assert check['source_status'] == 'VERIFIED_COMPLETE_OFFICIAL_HISTORY'
    assert check['before_target_count'] == 2


def test_career_check_keeps_unverified_status():
    check = career_check_for(_horse(status='UNVERIFIED'))
    assert check['status'] == 'UNVERIFIED'


def test_date_set_check_pass_and_mismatch():
    assert date_set_check_for(_horse())['status'] == 'PASS'
    bad = _horse(history=('2026-10-11',))
    assert date_set_check_for(bad)['status'] == 'MISMATCH'


def test_date_set_check_uses_last_five_started_rows():
    started = ('2026-10-11', '2026-09-19', '2026-08-16', '2026-07-25',
               '2026-07-04', '2026-06-14')
    horse = _horse(started=started, history=started[:5])
    assert date_set_check_for(horse)['status'] == 'PASS'


def _feature(declared, participating, nonstarters, count_match=True):
    return {
        'declared_entry_set_original': [{'horse_id': h} for h in declared],
        'participating_entry_set': [{'horse_id': h} for h in participating],
        'official_nonstarter_set': [{'horse_id': h} for h in nonstarters],
        'entry_result_count_check': {'match': count_match},
    }


def test_horse_set_check_with_nonstarter():
    feature = _feature(['a', 'b', 'c'], ['a', 'b'], ['c'])
    check = horse_set_check(feature, {'lineup_count_matches_result': True})
    assert check['same_set'] is True
    assert check['starter_set_same'] is True


def test_horse_set_check_requires_label_lineup_check():
    feature = _feature(['a'], ['a'], [])
    assert horse_set_check(feature, None)['starter_set_same'] is False
    assert horse_set_check(feature, {'lineup_count_matches_result': False})[
        'starter_set_same'] is False


def test_horse_set_check_detects_missing_participant():
    feature = _feature(['a', 'b'], ['a'], [])
    check = horse_set_check(feature, {'lineup_count_matches_result': True})
    assert check['same_set'] is False


def test_select_environment_requires_same_day_before_post():
    track_env = {
        'cushion_history': [{'measured_at': '2026-10-24T07:00:00+09:00', 'cushion': 9.5},
                            {'measured_at': '2026-10-23T09:00:00+09:00', 'cushion': 9.9}],
        'moisture_history': [{'measured_at': '2026-10-24T05:30:00+09:00'}],
    }
    environment, ok = select_environment(track_env, '芝',
                                         '2026-10-24T15:40:00+09:00')
    assert ok is True
    assert environment['cushion']['cushion'] == 9.5
    _, ok_late = select_environment(track_env, '芝', '2026-10-24T06:00:00+09:00')
    assert ok_late is False
    _, ok_missing = select_environment({'cushion_history': []}, '芝',
                                       '2026-10-24T15:40:00+09:00')
    assert ok_missing is False


def _dirt_track_env(moisture):
    return {
        'cushion_history': [{'measured_at': '2026-10-24T07:00:00+09:00',
                             'cushion': 10.0}],
        'moisture_history': [moisture],
    }


def test_select_environment_dirt_requires_both_dirt_moisture_points():
    # Dirt base form: BOTH dirt moisture points, same day, before post.
    # The venue-day cushion value is a turf-course measurement: it is
    # recorded as NOT_APPLICABLE and never required for dirt.
    moisture = {'measured_at': '2026-10-24T05:30:00+09:00',
                'turf_moisture_goal': 11.1, 'turf_moisture_corner4': 13.2,
                'dirt_moisture_goal': 7.7, 'dirt_moisture_corner4': 8.3}
    environment, ok = select_environment(
        _dirt_track_env(moisture), 'ダート', '2026-10-24T09:45:00+09:00')
    assert ok is True
    assert environment['cushion'] is None
    assert environment['cushion_applicability'] == 'NOT_APPLICABLE'
    assert environment['moisture']['dirt_moisture_goal'] == 7.7
    assert environment['moisture']['dirt_moisture_corner4'] == 8.3
    # All four observed moisture values stay recorded.
    assert environment['moisture']['turf_moisture_goal'] == 11.1


def test_select_environment_dirt_never_requires_cushion():
    moisture = {'measured_at': '2026-10-24T05:30:00+09:00',
                'dirt_moisture_goal': 7.7, 'dirt_moisture_corner4': 8.3}
    _, ok = select_environment({'moisture_history': [moisture]}, 'ダート',
                               '2026-10-24T09:45:00+09:00')
    assert ok is True


def test_select_environment_dirt_rejects_missing_dirt_point():
    # Turf moisture never substitutes for a missing dirt point.
    for missing in ('dirt_moisture_goal', 'dirt_moisture_corner4'):
        moisture = {'measured_at': '2026-10-24T05:30:00+09:00',
                    'turf_moisture_goal': 11.1, 'turf_moisture_corner4': 13.2,
                    'dirt_moisture_goal': 7.7, 'dirt_moisture_corner4': 8.3}
        moisture[missing] = None
        _, ok = select_environment(
            _dirt_track_env(moisture), 'ダート', '2026-10-24T09:45:00+09:00')
        assert ok is False


def test_select_environment_dirt_requires_same_day_before_post():
    moisture = {'measured_at': '2026-10-24T05:30:00+09:00',
                'dirt_moisture_goal': 7.7, 'dirt_moisture_corner4': 8.3}
    _, ok_late = select_environment(
        _dirt_track_env(moisture), 'ダート', '2026-10-24T05:00:00+09:00')
    assert ok_late is False
    other_day = {'measured_at': '2026-10-23T05:30:00+09:00',
                 'dirt_moisture_goal': 7.7, 'dirt_moisture_corner4': 8.3}
    _, ok_other_day = select_environment(
        _dirt_track_env(other_day), 'ダート', '2026-10-24T09:45:00+09:00')
    assert ok_other_day is False
    _, ok_missing = select_environment({}, 'ダート',
                                       '2026-10-24T09:45:00+09:00')
    assert ok_missing is False


def test_compose_gate_order_matches_reference_adapter():
    assert compose_gate('PASS', '芝', True, [], []) == 'PASS'
    assert compose_gate('PASS', '芝', True, [{'error': 'x'}], []) == \
        'BLOCKED_STRUCTURAL_CONTRACT'
    # Dirt base form (2026-11-09): the supported-surface set consulted by
    # the surface step now includes dirt, so a clean dirt target passes
    # this step exactly as turf does.  The composition ORDER — structural
    # contract, then surface, then environment, then issues — is the
    # 10/25 adapter's, unchanged.
    assert compose_gate('PASS', 'ダート', True, [], []) == 'PASS'
    # Structural contract outranks the surface gate, as in the 10/25 adapter.
    assert compose_gate('PASS', 'ダート', True, [{'error': 'x'}], []) == \
        'BLOCKED_STRUCTURAL_CONTRACT'
    # Jump remains a separate, unsupported surface category.
    assert compose_gate('PASS', '障害', True, [], []) == 'NOT_SUPPORTED_SURFACE'
    assert compose_gate('PASS', '芝', False, [], []) == 'UNVERIFIED_ENVIRONMENT'
    assert compose_gate('PASS', '芝', True, [], ['UNVERIFIED_HORSE_SET']) == 'UNVERIFIED'
    assert compose_gate('BLOCKED_MISSING_DATA', '芝', True, [], []) == \
        'BLOCKED_MISSING_DATA'


def test_parse_post_time_accepts_both_layouts():
    assert parse_post_time('発走時刻： 9時45分') == '2026-10-24T09:45:00+09:00'
    assert parse_post_time('15:40発走') == '2026-10-24T15:40:00+09:00'


def test_parse_post_time_rejects_missing_time():
    with pytest.raises(ValueError):
        parse_post_time('発走時刻：')


def test_project_entry_excludes_market_fields_and_history_containers():
    horse = {'horse_number': '3', 'horse_name': 'テスト', 'current_odds': '3.7',
             'current_popularity': '2', 'win_odds': '3.7',
             'history_pre_target': [{}], 'embedded_history': [{}],
             'career_verification': {}, 'career_source_text': '…',
             'target_conditions_captured_at': ''}
    entry = project_entry(horse, '晴', '良')
    for field in ('current_odds', 'current_popularity', 'win_odds',
                  'history_pre_target', 'embedded_history',
                  'career_verification', 'career_source_text'):
        assert field not in entry
    assert entry['target_weather'] == '晴'
    assert entry['target_track_condition'] == '良'
    assert entry['target_condition_provenance'] == {
        'type': 'post_event_official_reconstruction',
        'pre_race_capture_proven': False}


def test_odds_provenance_requires_recovery_page_and_value():
    row = {'odds': '2.5', 'race_result_url': 'https://example/result',
           'field_provenance': {'odds': {'url': 'https://example/odds',
                                         'sha256': 'abc'}}}
    assert odds_provenance(row) == ('https://example/odds', 'abc')
    same_page = {'odds': '2.5', 'race_result_url': 'https://example/odds',
                 'field_provenance': {'odds': {'url': 'https://example/odds',
                                               'sha256': 'abc'}}}
    assert odds_provenance(same_page) is None
    missing = {'odds': '', 'race_result_url': 'https://example/result',
               'field_provenance': {'odds': {'url': 'https://example/odds',
                                             'sha256': 'abc'}}}
    assert odds_provenance(missing) is None
    assert odds_provenance({'odds': '2.5'}) is None
