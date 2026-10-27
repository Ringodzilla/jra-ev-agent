"""Synthetic fully observed cases only; no probability/EV/fit calls."""
import sys
sys.path.insert(0, '/Users/konishi/Documents/Playground/projects/jra-ev-agent/report/research/20261024_batch_20261024T225718Z')
from pathlib import Path
import hashlib
import json
from structural_research_features import structural_features, HISTORY_FIELDS, TARGET_FIELDS
from analysis.ev import build_feature_rows

base = Path(__file__).resolve().parent
checked = 0
for track, surface, distance in [('東京', '芝', 1600), ('京都', '芝', 2000), ('新潟', '芝', 1400)]:
    for count in range(1, 6):
        for condition in ('良', '稍重', '重', '不良'):
            rows = []
            for i in range(count):
                rows.append(dict(race_id='synthetic', horse_id='h1', horse_name='人工全実測',
                    horse_number='1', frame_number='2', date=f'2026-09-{20-i:02d}',
                    run_index=str(i+1), position=str(i+1), time=str(92+i), distance=str(distance),
                    last_3f=str(33+i*0.5), passing_order=f'{2+i}-{3+i}', weight=str(54+i),
                    course=track, history_surface=surface, track_condition=condition,
                    jockey='人工騎手', popularity=str(2+i), odds=str(3+i),
                    target_track=track, target_surface=surface, target_distance=str(distance),
                    target_track_condition=condition, target_race_date='2026-10-24',
                    assigned_weight='55', current_jockey='人工騎手',
                    current_odds='7.5', current_popularity='3'))
            expected = build_feature_rows(rows)[0]
            actual = structural_features(rows, rows[0])
            fields = ('ability_score', 'course_score', 'pace_score', 'weight_score', 'jockey_score',
                      'front_rate', 'closing_strength', 'speed_score', 'track_condition_score', 'consistency')
            for field in fields:
                assert actual[field] == expected[field], (track, count, condition, field, actual[field], expected[field])
            # Access traps prove the structural path never reads current-market fields.
            class NoMarket(dict):
                def __getitem__(self, key):
                    if key in {'current_odds', 'current_popularity', 'win_odds'}:
                        raise AssertionError('Forbidden market access')
                    return super().__getitem__(key)
                def get(self, key, default=None):
                    return self[key] if key in self else default
            assert structural_features([NoMarket(row) for row in rows], NoMarket(rows[0])) == actual
            checked += 1
result = {'record_type':'synthetic_structural_equivalence_verification','status':'PASS',
          'synthetic_cases':checked,'exact_fields':list(fields),
          'current_market_access_trap':'PASS','input_contract':{'history':HISTORY_FIELDS,'target':TARGET_FIELDS},
          'calculations_performed':['synthetic feature/component scores only'],
          'probability_ev_fit_calls':0,
          'code_refs':[{'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in
                       ['/Users/konishi/Documents/Playground/projects/jra-ev-agent/report/research/20261024_batch_20261024T225718Z/structural_research_features.py',Path('src/feature_engineering.py').resolve()]]}
p=base/'current_structural_feature_equivalence.json'
p.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'PASS','synthetic_cases':checked,'artifact':str(p)}))
