import json

import pytest

from analysis.ev import EVWeights, compute_ev
from src.environment_prediction import (
    HORSE_FEATURES,
    _canonical_sha256,
    _environment_offsets,
    _environment_values,
    _load_promoted_model,
    _snapshot_complete,
    apply_environment_features,
)
from src.model import _apply_environment_offsets


def snapshot():
    hashes = {
        'weather': '1' * 64,
        'cushion': '2' * 64,
        'moisture': '3' * 64,
    }
    return {
        'environment_schema_version': 2,
        'race_id': 'R',
        'date': '2026-10-18',
        'value_type': 'observed',
        'collection_complete': True,
        'weather': '晴',
        'track_condition': '良',
        'weather_captured_at': '2026-10-18T12:00:00+09:00',
        'captured_at': '2026-10-18T12:00:00+09:00',
        'cushion': 10.7,
        'cushion_measured_at': '2026-10-18T07:00:00+09:00',
        'moisture_measured_at': '2026-10-18T06:00:00+09:00',
        'turf_moisture_goal': 6.7,
        'turf_moisture_corner4': 7.3,
        'sources': {name: {'sha256': digest} for name, digest in hashes.items()},
        'errors': [],
    }


def rows():
    common = {
        'race_id': 'R', 'target_surface': '芝', 'target_race_date': '2026-10-18',
        'ability_score': 0.5, 'course_score': 0.5, 'pace_score': 0.5,
        'weight_score': 0.0, 'jockey_score': 0.5, 'closing_strength': 0.5,
        'speed_score': 0.5, 'track_condition_score': 0.5, 'consistency': 0.5,
        'pace_mix_high': 0.3, 'current_popularity': '1',
    }
    return [
        dict(common, horse_id='H1', horse_name='A', horse_number='1', front_rate=0.8, current_odds='3'),
        dict(common, horse_id='H2', horse_name='B', horse_number='2', front_rate=0.2, current_odds='3'),
    ]


def promoted_model(path):
    env_names = ['rain', 'cloud', 'snow', 'cushion', 'moisture_goal', 'moisture_corner4', 'condition']
    names = [f'{horse}*{env}' for horse in HORSE_FEATURES for env in env_names]
    coefficients = [0.0] * len(names)
    coefficients[names.index('front_rate*cushion')] = 1.0
    artifact = {
        'schema_version': 1,
        'status': 'PROMOTED',
        'production_model_changed': True,
        'model_id': 'test-model',
        'model': {
            'schema_version': 1,
            'family': 'linear',
            'feature_names': names,
            'scales': [1.0] * len(names),
            'coefficients': coefficients,
            'training_end_date': '2026-10-17',
            'supported_surfaces': ['芝'],
        },
    }
    path.write_text(json.dumps(artifact))


def test_observation_is_carried_even_before_model_promotion(tmp_path):
    enriched, audit = apply_environment_features(rows(), [snapshot()], production_model_path=tmp_path / 'missing.json')
    assert not audit['production_model_available']
    assert all(row['target_environment_cushion'] == 10.7 for row in enriched)
    assert all(row['environment_score'] == 0 for row in enriched)
    assert all(row['environment_adjustment_applied'] is False for row in enriched)
    assert all(row['environment_model_status'] == 'not_promoted' for row in enriched)
    assert all(row['environment_not_applied_reason'] == 'no_promoted_environment_model' for row in enriched)
    assert all(row['environment_fallback_reason'] == 'NO_PROMOTED_ENVIRONMENT_MODEL' for row in enriched)
    assert all(row['environment_observation_id'].startswith('environment-v2:R:') for row in enriched)
    assert all(row['environment_observation_sha256'] == audit['races'][0]['observation_sha256'] for row in enriched)
    assert all(row['environment_source_sha256s'] == audit['races'][0]['source_sha256s'] for row in enriched)
    assert audit['races'][0]['environment_model_status'] == 'not_promoted'
    assert audit['races'][0]['environment_not_applied_reason'] == 'no_promoted_environment_model'
    assert audit['races'][0]['source_sha256s'] == {
        'cushion': '2' * 64,
        'moisture': '3' * 64,
        'weather': '1' * 64,
    }


def test_unpromoted_artifact_never_creates_an_environment_score(tmp_path):
    path = tmp_path / 'environment_model.json'
    promoted_model(path)
    artifact = json.loads(path.read_text())
    artifact['status'] = 'CANDIDATE'
    path.write_text(json.dumps(artifact))

    enriched, audit = apply_environment_features(rows(), [snapshot()], production_model_path=path)

    assert all(row['environment_score'] == 0 for row in enriched)
    assert all(row['environment_adjustment_applied'] is False for row in enriched)
    assert all(row['environment_model_status'] == 'not_promoted' for row in enriched)
    assert all(row['environment_not_applied_reason'] == 'no_promoted_environment_model' for row in enriched)
    assert audit['races'][0]['status'] == 'COLLECTED_PENDING_PROMOTION'


def test_promoted_model_changes_probability_and_preserves_sum(tmp_path):
    path = tmp_path / 'environment_model.json'
    promoted_model(path)
    enriched, audit = apply_environment_features(rows(), [snapshot()], production_model_path=path)
    assert audit['races'][0]['environment_adjustment_applied']
    assert audit['races'][0]['environment_model_status'] == 'promoted'
    assert audit['races'][0]['environment_not_applied_reason'] == ''
    assert enriched[0]['environment_score'] == pytest.approx(0.21)
    assert enriched[1]['environment_score'] == pytest.approx(-0.21)
    assert all(row['environment_model_status'] == 'promoted' for row in enriched)
    assert all(row['environment_not_applied_reason'] == '' for row in enriched)
    calculated = compute_ev(
        enriched,
        weights=EVWeights(monte_carlo_iterations=1, luck_score_std=0.0),
    )
    by_number = {row['horse_number']: row for row in calculated}
    assert float(by_number['1']['win_prob']) > float(by_number['2']['win_prob'])
    assert sum(float(row['win_prob']) for row in calculated) == pytest.approx(1.0, abs=2e-6)
    assert all(row['structural_probability_formula_version'] == 'structural_ev_v2_environment' for row in calculated)


def test_missing_and_incomplete_observations_are_explicit(tmp_path):
    missing, audit = apply_environment_features(rows(), [], production_model_path=tmp_path / 'missing.json')
    assert audit['races'][0]['status'] == 'MISSING_OBSERVATION'
    assert missing[0]['environment_observation_sha256'] == ''

    incomplete, audit = apply_environment_features(
        rows(), [dict(snapshot(), collection_complete=False)],
        production_model_path=tmp_path / 'missing.json',
    )
    assert audit['races'][0]['status'] == 'INCOMPLETE_OBSERVATION'
    assert incomplete[0]['target_environment_cushion'] == 10.7
    assert not _snapshot_complete(dict(snapshot(), value_type='forecast'), '芝')
    assert not _snapshot_complete(dict(snapshot(), collection_complete=False), '芝')
    assert not _snapshot_complete(dict(snapshot(), environment_schema_version=1), '芝')
    assert not _snapshot_complete(dict(snapshot(), turf_moisture_goal=''), '芝')
    assert not _snapshot_complete(dict(snapshot(), sources={}), '芝')
    assert not _snapshot_complete(dict(snapshot(), sources=[]), '芝')
    sources_with_non_mapping_metadata = dict(snapshot()['sources'], metadata='ignored')
    assert _snapshot_complete(dict(snapshot(), sources=sources_with_non_mapping_metadata), '芝')
    assert not _snapshot_complete(snapshot(), 'unknown')
    dirt = dict(snapshot(), dirt_moisture_goal=2.0, dirt_moisture_corner4=2.5)
    assert _snapshot_complete(dirt, 'ダート')


def test_model_artifact_validation_and_race_rejection(tmp_path):
    path = tmp_path / 'model.json'
    path.write_text('{')
    assert _load_promoted_model(path)[1] == 'INVALID_ENVIRONMENT_MODEL_ARTIFACT'
    path.write_text(json.dumps({'status': 'CANDIDATE'}))
    assert _load_promoted_model(path)[1] == 'ENVIRONMENT_MODEL_NOT_PROMOTED'
    path.write_text(json.dumps({'status': 'PROMOTED', 'production_model_changed': True, 'model': {}}))
    assert _load_promoted_model(path)[1] == 'ENVIRONMENT_MODEL_SCHEMA_MISMATCH'

    promoted_model(path)
    artifact = json.loads(path.read_text())
    artifact['model']['supported_surfaces'] = ['ダート']
    path.write_text(json.dumps(artifact))
    _, audit = apply_environment_features(rows(), [snapshot()], production_model_path=path)
    assert audit['races'][0]['status'] == 'MODEL_REJECTED_FOR_RACE'


def test_environment_schema_guards_and_dirt_quadratic_values(tmp_path):
    path = tmp_path / 'model.json'
    promoted_model(path)
    model = json.loads(path.read_text())['model']
    with pytest.raises(ValueError, match='training cutoff'):
        _environment_offsets(rows(), snapshot(), dict(model, training_end_date='2026-10-18'))
    with pytest.raises(ValueError, match='feature schema'):
        _environment_offsets(rows(), snapshot(), dict(model, feature_names=[]))
    with pytest.raises(ValueError, match='model scales'):
        _environment_offsets(rows(), snapshot(), dict(model, scales=[0.0] * len(model['scales'])))
    bad_coefficients = list(model['coefficients'])
    bad_coefficients[0] = float('inf')
    with pytest.raises(ValueError, match='non-finite'):
        _environment_offsets(rows(), snapshot(), dict(model, coefficients=bad_coefficients))

    with pytest.raises(ValueError, match='weather'):
        _environment_values(dict(snapshot(), weather=''), '芝', 'linear')
    with pytest.raises(ValueError, match='family'):
        _environment_values(snapshot(), '芝', 'unknown')
    with pytest.raises(ValueError, match='surface'):
        _environment_values(snapshot(), 'unknown', 'linear')
    dirt = dict(snapshot(), dirt_moisture_goal=2.0, dirt_moisture_corner4=2.5)
    values = _environment_values(dirt, 'ダート', 'quadratic')
    assert 'cushion' not in values and 'moisture_goal_squared' in values
    turf = _environment_values(snapshot(), '芝', 'quadratic')
    assert turf['cushion_squared'] == pytest.approx(0.49)
    assert _canonical_sha256(None) == ''
    with pytest.raises(ValueError, match='complete race field'):
        _apply_environment_offsets(
            [{'environment_adjustment_applied': True}, {'environment_adjustment_applied': False}],
            [0.5, 0.5],
        )
