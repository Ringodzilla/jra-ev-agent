"""Surface base-form contract tests for the strict structural analyzer.

The common base form (skills/jra-environment-research/scripts/
structural_features.py) is inherited unchanged by every supported
surface; a base form only fixes the target surface it serves and the
environment observations its research connection requires.  Turf came
first; the dirt base form was added on 2026-11-09 under the user's
direction (「ダートも学習用に使おうよ」, Issue #39).  Jump （障害） is a
separate surface category and stays unsupported.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'jra-environment-research' / 'scripts'))

from structural_features import (  # noqa: E402
    SUPPORTED_TARGET_SURFACES,
    SURFACE_BASE_FORMS,
    structural_features,
    surface_base_form,
    surface_supported,
)


def _target(**overrides):
    target = dict(race_id='synthetic0', horse_id='h1', horse_name='人工1',
                  horse_number='1', frame_number='1', target_track='東京',
                  target_surface='芝', target_distance='1600',
                  target_track_condition='良', target_weather='晴',
                  target_race_date='2026-10-24', assigned_weight='55',
                  current_jockey='人工騎手')
    target.update(overrides)
    return target


def _history(target, **row_overrides):
    rows = []
    for run in (1, 2):
        row = dict(target, run_index=str(run), date=f'2026-09-{20 - run:02d}',
                   position=str(run), time='95', distance='1600',
                   last_3f='34', passing_order=f'1-{1 + run}', weight='55',
                   course=target['target_track'], history_surface='芝',
                   track_condition='良', jockey='人工騎手', popularity='1',
                   odds='3')
        row.update(row_overrides)
        rows.append(row)
    return rows


# Golden output of the turf base form for the fixture above, captured
# from the pre-extension code: the dirt extension must not move any
# turf value.
TURF_GOLDEN = {
    'ability_score': 0.7608, 'assigned_weight': '55',
    'closing_strength': 0.6667, 'consistency': 0.6667, 'course_score': 1.0,
    'current_jockey': '人工騎手', 'frame_number': '1', 'front_rate': 0.9141,
    'history_count': 2, 'horse_id': 'h1', 'horse_name': '人工1',
    'horse_number': '1', 'jockey_score': 0.8392, 'pace_score': 0.8028,
    'race_id': 'synthetic0', 'speed_score': 0.9474,
    'target_distance': '1600', 'target_race_date': '2026-10-24',
    'target_surface': '芝', 'target_track': '東京',
    'target_track_condition': '良', 'track_condition_score': 1.0,
    'weight_score': 0.0,
}


def test_supported_surfaces_are_turf_and_dirt_only():
    assert SUPPORTED_TARGET_SURFACES == {'芝', 'ダート'}
    assert surface_supported('芝') and surface_supported('ダート')
    assert not surface_supported('障害')
    assert surface_base_form('障害') is None


def test_surface_contract_environment_requirements():
    turf = SURFACE_BASE_FORMS['芝']
    assert 'cushion' in turf['environment_required']
    assert 'turf_moisture' in turf['environment_required']
    dirt = SURFACE_BASE_FORMS['ダート']
    # Dirt centers on moisture content: both measurement points required.
    assert dirt['environment_required'] == (
        'dirt_moisture_goal', 'dirt_moisture_corner4')
    # Cushion is a turf-course measurement: NOT_APPLICABLE for dirt,
    # never required and never substituted.
    assert 'cushion' in dirt['environment_not_applicable']
    assert 'cushion' not in dirt['environment_required']
    assert 'turf_moisture' in dirt['environment_not_applicable']


def test_turf_base_form_output_unchanged():
    target = _target()
    assert structural_features(_history(target), target) == TURF_GOLDEN


def test_dirt_base_form_computes_with_same_output_shape():
    target = _target(target_surface='ダート', target_distance='1800',
                     target_track='京都')
    history = _history(target, history_surface='ダート', distance='1800',
                       time='112', last_3f='38.5')
    result = structural_features(history, target)
    assert set(result) == set(TURF_GOLDEN)
    assert result['target_surface'] == 'ダート'
    assert result['history_count'] == 2
    for key in ('ability_score', 'course_score', 'pace_score',
                'weight_score', 'jockey_score'):
        assert isinstance(result[key], float)
    # Deterministic: a second call reproduces the first exactly.
    assert structural_features(history, target) == result


def test_dirt_base_form_accepts_mixed_surface_histories():
    target = _target(target_surface='ダート', target_distance='1800',
                     target_track='京都')
    history = _history(target, distance='1800', time='112', last_3f='38.5')
    history[0]['history_surface'] = 'ダート'
    history[1]['history_surface'] = '芝'
    result = structural_features(history, target)
    assert result['history_count'] == 2


def test_dirt_base_form_rejects_missing_target_field():
    target = _target(target_surface='ダート', target_distance='1800')
    history = _history(target, history_surface='ダート', distance='1800',
                       time='112', last_3f='38.5')
    del target['assigned_weight']
    with pytest.raises(ValueError):
        structural_features(history, target)


def test_dirt_base_form_rejects_unobserved_target_condition():
    target = _target(target_surface='ダート', target_distance='1800',
                     target_track_condition='')
    history = _history(target, history_surface='ダート', distance='1800',
                       time='112', last_3f='38.5')
    with pytest.raises(ValueError):
        structural_features(history, target)


def test_jump_target_still_rejected():
    target = _target(target_surface='障害', target_distance='3250')
    history = _history(target, history_surface='芝')
    with pytest.raises(ValueError):
        structural_features(history, target)


def test_jump_history_surface_still_rejected_for_dirt_target():
    target = _target(target_surface='ダート', target_distance='1800')
    history = _history(target, history_surface='障害', distance='1800',
                       time='112', last_3f='38.5')
    with pytest.raises(ValueError):
        structural_features(history, target)
