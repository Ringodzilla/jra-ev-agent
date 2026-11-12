"""Structured hypothesis registry for the Issue #40 hypothesis funnel.

Mirrors ``hypothesis_registry.md`` v1 (60 hypotheses) in machine-readable
form.  The markdown stays the human-readable original; the execution plan
pins its SHA-256, and a consistency test asserts that this table covers
exactly the markdown's hypothesis IDs, family by family.

Every hypothesis is a single difference against the common base form
``exact_structural_softmax_temperature1_no_mc_no_market`` (repo AGENTS.md
section 13).  Composite hypotheses are never constructed here.

Two registry entries carry a 2026-11-10 ruling (recorded in the markdown):
the final-odds-band diagnostic of UP-02 / UP-08 is report-only and never a
kill input (protocol section 9).  This table therefore applies only the
standard kills K1-K7 to those hypotheses.

PACE-03 / PACE-06 / PACE-07 are held, not dropped: the observations they
need are absent from the admission artifacts (see each entry's
``hold_reason``), so they stay in the total count N=60 with status held.
"""
from __future__ import annotations

import re
from pathlib import Path

REGISTRY_VERSION = 'v1'

FAMILY_COUNTS = {'ENV-T': 14, 'ENV-V': 4, 'ENV-D': 6, 'CAL': 8,
                 'SEG': 10, 'UP': 8, 'PACE': 10}

# Kill-criteria defaults per family (protocol section 5.2 notation in the
# registry's preamble).  Individual entries override where the registry
# says so explicitly.
_ENV_KILLS = ('K1', 'K2', 'K3', 'K4')
_SEG_KILLS = ('K1', 'K2', 'K3', 'K4', 'K6')
_UP_KILLS = ('K1', 'K2', 'K3', 'K7')

_L3 = (1.0, 10.0, 100.0)
_L2 = (1.0, 10.0)


def _env(hid, family, surface, variables, form, lambdas, kills=_ENV_KILLS,
         features='all4', control='no_env', **extra):
    spec = {'id': hid, 'family': family, 'kind': 'env', 'surface': surface,
            'control': control,
            'env': {'variables': tuple(variables), 'form': form,
                    'features': features},
            'grid': tuple({'lambda_env': float(v)} for v in lambdas),
            'kills': tuple(kills)}
    spec.update(extra)
    return spec


def _cal(hid, kind, grid, **extra):
    spec = {'id': hid, 'family': 'CAL', 'kind': kind, 'surface': 'both',
            'control': 'base_form', 'grid': tuple(grid),
            'kills': ('K1', 'K2', 'K3')}
    spec.update(extra)
    return spec


def _seg(hid, kind, grid, kills=_SEG_KILLS, **extra):
    spec = {'id': hid, 'family': 'SEG', 'kind': kind, 'surface': 'both',
            'control': 'cal01', 'grid': tuple(grid), 'kills': tuple(kills)}
    spec.update(extra)
    return spec


def _up(hid, kind, grid, **extra):
    spec = {'id': hid, 'family': 'UP', 'kind': kind, 'surface': 'both',
            'control': 'cal01', 'grid': tuple(grid), 'kills': _UP_KILLS}
    spec.update(extra)
    return spec


def _pace(hid, kind, grid, kills=('K1', 'K2', 'K3'), **extra):
    spec = {'id': hid, 'family': 'PACE', 'kind': kind, 'surface': 'both',
              'control': 'cal01', 'grid': tuple(grid),
              'kills': tuple(kills)}
    spec.update(extra)
    return spec


def _mult_grid():
    return tuple({'multiplier': m, 'ridge': r}
                 for m in (0.3, 1.0) for r in _L2)


def _ridge_grid(values=_L3):
    return tuple({'ridge': float(v)} for v in values)


HYPOTHESES = [
    # ---------------------------------------------------------- ENV-T (turf)
    _env('ENV-T01', 'ENV-T', '芝', ('cushion',), 'linear', _L3,
         kills=_ENV_KILLS + ('K5',)),
    _env('ENV-T02', 'ENV-T', '芝', ('moisture_goal', 'moisture_corner4'),
         'linear', _L3),
    _env('ENV-T03', 'ENV-T', '芝', ('moisture_mean',), 'linear', _L3),
    _env('ENV-T04', 'ENV-T', '芝',
         ('cushion', 'moisture_goal', 'moisture_corner4'), 'linear', _L3),
    _env('ENV-T05', 'ENV-T', '芝',
         ('rain', 'cloud', 'snow', 'cushion', 'moisture_goal',
          'moisture_corner4', 'condition'), 'linear', _L3),
    _env('ENV-T06', 'ENV-T', '芝',
         ('cushion', 'moisture_goal', 'moisture_corner4'), 'quadratic', _L3,
         must_beat=('ENV-T04',)),
    _env('ENV-T07', 'ENV-T', '芝', ('moisture_goal', 'moisture_corner4'),
         'quadratic', _L3, must_beat=('ENV-T02',)),
    _env('ENV-T08', 'ENV-T', '芝', ('cushion',), 'hinge', _L2,
         must_beat=('ENV-T01',)),
    _env('ENV-T09', 'ENV-T', '芝', ('moisture_mean',), 'hinge', _L2,
         must_beat=('ENV-T03',)),
    _env('ENV-T10', 'ENV-T', '芝', ('cushion', 'moisture_mean'), 'hinge',
         _L2, must_beat=('ENV-T08', 'ENV-T09')),
    _env('ENV-T11', 'ENV-T', '芝', ('cushion', 'moisture_mean'),
         'piecewise', _L2, must_beat=('ENV-T04',)),
    _env('ENV-T12', 'ENV-T', '芝', ('cushion',), 'piecewise', _L2,
         must_beat=('ENV-T01',)),
    _env('ENV-T13', 'ENV-T', '芝',
         ('cushion', 'moisture_goal', 'moisture_corner4'), 'linear', _L3,
         features='front_closing'),
    _env('ENV-T14', 'ENV-T', '芝',
         ('cushion', 'moisture_goal', 'moisture_corner4'), 'linear', _L3,
         features='speed_condition'),
    # ---------------------------------------------------------- ENV-V
    {'id': 'ENV-V01', 'family': 'ENV-V', 'kind': 'env_v_multiplier',
     'surface': '芝', 'control': 'env_t04',
     'env': {'target': 'cushion', 'group': 'venue'},
     'grid': tuple({'prior_scale': s, 'lambda_env': lam}
                   for s in (0.25, 0.5) for lam in _L2),
     'kills': _ENV_KILLS},
    {'id': 'ENV-V02', 'family': 'ENV-V', 'kind': 'env_v_multiplier',
     'surface': '芝', 'control': 'env_t04',
     'env': {'target': 'moisture', 'group': 'venue'},
     'grid': tuple({'prior_scale': s, 'lambda_env': lam}
                   for s in (0.25, 0.5) for lam in _L2),
     'kills': _ENV_KILLS},
    {'id': 'ENV-V03', 'family': 'ENV-V', 'kind': 'env_v_band',
     'surface': '芝', 'control': 'env_t04',
     'env': {'target': 'cushion', 'group': 'band'},
     'grid': tuple({'multiplier': m, 'lambda_env': lam}
                   for m in (1.0, 10.0) for lam in _L2),
     'kills': _ENV_KILLS + ('K5',)},
    {'id': 'ENV-V04', 'family': 'ENV-V', 'kind': 'env_v_band',
     'surface': '芝', 'control': 'env_t04',
     'env': {'target': 'moisture', 'group': 'band'},
     'grid': tuple({'multiplier': m, 'lambda_env': lam}
                   for m in (1.0, 10.0) for lam in _L2),
     'kills': _ENV_KILLS + ('K5',)},
    # ---------------------------------------------------------- ENV-D (dirt)
    _env('ENV-D01', 'ENV-D', 'ダート', ('moisture_goal', 'moisture_corner4'),
         'linear', _L3),
    _env('ENV-D02', 'ENV-D', 'ダート', ('moisture_mean',), 'linear', _L3),
    _env('ENV-D03', 'ENV-D', 'ダート', ('moisture_mean',), 'quadratic', _L3,
         must_beat=('ENV-D02',)),
    _env('ENV-D04', 'ENV-D', 'ダート', ('moisture_mean',), 'hinge', _L2,
         must_beat=('ENV-D02',)),
    _env('ENV-D05', 'ENV-D', 'ダート', ('moisture_mean',), 'piecewise', _L2,
         must_beat=('ENV-D02',)),
    _env('ENV-D06', 'ENV-D', 'ダート', ('moisture_goal', 'moisture_corner4'),
         'linear', _L3, features='front_closing'),
    # ---------------------------------------------------------- CAL
    _cal('CAL-01', 'cal_free', _ridge_grid(_L2),
         free=('ability_score', 'course_score', 'pace_score',
               'weight_score', 'jockey_score'),
         ability_sign_reference=2.25),
    _cal('CAL-02', 'cal_free', _ridge_grid(_L2),
         free=('ability_score', 'pace_score')),
    _cal('CAL-03', 'cal_temperature',
         tuple({'tau': t} for t in (1.25, 1.5, 2.0, 3.0))),
    _cal('CAL-04', 'cal_multiplicative', _ridge_grid(_L3),
         must_beat=('CAL-01',)),
    _cal('CAL-05', 'cal_temperature_ability',
         tuple({'tau': t, 'ridge': r}
               for t in (1.25, 1.5, 2.0) for r in _L2)),
    _cal('CAL-06', 'cal_free', _ridge_grid(_L2),
         free=('ability_score', 'course_score', 'pace_score',
               'weight_score', 'jockey_score'),
         bounds={'ability_score': (0.0, None)}),
    _cal('CAL-07', 'cal_decay',
         tuple({'half_life_days': d} for d in (30.0, 90.0)),
         must_beat=('CAL-01',)),
    _cal('CAL-08', 'cal_prior',
         tuple({'prior_penalty': p} for p in _L2),
         prior_raw={'ability_score': 2.25, 'course_score': 0.18,
                    'pace_score': 0.86, 'weight_score': -0.18,
                    'jockey_score': 0.16},
         must_beat=('CAL-01',)),
    # ---------------------------------------------------------- SEG
    _seg('SEG-01', 'seg_deviations', _mult_grid(),
         blocks=('venue',), scheme='fixed_multiplier', width=5),
    _seg('SEG-02', 'seg_deviations', _mult_grid(),
         blocks=('band4',), scheme='fixed_multiplier', width=5),
    _seg('SEG-03', 'seg_deviations',
         tuple({'multiplier': m, 'ridge': 10.0} for m in (0.3, 1.0)),
         blocks=('cell',), scheme='fixed_multiplier', width=5,
         min_cell_races=15, min_cells=3),
    _seg('SEG-04', 'seg_deviations',
         tuple({'floor': f} for f in (0.001, 0.01)),
         blocks=('venue', 'band4'), scheme='empirical_bayes', width=5,
         must_beat=('SEG-01', 'SEG-02')),
    _seg('SEG-05', 'seg_deviations', _mult_grid(),
         blocks=('venue', 'band4'), scheme='fixed_multiplier', width=1,
         component='ability_score'),
    _seg('SEG-06', 'seg_deviations', _mult_grid(),
         blocks=('venue', 'band4'), scheme='fixed_multiplier', width=1,
         component='pace_score'),
    _seg('SEG-07', 'seg_distance_slope', _ridge_grid(_L3),
         kills=('K1', 'K2', 'K3', 'K4'), must_beat=('SEG-02',)),
    _seg('SEG-08', 'seg_deviations', _mult_grid(),
         blocks=('band3',), scheme='fixed_multiplier', width=5,
         must_beat=('SEG-02',), lose_note='現行境界を維持'),
    _seg('SEG-09', 'seg_deviations', _mult_grid(),
         blocks=('band5',), scheme='fixed_multiplier', width=5,
         must_beat=('SEG-02',)),
    _seg('SEG-10', 'seg_deviations',
         tuple({'threshold': t, 'ridge': r}
               for t in (20, 30, 40) for r in _L2),
         blocks=('venue', 'band4'), scheme='hard_pooling', width=5,
         must_beat=('SEG-01', 'SEG-02')),
    # ---------------------------------------------------------- UP
    {'id': 'UP-01', 'family': 'UP', 'kind': 'diagnostic', 'surface': 'both',
     'control': 'cal01', 'grid': (), 'kills': (), 'diagnostic': True},
    _up('UP-02', 'up_piecewise_temperature',
        tuple({'tau_rel': t, 'band_split': b}
              for t in (0.8, 1.0, 1.25) for b in ('median', 'tertile'))),
    _up('UP-03', 'up_power', tuple({'gamma': g} for g in (0.85, 0.925))),
    _up('UP-04', 'up_tail_ability', _ridge_grid(_L3)),
    _up('UP-05', 'up_isotonic',
        tuple({'min_bin': n} for n in (200, 500)),
        must_beat=('CAL-01',)),
    _up('UP-06', 'up_race_temperature',
        tuple({'tau': t} for t in (0.85, 1.15))),
    {'id': 'UP-07', 'family': 'UP', 'kind': 'diagnostic', 'surface': 'both',
     'control': 'cal01', 'grid': (), 'kills': (), 'diagnostic': True},
    _up('UP-08', 'up_clip', tuple({'cap': c} for c in (1.0, 1.5, 2.0))),
    # ---------------------------------------------------------- PACE
    _pace('PACE-01', 'pace_ablation',
          ({'setting': 'pace_zero'}, {'setting': 'pace_fixed_037'}),
          pace_fixed_raw=0.86),
    _pace('PACE-02', 'pace_extra_term', _ridge_grid(_L3), term='front_rate'),
    {'id': 'PACE-03', 'family': 'PACE', 'kind': 'held', 'surface': 'both',
     'control': 'cal01', 'grid': (), 'kills': (),
     'hold_reason': '展開シナリオ確率（P(ハイ) - P(スロー)）がスクリーニング用'
                    '特徴量に含まれない。監査成果物は simulator のシナリオ確率を'
                    '保持していないため、必要観測なしとして保留する。'},
    _pace('PACE-04', 'pace_extra_term', _ridge_grid(_L3),
          term='closing_x_pressure'),
    _pace('PACE-05', 'pace_extra_term', _ridge_grid(_L3),
          term='front_x_pressure'),
    {'id': 'PACE-06', 'family': 'PACE', 'kind': 'held', 'surface': 'both',
     'control': 'cal01', 'grid': (), 'kills': (),
     'hold_reason': 'pace_score の距離正規化再定義には公式ラップの区間距離を'
                    '伴う観測が必要だが、監査成果物（normalized）は区間ラップを'
                    '保持していない。再定義を推定で埋めないため保留する。'},
    {'id': 'PACE-07', 'family': 'PACE', 'kind': 'held', 'surface': 'both',
     'control': 'cal01', 'grid': (), 'kills': (),
     'hold_reason': '「全出走馬のペース観測が揃ったレース」の判定に必要な'
                    '馬別ペース観測の完全性フラグが監査成果物に記録されて'
                    'いないため、推定対象を分離できず保留する。'},
    _pace('PACE-08', 'pace_split_adjust', _ridge_grid(_L2)),
    _pace('PACE-09', 'seg_deviations', _mult_grid(),
          kills=('K1', 'K2', 'K3', 'K4', 'K6'),
          blocks=('band4',), scheme='fixed_multiplier', width=1,
          component='pace_score'),
    _pace('PACE-10', 'pace_extra_term', _ridge_grid(_L3),
          term='pace_squared'),
]

BY_ID = {spec['id']: spec for spec in HYPOTHESES}

# Shortlist-stage preferences (registry text; not kills).  When both of a
# pair survive, the preferred entry is kept / ranked first.
SHORTLIST_PREFERENCES = (
    {'keep': 'ENV-T03', 'drop': 'ENV-T02',
     'reason': '両方通過した場合は項数の少ない方（ENV-T03）を優先'},
    {'keep': 'ENV-D02', 'drop': 'ENV-D01',
     'reason': '両方通過なら項数の少ない方（ENV-D02）を優先'},
    {'keep': 'better_fold_mean', 'pair': ('UP-02', 'UP-03'),
     'reason': 'UP-02 と UP-03 が両方通過なら fold 平均の良い方だけを残す'},
    {'keep': 'CAL-02', 'pair': ('CAL-01', 'CAL-02'), 'within': 0.005,
     'reason': 'CAL-01 との差が 0.005 以内なら単純な CAL-02 を優先'},
)

_ID_PATTERN = re.compile(r'^### ([A-Z]+-[A-Z]?\d{2}) ', re.MULTILINE)


def parse_registry_markdown(path) -> dict:
    """Hypothesis IDs by family, parsed from the markdown original."""
    text = Path(path).read_text(encoding='utf-8')
    found = {}
    for hid in _ID_PATTERN.findall(text):
        family = hid[:-2].rstrip('-')
        found.setdefault(family, []).append(hid)
    return found


def registry_sha256(path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
