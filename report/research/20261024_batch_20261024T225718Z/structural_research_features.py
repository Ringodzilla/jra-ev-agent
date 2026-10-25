"""Strict research analyzer; no current-market fields are read or generated."""
from __future__ import annotations
import math
import re
from datetime import date
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.feature_engineering import (
    summarize_history_rows, _map_score, _distance_fit_score, _condition_match_score,
)
from src.track_bias import track_bias_adjustment

HISTORY_FIELDS = ('position', 'time', 'distance', 'last_3f', 'passing_order', 'weight',
                  'course', 'history_surface', 'track_condition', 'jockey', 'date',
                  'run_index', 'popularity', 'odds', 'race_id', 'horse_id')
TARGET_FIELDS = ('target_track', 'target_surface', 'target_distance',
                 'target_track_condition', 'assigned_weight', 'current_jockey', 'frame_number',
                 'race_id', 'horse_id', 'horse_number', 'horse_name', 'target_race_date')
COMPONENT_WEIGHTS = {'ability_score': 0.4772727273, 'course_score': 0.1590909091,
                     'pace_score': 0.1818181818, 'weight_score': 0.0909090909,
                     'jockey_score': 0.0909090909}


def structural_features(history, target):
    """Caller must validate whole-race audit PASS and hashes before invoking."""
    if not history or len(history) > 5:
        raise ValueError('One to five fully observed histories required')
    for row in history:
        if any(key not in row or str(row[key]).strip() in {'', 'None', 'UNVERIFIED'} for key in HISTORY_FIELDS):
            raise ValueError('Required historical observation missing')
        for key in ('position', 'time', 'distance', 'last_3f', 'weight', 'odds', 'popularity'):
            if not math.isfinite(float(row[key])) or float(row[key]) <= 0:
                raise ValueError('Positive finite observed history required: ' + key)
        if date.fromisoformat(row['date']) >= date.fromisoformat(target['target_race_date']):
            raise ValueError('Non-past historical observation')
        if not re.fullmatch(r'\d+(?:-\d+)*', str(row['passing_order'])):
            raise ValueError('Observed corner order required')
        if row['history_surface'] not in {'芝', 'ダート'}:
            raise ValueError('Noncomparable historical surface')
        if row['track_condition'] not in {'良', '稍重', '重', '不良'}:
            raise ValueError('Observed historical condition required')
        if row['race_id'] != target['race_id'] or row['horse_id'] != target['horse_id']:
            raise ValueError('History identity mismatch')
    if any(key not in target or str(target[key]).strip() in {'', 'None', 'UNVERIFIED'} for key in TARGET_FIELDS):
        raise ValueError('Required target structural field missing')
    # Project only the explicitly contracted historical fields. Current market
    # fields are never looked up, even if present on the source dictionary.
    for key in ('target_distance', 'assigned_weight', 'frame_number'):
        if not math.isfinite(float(target[key])) or float(target[key]) <= 0:
            raise ValueError('Positive finite observed target required: ' + key)
    if target['target_surface'] != '芝' or target['target_track_condition'] not in {'良', '稍重', '重', '不良'}:
        raise ValueError('Observed turf target condition required')
    rows = [{key: row[key] for key in HISTORY_FIELDS} for row in history]
    summary = summarize_history_rows(rows, group_keys=('race_id', 'horse_id'))[
        (str(target['race_id']), str(target['horse_id']))]
    avg_finish = float(summary['avg_finish'])
    win_rate = float(summary['win_rate'])
    front_rate = float(summary['front_rate'])
    closing_strength = max(0.0, 36.0 - float(summary['avg_last3f'])) / 3.0
    ability = (0.34 / max(avg_finish, 1.0) + 0.18 * win_rate
               + 0.18 * float(summary['top3_rate']) + 0.18 * float(summary['speed_score'])
               + 0.12 * float(summary['consistency']))
    condition = _condition_match_score(summary['track_condition_map'], target['target_track_condition'])
    course = (0.40 * _map_score(summary['venue_map'], target['target_track'])
              + 0.20 * _map_score(summary['surface_map'], target['target_surface'])
              + 0.20 * _distance_fit_score(summary['avg_distance'], float(target['target_distance']))
              + 0.20 * condition)
    bias = track_bias_adjustment(track=target['target_track'], surface=target['target_surface'],
                                 distance=float(target['target_distance']),
                                 track_condition=target['target_track_condition'],
                                 frame_number=str(target['frame_number']), front_rate=front_rate)
    pace = max(0.0, min(1.5, (0.55 * front_rate + 0.45 * closing_strength)
                         + float(bias['track_bias_strength']) * float(bias['track_bias_score'])))
    weight = max(-1.5, min(1.5, -(float(target['assigned_weight']) - float(summary['avg_weight'])) / 3.0))
    jockey = 0.65 * _map_score(summary['jockey_map'], target['current_jockey']) + 0.35 * win_rate
    return {**{key: target[key] for key in TARGET_FIELDS},
            'history_count': len(history), 'front_rate': round(front_rate, 4),
            'closing_strength': round(closing_strength, 4), 'speed_score': round(float(summary['speed_score']), 4),
            'track_condition_score': round(condition, 4), 'consistency': round(float(summary['consistency']), 4),
            'ability_score': round(ability, 4), 'course_score': round(course, 4),
            'pace_score': round(pace, 4), 'weight_score': round(weight, 4),
            'jockey_score': round(jockey, 4)}
