import csv
import json

from scripts.update_environment_learning import update_learning, usable_snapshot


def snapshot(day='2026-02-01', race_id='R1'):
    return {'race_id': race_id, 'track': '京都', 'date': day, 'eligible_for_learning': True,
            'value_type': 'observed', 'captured_at': day + 'T09:00:00+09:00',
            'cutoff_at': day + 'T15:40:00+09:00', 'weather_captured_at': day + 'T09:00:00+09:00',
            'weather': '晴', 'track_condition': '良', 'cushion': 10.0,
            'turf_moisture_goal': 8.0, 'turf_moisture_corner4': 9.0,
            'cushion_measured_at': day + 'T07:00:00+09:00',
            'moisture_measured_at': day + 'T06:00:00+09:00',
            'source_url': 'https://www.jra.go.jp/JRADB/accessD.html'}


def test_training_never_trusts_a_future_forecast_or_after_race_snapshot():
    valid = snapshot()
    assert usable_snapshot(valid)
    assert usable_snapshot(dict(valid, captured_at='2026-02-01T00:00:00+00:00',
                                 weather_captured_at='2026-02-01T00:00:00+00:00'))
    assert usable_snapshot(dict(snapshot('2026-02-02'), captured_at='2026-02-01T23:00:00+00:00',
                                 weather_captured_at='2026-02-01T23:00:00+00:00'))
    for change in ({'value_type': 'forecast'}, {'eligible_for_learning': False},
                   {'captured_at': '2026-02-01T16:00:00+09:00'},
                   {'weather_captured_at': '2026-02-01T17:00:00+09:00'},
                   {'cushion_measured_at': '2026-01-31T07:00:00+09:00'},
                   {'cutoff_at': None}, {'captured_at': 'not-a-date'}):
        assert not usable_snapshot(dict(valid, **change))


def test_new_result_data_retrains_idempotently_without_touching_fixed_data(tmp_path):
    labels = []
    for i, day in enumerate(('2026-02-01', '2026-02-02', '2026-02-03'), 1):
        race_id = f'R{i}'
        rows = [{'race_id': race_id, 'horse_id': f'H{h}', 'horse_number': str(h), 'horse_name': f'H{h}',
                 'run_index': '1', 'date': '2026-01-01', 'target_race_date': day,
                 'target_track': '京都', 'target_surface': '芝', 'target_weather': '曇',
                 'target_track_condition': '良', 'course': '京都', 'distance': '1600',
                 'target_distance': '1600', 'position': str(h), 'last_3f': '34'} for h in (1, 2)]
        data = {'data_collector': {'rows': rows, 'environment_snapshots': [snapshot(day, race_id)]}}
        directory = tmp_path / 'report/races' / race_id
        directory.mkdir(parents=True)
        (directory / 'pipeline_run.json').write_text(json.dumps(data))
        labels.append({'race_id': race_id, '式別': '単勝', '組番': '', '馬番': '1', '払戻金': '200'})
    label_path = tmp_path / 'data/processed/result_labels.csv'
    label_path.parent.mkdir(parents=True)
    with label_path.open('w') as file:
        writer = csv.DictWriter(file, fieldnames=list(labels[0])); writer.writeheader(); writer.writerows(labels)
    original_labels = label_path.read_bytes()
    first = update_learning(tmp_path)
    second = update_learning(tmp_path)
    assert first['status'] == 'INSUFFICIENT_DATA' and not first['production_model_changed']
    assert first['dataset_sha256'] == second['dataset_sha256']
    assert first['selected_model'] == second['selected_model']
    assert sum(map(len, first['blocks'].values())) == 3
    assert label_path.read_bytes() == original_labels
    assert len(list((tmp_path / 'data/learning/environment').glob('*/*/dataset.json'))) == 1
    artifact = tmp_path / 'report/races/R1/pipeline_run.json'
    bad = json.loads(artifact.read_text())
    bad['data_collector']['environment_snapshots'] = [dict(snapshot(), value_type='forecast')]
    artifact.write_text(json.dumps(bad))
    blocked = update_learning(tmp_path)
    assert blocked['pending_environment'] == [{'race_id': 'R1', 'reason': 'missing_verified_pre_race_environment'}]
    assert blocked['status'] == 'INSUFFICIENT_DATA'


def test_learning_does_not_multiply_same_day_measurements_or_use_unlabeled_data(tmp_path):
    empty = update_learning(tmp_path)
    assert empty['status'] == 'INSUFFICIENT_DATA'
    directory = tmp_path / 'report/races/R1'
    directory.mkdir(parents=True)
    (directory / 'pipeline_run.json').write_text(json.dumps({'data_collector': {'rows': []}}))
    assert update_learning(tmp_path)['status'] == 'INSUFFICIENT_DATA'
    rows = [{'race_id': race_id, 'date': '2026-01-01', 'target_race_date': '2026-02-01',
             'target_track': '京都', 'target_surface': '芝', 'target_weather': '晴',
             'target_track_condition': '良'} for race_id in ('R1', 'R2')]
    (directory / 'pipeline_run.json').write_text(json.dumps({'data_collector': {
        'rows': rows, 'environment_snapshots': [snapshot(race_id='R1'), snapshot(race_id='R2')]}}))
    result = update_learning(tmp_path)
    assert len(result['excluded_races']) == 2
    assert all(r['reason'] == 'missing_win_label' for r in result['excluded_races'])
