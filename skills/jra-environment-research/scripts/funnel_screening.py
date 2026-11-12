"""Screening orchestration for the Issue #40 hypothesis funnel.

Implements protocol section 5: rolling-origin cross-validation inside
the integrated train pool (whole-day boundaries, no future leakage),
per-hypothesis controls (ablations), the frozen kill criteria K1-K7,
registry cross-kills and shortlist preferences, and the section 5.3
caps (8 total, 3 per family).

Only log-loss and Brier are recorded from ``condition_fit.evaluate``;
the win-policy ROI in that evaluator is a confirmation-gate metric and
is not a screening input, and final odds are never read by the engine
(poison-tested).  Reference blocks (the already-inspected migrated
validation/test) are reported with the same metrics but never feed kill
decisions.

Research track only: every record carries ``production_eligible=False``.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import condition_fit as cf  # noqa: E402
import funnel_engine as fe  # noqa: E402
import funnel_registry as fr  # noqa: E402

KILL_THRESHOLDS = {
    'K1_min_mean_improvement': 0.010,
    'K2_min_improved_folds': 3,
    'K2_max_worst_fold_worsening': 0.030,
    'K3_max_mean_brier_worsening': 0.002,
    'K4_min_mean_improvement': 0.005,
    'K6_min_shrinkage': 0.2,
    'K6_min_segment_races': 20,
    'K7_min_longshot_winners': 15,
}
SHORTLIST_CAP = 8
FAMILY_CAP = 3
CONFIRMATION_MINIMUM = {'train': 100, 'validation': 20, 'test': 20}


class Blocked(cf.Blocked):
    """Screening setup failure; never repaired silently."""


# ---------------------------------------------------------------------------
# Folds.
# ---------------------------------------------------------------------------

def make_folds(races, n_blocks=5):
    """Rolling-origin folds over whole race days.

    Train races are split into ``n_blocks`` contiguous day blocks of as
    equal race counts as possible; fold ``i`` fits on blocks ``0..i``
    and evaluates on block ``i+1`` (4 folds for 5 blocks).
    """
    dates = sorted({r['date'] for r in races})
    if len(dates) < n_blocks:
        raise Blocked('Not enough distinct race days for folds')
    by_date = {d: [r for r in races if r['date'] == d] for d in dates}
    total = len(races)
    target = total / n_blocks
    blocks, current, current_count = [], [], 0
    for day in dates:
        if current and current_count + len(by_date[day]) > target \
                and len(blocks) < n_blocks - 1:
            blocks.append(current)
            current, current_count = [], 0
        current.append(day)
        current_count += len(by_date[day])
    blocks.append(current)
    if len(blocks) != n_blocks:
        raise Blocked('Fold block construction failed')
    folds = []
    for i in range(n_blocks - 1):
        train_days = {d for block in blocks[:i + 1] for d in block}
        eval_days = set(blocks[i + 1])
        folds.append({
            'fold': i + 1,
            'train': [r for r in races if r['date'] in train_days],
            'eval': [r for r in races if r['date'] in eval_days],
            'train_days': sorted(train_days),
            'eval_days': sorted(eval_days),
        })
    return folds


def _subset(races, surface):
    if surface == 'both':
        return list(races)
    return [r for r in races if r['surface'] == surface]


def _metrics(races, model):
    result = cf.evaluate(races, lambda race: fe.model_probs(race, model))
    return {'log_loss': result['log_loss'], 'brier': result['brier'],
            'race_count': result['race_count']}


# ---------------------------------------------------------------------------
# Controls, evaluated per subset with their own grid selection.
# ---------------------------------------------------------------------------

class ControlCache:
    """Ablation controls fitted per (control, subset) with grid selection."""

    def __init__(self, train_races, reference_races):
        self._train = train_races
        self._reference = reference_races
        self._cache = {}

    def get(self, control, surface):
        key = (control, surface)
        if key in self._cache:
            return self._cache[key]
        train = _subset(self._train, surface)
        reference = _subset(self._reference, surface)
        folds = make_folds(train)
        if control == 'base_form':
            record = self._evaluate_grid(
                folds, reference, lambda tr, gp, ctx: None)
        elif control in ('cal01', 'no_env'):
            def fit(tr, gp, ctx):
                return fe.fit_global(tr, ridge=float(gp['ridge']))
            record = self._evaluate_grid(
                folds, reference, fit,
                grid=({'ridge': 1.0}, {'ridge': 10.0}))
        elif control == 'env_t04':
            def fit(tr, gp, ctx):
                return fe.fit_env(tr, fe._t04_spec(float(gp['lambda_env'])),
                                  float(gp['lambda_env']))
            record = self._evaluate_grid(
                folds, reference, fit,
                grid=({'lambda_env': 1.0}, {'lambda_env': 10.0},
                      {'lambda_env': 100.0}))
        else:  # pragma: no cover - registry guards control names
            raise Blocked(f'Unknown control: {control}')
        self._cache[key] = record
        return record

    @staticmethod
    def _evaluate_grid(folds, reference, fit, grid=(None,)):
        per_grid = []
        for grid_point in grid:
            fold_metrics = []
            for fold in folds:
                context = {'reference_date': min(fold['eval_days'])}
                model = fit(fold['train'], grid_point, context)
                fold_metrics.append(_metrics(fold['eval'], model))
            per_grid.append({
                'grid': grid_point,
                'folds': fold_metrics,
                'mean_log_loss': float(np.mean(
                    [m['log_loss'] for m in fold_metrics])),
            })
        selected = min(per_grid, key=lambda g: g['mean_log_loss'])
        full_train = folds[-1]['train'] + folds[-1]['eval']
        context = {'reference_date': None}
        full_model = fit(full_train, selected['grid'], context)
        reference_metrics = (_metrics(reference, full_model)
                             if reference else None)
        return {'selected_grid': selected['grid'],
                'folds': selected['folds'],
                'mean_log_loss': selected['mean_log_loss'],
                'reference': reference_metrics,
                'full_model': full_model}


# ---------------------------------------------------------------------------
# Hypothesis evaluation.
# ---------------------------------------------------------------------------

def _kill_verdicts(spec, fold_improvements, fold_brier_changes, meta_folds,
                   full_meta, k7_count):
    verdicts = {}
    kills = set(spec['kills'])
    mean_improvement = float(np.mean(fold_improvements))
    if 'K1' in kills:
        verdicts['K1'] = {
            'passed': mean_improvement
            >= KILL_THRESHOLDS['K1_min_mean_improvement'],
            'mean_improvement': mean_improvement}
    if 'K2' in kills:
        improved = sum(1 for v in fold_improvements if v > 0)
        worst = float(min(fold_improvements))
        verdicts['K2'] = {
            'passed': improved >= KILL_THRESHOLDS['K2_min_improved_folds']
            and worst >= -KILL_THRESHOLDS['K2_max_worst_fold_worsening'],
            'improved_folds': improved, 'worst_fold_improvement': worst}
    if 'K3' in kills:
        mean_brier = float(np.mean(fold_brier_changes))
        verdicts['K3'] = {
            'passed': mean_brier
            <= KILL_THRESHOLDS['K3_max_mean_brier_worsening'],
            'mean_brier_change': mean_brier}
    if 'K4' in kills:
        verdicts['K4'] = {
            'passed': mean_improvement
            >= KILL_THRESHOLDS['K4_min_mean_improvement'],
            'mean_improvement': mean_improvement}
    if 'K5' in kills:
        frozen_folds = sum(1 for m in meta_folds
                           if m.get('frozen_terms'))
        verdicts['K5'] = {
            'passed': frozen_folds <= len(meta_folds) // 2,
            'frozen_folds': frozen_folds}
    if 'K6' in kills:
        support = (full_meta or {}).get('support') or {}
        supported = [name for name, s in support.items() if s['supported']]
        verdicts['K6'] = {'passed': bool(supported),
                          'supported_segments': supported}
    if 'K7' in kills:
        verdicts['K7'] = {
            'passed': k7_count
            >= KILL_THRESHOLDS['K7_min_longshot_winners'],
            'longshot_winners': k7_count,
            'effect': 'blocks_confirmation_only'}
    return verdicts


def evaluate_hypothesis(spec, train_races, reference_races, controls,
                        k7_count):
    surface = spec['surface']
    train = _subset(train_races, surface)
    reference = _subset(reference_races, surface)
    folds = make_folds(train)
    control = controls.get(spec['control'], surface)
    grid_results, per_grid_folds, per_grid_meta = [], [], []
    for grid_point in spec['grid']:
        fold_records, fold_meta = [], []
        for fold in folds:
            context = {'reference_date': min(fold['eval_days'])}
            model = fe.fit_hypothesis(fold['train'], spec, grid_point,
                                      context)
            fold_records.append(_metrics(fold['eval'], model))
            fold_meta.append(model['funnel_meta'])
        grid_results.append({
            'grid': grid_point,
            'folds': fold_records,
            'mean_log_loss': float(np.mean(
                [m['log_loss'] for m in fold_records])),
            'mean_brier': float(np.mean(
                [m['brier'] for m in fold_records])),
        })
        per_grid_folds.append(fold_records)
        per_grid_meta.append(fold_meta)
    selected_idx = int(np.argmin([g['mean_log_loss'] for g in grid_results]))
    selected = grid_results[selected_idx]
    fold_records = per_grid_folds[selected_idx]
    fold_meta = per_grid_meta[selected_idx]
    improvements = [c['log_loss'] - f['log_loss']
                    for c, f in zip(control['folds'], fold_records)]
    brier_changes = [f['brier'] - c['brier']
                     for c, f in zip(control['folds'], fold_records)]
    full_model = fe.fit_hypothesis(train, spec,
                                   spec['grid'][selected_idx],
                                   {'reference_date': None})
    full_meta = full_model['funnel_meta']
    reference_metrics = (_metrics(reference, full_model)
                         if reference else None)
    verdicts = _kill_verdicts(spec, improvements, brier_changes, fold_meta,
                              full_meta, k7_count)
    record = {
        'id': spec['id'], 'family': spec['family'], 'kind': spec['kind'],
        'surface': surface, 'control': spec['control'],
        'status': 'evaluated',
        'selected_grid': spec['grid'][selected_idx],
        'grid_results': grid_results,
        'folds': [{'fold': fold['fold'],
                   'train_races': len(fold['train']),
                   'eval_races': len(fold['eval']),
                   'candidate': cand, 'control': ctrl,
                   'improvement': imp}
                  for fold, cand, ctrl, imp
                  in zip(folds, fold_records, control['folds'],
                         improvements)],
        'mean_log_loss': selected['mean_log_loss'],
        'mean_improvement': float(np.mean(improvements)),
        'kill_verdicts': verdicts,
        'reference': {'candidate': reference_metrics,
                      'control': control['reference']},
        'full_meta': {k: v for k, v in full_meta.items()
                      if k in ('support', 'segment_count', 'multipliers',
                               'adjust_raw', 'frozen_terms')},
        'notes': [],
        'production_eligible': False,
    }
    # Hypothesis-specific extra kills from the registry.
    if spec['id'] == 'CAL-01':
        signs = [m.get('adjust_raw', {}).get('ability_score', 0.0)
                 for m in fold_meta]
        negative = sum(1 for v in signs if v < 0)
        verdicts['ABILITY_SIGN'] = {
            'passed': negative <= len(signs) // 2,
            'negative_folds': negative,
            'fold_adjust_raw': signs}
    if spec['kind'] == 'env_v_multiplier':
        multipliers = (full_meta.get('multipliers') or {}).values()
        insensitive = all(0.9 <= v <= 1.1 for v in multipliers)
        verdicts['NO_SENSITIVITY'] = {'passed': not insensitive,
                                      'multipliers': dict(
                                          full_meta.get('multipliers')
                                          or {})}
    if spec['id'] == 'SEG-03':
        count = int(full_meta.get('segment_count', 0))
        verdicts['MIN_CELLS'] = {
            'passed': count >= int(spec.get('min_cells', 3)),
            'segment_count': count}
    return record


def _longshot_winner_count(folds_races):
    count = 0
    seen = set()
    for race in folds_races:
        if race['race_id'] in seen:
            continue
        seen.add(race['race_id'])
        for winner in race['winners']:
            if float(race['final_odds'][winner]) >= 10.0:
                count += 1
    return count


def _apply_cross_kills(records):
    by_id = {r['id']: r for r in records}
    for record in records:
        spec = fr.BY_ID[record['id']]
        for target in spec.get('must_beat', ()):
            other = by_id.get(target)
            if other is None or other.get('mean_log_loss') is None:
                record['notes'].append(
                    f'must_beat {target}: reference not evaluated; '
                    'rule skipped')
                continue
            passed = record['mean_log_loss'] < other['mean_log_loss']
            record['kill_verdicts'][f'MUST_BEAT_{target}'] = {
                'passed': passed,
                'candidate_mean_log_loss': record['mean_log_loss'],
                'reference_mean_log_loss': other['mean_log_loss']}


def _finalize_status(record, confirmation_counts):
    if record['status'] != 'evaluated':
        return
    verdicts = record['kill_verdicts']
    hard = {k: v for k, v in verdicts.items() if k != 'K7'}
    if any(not v['passed'] for v in hard.values()):
        record['status'] = 'killed'
        record['kill_reasons'] = [k for k, v in hard.items()
                                  if not v['passed']]
        return
    if 'K7' in verdicts and not verdicts['K7']['passed']:
        record['status'] = 'screening_only'
        record['kill_reasons'] = ['K7']
        return
    if record['surface'] == 'ダート':
        dirt_ok = all(confirmation_counts.get(block, {})
                      .get('ダート', 0) >= CONFIRMATION_MINIMUM[block]
                      for block in ('validation', 'test'))
        if not dirt_ok:
            record['status'] = 'screening_only'
            record['kill_reasons'] = [
                'dirt_confirmation_block_below_minimum']
            return
    record['status'] = 'passed'
    record['kill_reasons'] = []


def build_shortlist(records):
    passed = [r for r in records if r['status'] == 'passed']
    dropped = []
    by_id = {r['id']: r for r in passed}

    def drop(rid, reason):
        if rid in by_id:
            dropped.append({'id': rid, 'reason': reason})
            passed.remove(by_id.pop(rid))

    for pref in fr.SHORTLIST_PREFERENCES:
        if pref.get('drop') and pref['keep'] in by_id:
            drop(pref['drop'], pref['reason'])
        if pref.get('keep') == 'better_fold_mean':
            first, second = pref['pair']
            if first in by_id and second in by_id:
                worse = (first if by_id[first]['mean_log_loss']
                         >= by_id[second]['mean_log_loss'] else second)
                drop(worse, pref['reason'])
    cal_pair = [p for p in fr.SHORTLIST_PREFERENCES if 'within' in p]
    if cal_pair and 'CAL-01' in by_id and 'CAL-02' in by_id:
        diff = (by_id['CAL-01']['mean_improvement']
                - by_id['CAL-02']['mean_improvement'])
        if diff <= cal_pair[0]['within']:
            by_id['CAL-02']['rank_bonus'] = 1
    passed.sort(key=lambda r: (-r.get('rank_bonus', 0),
                               -r['mean_improvement']))
    shortlist, family_counts = [], {}
    for record in passed:
        family = record['family']
        if len(shortlist) >= SHORTLIST_CAP:
            dropped.append({'id': record['id'],
                            'reason': 'shortlist_cap_8'})
            continue
        if family_counts.get(family, 0) >= FAMILY_CAP:
            dropped.append({'id': record['id'],
                            'reason': 'family_cap_3'})
            continue
        family_counts[family] = family_counts.get(family, 0) + 1
        shortlist.append({'id': record['id'], 'family': family,
                          'mean_improvement': record['mean_improvement'],
                          'selected_grid': record['selected_grid']})
    return {'shortlist': shortlist, 'dropped_after_pass': dropped,
            'cap': SHORTLIST_CAP, 'family_cap': FAMILY_CAP}


def run_screening(races, confirmation_counts) -> dict:
    """Run the full screening over a loaded pool (train + reference)."""
    train_races = [r for r in races if r['pool_block'] == 'train']
    reference_races = [r for r in races if r['pool_block'] == 'reference']
    folds_all = make_folds(train_races)
    eval_races = [r for fold in folds_all for r in fold['eval']]
    k7_count = _longshot_winner_count(eval_races)
    controls = ControlCache(train_races, reference_races)
    records = []
    for spec in fr.HYPOTHESES:
        if spec['kind'] == 'held':
            records.append({
                'id': spec['id'], 'family': spec['family'],
                'kind': 'held', 'status': 'held',
                'hold_reason': spec['hold_reason'],
                'production_eligible': False})
            continue
        if spec['kind'] == 'diagnostic':
            records.append({
                'id': spec['id'], 'family': spec['family'],
                'kind': 'diagnostic', 'status': 'diagnostic',
                'note': '診断専用（レジストリ区分）。形の推定は行わず、'
                        '確認層へ進まない。',
                'production_eligible': False})
            continue
        records.append(evaluate_hypothesis(spec, train_races,
                                           reference_races, controls,
                                           k7_count))
    _apply_cross_kills(records)
    for record in records:
        _finalize_status(record, confirmation_counts)
    shortlist = build_shortlist(records)
    counts = {}
    for record in records:
        counts[record['status']] = counts.get(record['status'], 0) + 1
    return {
        'schema': 'funnel_screening_results_v1',
        'production_eligible': False,
        'n_hypotheses': len(records),
        'status_counts': counts,
        'k7_longshot_winners_in_folds': k7_count,
        'kill_thresholds': KILL_THRESHOLDS,
        'hypotheses': records,
        'shortlist': shortlist,
    }
