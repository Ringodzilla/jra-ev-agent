from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from scripts.train_environment_model import build_training_races


def inputs():
    rows = [{'race_id': 'R', 'horse_id': f'H{i}', 'horse_name': f'H{i}', 'horse_number': str(i),
             'run_index': '1', 'date': '2026-01-01', 'target_race_date': '2026-02-01',
             'target_track': '京都', 'target_surface': '芝', 'target_weather': '晴',
             'target_track_condition': '良', 'course': '京都', 'distance': '1600',
             'target_distance': '1600', 'position': str(i), 'last_3f': '34.0'} for i in (1, 2)]
    labels = [{'race_id': 'R', '式別': '単勝', '馬番': '1', '払戻金': '250'}]
    measurements = [{'track': '京都', 'date': '2026-02-01', 'cushion': 10.0,
                     'turf_moisture_goal': 8.0, 'turf_moisture_corner4': 9.0,
                     'cushion_measured_at': '2026-02-01T07:00:00+09:00',
                     'moisture_measured_at': '2026-02-01T06:00:00+09:00',
                     'source_url': 'https://www.jra.go.jp/keiba/baba/archive/2026pdf/kyoto02.pdf'}]
    return rows, labels, measurements


def test_join_uses_exact_date_and_does_not_mutate_frozen_inputs():
    rows, labels, measurements = inputs()
    saved = deepcopy((rows, labels, measurements))
    dataset = build_training_races(rows, labels, measurements)
    assert not dataset['excluded'] and len(dataset['races']) == 1
    assert dataset['races'][0]['win_payouts'] == {'1': 250}
    assert (rows, labels, measurements) == saved
    assert build_training_races(rows, labels, [dict(measurements[0], date='2026-01-31')])['excluded'][0]['reason'] == 'missing_same_day_official_measurements'


def test_missing_environment_never_gets_a_default_or_turf_value_for_dirt():
    rows, labels, measurements = inputs()
    for key, value, expected in [('target_surface', 'ダート', 'turf_cushion_not_applicable_to_dirt'),
                                 ('target_weather', '', 'missing_pre_race_weather_or_condition')]:
        edited = [dict(row, **{key: value}) for row in rows]
        assert build_training_races(edited, labels, measurements)['excluded'][0]['reason'] == expected
    assert build_training_races(rows, [], measurements)['excluded'][0]['reason'] == 'missing_win_label'


def test_leakage_conflicts_and_unverified_observations_fail_closed():
    rows, labels, measurements = inputs()
    with pytest.raises(ValueError, match='Non-past'):
        build_training_races([dict(r, date='2026-02-01') for r in rows], labels, measurements)
    with pytest.raises(ValueError, match='Conflicting target'):
        build_training_races([rows[0], dict(rows[1], target_track='東京')], labels, measurements)
    with pytest.raises(ValueError, match='Conflicting measurement'):
        build_training_races(rows, labels, measurements + [dict(measurements[0], cushion=9)])
    for change in ({'source_url': 'https://example.com/'},
                   {'cushion_measured_at': '2026-02-02T07:00:00+09:00'},
                   {'cushion_measured_at': '2026-02-01T16:00:00+09:00'},
                   {'cushion_measured_at': '2026-02-01T07:00:00'}):
        with pytest.raises(ValueError):
            build_training_races(rows, labels, [dict(measurements[0], **change)])
    with pytest.raises(ValueError, match='Winner missing'):
        build_training_races(rows, [dict(labels[0], 馬番='3')], measurements)


def test_curated_measurements_have_valid_official_provenance_and_distinct_days():
    records = json.loads(Path('data/environment/official_measurements.json').read_text())['records']
    assert len({(r['track'], r['date']) for r in records}) == len(records)
    for record in records:
        assert record['source_url'].startswith(('https://www.jra.go.jp/', 'https://jra.jp/'))
        assert datetime.fromisoformat(record['cushion_measured_at']).date().isoformat() == record['date']
