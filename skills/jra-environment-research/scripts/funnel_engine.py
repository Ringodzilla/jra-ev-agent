"""Estimation engine for the Issue #40 hypothesis funnel (integrated run).

Every hypothesis in ``funnel_registry`` is fitted here as a single
difference against the common base form, reusing ``condition_fit`` for the
base definitions (components, targets, metrics).  Where a hypothesis
cannot reuse ``condition_fit``'s two-stage fit directly, the generalized
penalized fit below reduces to ``condition_fit._fit_penalized`` in the
base case (proven by a synthetic equivalence test), and all evaluation
goes through ``condition_fit.evaluate`` so metric definitions never drift.

Final odds are never read here: designs are built from structural rows
and same-day environment observations only.  The poison test in the test
suite permutes final odds and asserts identical fits.

Research track only: fitted models are in-memory research objects and are
never connected to the production prediction pipeline.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import condition_fit as cf  # noqa: E402
from analysis.environment_model import HORSE_FEATURES  # noqa: E402

COMPONENTS = cf.COMPONENTS
CONDITIONS = {'良': 0.0, '稍重': 1 / 3, '重': 2 / 3, '不良': 1.0}
FEATURE_SUBSETS = {
    'all4': HORSE_FEATURES,
    'front_closing': ('front_rate', 'closing_strength'),
    'speed_condition': ('speed_score', 'track_condition_score'),
}
CONTINUOUS_ENV = ('cushion', 'moisture_goal', 'moisture_corner4',
                  'moisture_mean')


class Blocked(cf.Blocked):
    """Input or fit failure inside the funnel; never repaired silently."""


# ---------------------------------------------------------------------------
# Generalized penalized fit (reduces to cf._fit_penalized in the base case).
# ---------------------------------------------------------------------------

def penalized_fit(designs, offsets, targets, penalties, prior=None,
                  weights=None, bounds=None):
    """Conditional log-loss with per-column ridge penalties.

    Objective: ``0.5 * sum(pen * (theta - prior)^2)``
    ``+ sum_r w_r * loss_r / sum_r w_r`` where ``loss_r`` is the per-race
    conditional log-loss of ``offset_r + design_r @ theta``.  With
    ``prior=None``, ``weights=None`` and ``bounds=None`` this is exactly
    ``condition_fit._fit_penalized``.
    """
    penalties = np.asarray(penalties, dtype=float)
    width = designs[0].shape[1]
    prior = np.zeros(width) if prior is None else np.asarray(prior, float)
    if weights is None:
        weights = np.ones(len(designs))
    weights = np.asarray(weights, dtype=float)
    weight_total = float(weights.sum())
    shifted_offsets = [offset + design @ prior
                       for design, offset in zip(designs, offsets)]
    shifted_bounds = None
    if bounds is not None:
        shifted_bounds = [
            (None if lo is None else lo - prior[i],
             None if hi is None else hi - prior[i])
            for i, (lo, hi) in enumerate(bounds)]

    def objective(phi):
        value = 0.5 * float(penalties @ (phi ** 2))
        gradient = penalties * phi
        for design, offset, target, w in zip(
                designs, shifted_offsets, targets, weights):
            score = offset + design @ phi
            normalizer = logsumexp(score)
            probability = np.exp(score - normalizer)
            value += float(w * (normalizer - target @ score)) / weight_total
            gradient += (w / weight_total) * design.T @ (probability - target)
        return value, gradient

    result = minimize(objective, np.zeros(width), jac=True,
                      method='L-BFGS-B', bounds=shifted_bounds,
                      options={'maxiter': 1000, 'ftol': 1e-12,
                               'gtol': 1e-8})
    if not result.success or not np.isfinite(result.x).all():
        raise Blocked('Coefficient fit failed to converge')
    return prior + result.x


def _softmax(values):
    values = np.asarray(values, dtype=float)
    return np.exp(values - logsumexp(values))


def _log_base(race):
    return np.log(np.array([float(r['win_prob']) for r in race['rows']]))


def _targets(race):
    return cf._targets(race)


# ---------------------------------------------------------------------------
# Environment values and transformed designs.
# ---------------------------------------------------------------------------

def env_base_values(environment) -> dict:
    """Unit conversions identical to analysis.environment_model.

    The loader normalizes moisture to the target surface's own readings
    (``moisture_goal`` / ``moisture_corner4``); the cushion value exists
    only for turf and is ``None`` for dirt (never substituted).
    """
    weather = environment['weather']
    values = {
        'rain': float(weather in {'雨', '小雨'}),
        'cloud': float(weather == '曇'),
        'snow': float(weather in {'雪', '小雪'}),
        'condition': CONDITIONS[environment['track_condition']],
        'moisture_goal': (float(environment['moisture_goal']) - 8.0) / 4.0,
        'moisture_corner4': (float(environment['moisture_corner4']) - 8.0) / 4.0,
    }
    values['moisture_mean'] = (
        values['moisture_goal'] + values['moisture_corner4']) / 2.0
    cushion = environment.get('cushion')
    values['cushion'] = None if cushion is None else float(cushion) - 10.0
    return values


def _transform_params(train, variables, form) -> dict:
    params = {}
    if form not in ('hinge', 'piecewise'):
        return params
    base = [env_base_values(r['environment']) for r in train]
    for var in variables:
        series = np.array([b[var] for b in base], dtype=float)
        if form == 'hinge':
            params[var] = {'median': float(np.median(series))}
        else:
            q1, q2 = np.quantile(series, [1 / 3, 2 / 3])
            params[var] = {'tertiles': [float(q1), float(q2)]}
    return params


def _transformed(base_values, variables, form, params):
    """(name, value) pairs for one race's environment."""
    out = []
    for var in variables:
        value = base_values[var]
        if value is None:
            raise Blocked(f'Environment variable unavailable: {var}')
        if form == 'linear':
            out.append((var, value))
        elif form == 'quadratic':
            out.append((var, value))
            out.append((var + '_sq', value * value))
        elif form == 'hinge':
            median = params[var]['median']
            out.append((var + '_lo', min(value - median, 0.0)))
            out.append((var + '_hi', max(value - median, 0.0)))
        elif form == 'piecewise':
            t1, t2 = params[var]['tertiles']
            out.append((var, value))
            out.append((var + '_pw1', max(value - t1, 0.0)))
            out.append((var + '_pw2', max(value - t2, 0.0)))
        else:  # pragma: no cover - registry guards the form vocabulary
            raise Blocked(f'Unknown environment form: {form}')
    return out


def _env_columns(train, env_spec, params):
    """Transformed env column names, frozen names, and per-race values."""
    variables = env_spec['variables']
    form = env_spec['form']
    sample = _transformed(env_base_values(train[0]['environment']),
                          variables, form, params)
    names = [name for name, _ in sample]
    series = {name: [] for name in names}
    per_race = []
    for race in train:
        pairs = _transformed(env_base_values(race['environment']),
                             variables, form, params)
        per_race.append([value for _, value in pairs])
        for name, value in pairs:
            series[name].append(value)
    frozen = [name for name in names
              if float(np.ptp(series[name])) <= 1e-10]
    return names, frozen, per_race


def _env_design(race, env_spec, params, names):
    features = FEATURE_SUBSETS[env_spec.get('features', 'all4')]
    horse = np.array([[float(r[k]) for k in features] for r in race['rows']])
    horse = horse - horse.mean(axis=0)
    pairs = _transformed(env_base_values(race['environment']),
                         env_spec['variables'], env_spec['form'], params)
    values = np.array([v for _, v in pairs])
    if [n for n, _ in pairs] != names:
        raise Blocked('Environment column order mismatch')
    design = (horse[:, :, None] * values[None, None, :]).reshape(len(horse), -1)
    column_names = [f'{h}*{e}' for h in features for e in names]
    return design, column_names


# ---------------------------------------------------------------------------
# Global (CAL-01 family) fits.
# ---------------------------------------------------------------------------

def fit_global(train, free=COMPONENTS, fixed_raw=None, ridge=1.0,
               prior_raw=None, prior_penalty=None, weights=None,
               bounds_raw=None):
    fixed_raw = fixed_raw or {}
    comp = [cf.comp_centered(r) for r in train]
    comp_scales = cf._rms_scales(comp)
    free_idx = [COMPONENTS.index(k) for k in free]
    designs = [c[:, free_idx] / comp_scales[free_idx] for c in comp]
    offsets = []
    for race, centered in zip(train, comp):
        offset = _log_base(race)
        for name, raw in fixed_raw.items():
            offset = offset + raw * centered[:, COMPONENTS.index(name)]
        offsets.append(offset)
    prior = None
    if prior_raw is not None:
        prior = np.array([prior_raw[k] * comp_scales[COMPONENTS.index(k)]
                          for k in free])
    penalties = np.full(len(free_idx),
                        float(prior_penalty if prior_penalty is not None
                              else ridge))
    bounds = None
    if bounds_raw:
        bounds = []
        for k in free:
            lo, hi = bounds_raw.get(k, (None, None))
            scale = comp_scales[COMPONENTS.index(k)]
            bounds.append((None if lo is None else lo * scale,
                           None if hi is None else hi * scale))
    theta_free = penalized_fit(designs, offsets, [_targets(r) for r in train],
                               penalties, prior=prior, weights=weights,
                               bounds=bounds)
    theta = np.zeros(len(COMPONENTS))
    for pos, idx in enumerate(free_idx):
        theta[idx] = theta_free[pos]
    for name, raw in fixed_raw.items():
        theta[COMPONENTS.index(name)] = raw * comp_scales[COMPONENTS.index(name)]
    return {'form': 'global', 'comp_scales': comp_scales, 'theta': theta,
            'adjust_raw': theta / comp_scales,
            'training_races': len(train)}


def _global_scores(race, model):
    comp = cf.comp_centered(race) / model['comp_scales']
    return _log_base(race) + comp @ model['theta']


# ---------------------------------------------------------------------------
# Environment fits (ENV-T / ENV-D) and the no-environment control.
# ---------------------------------------------------------------------------

def fit_env(train, env_spec, lambda_env):
    params = _transform_params(train, env_spec['variables'],
                               env_spec['form'])
    names, frozen, _ = _env_columns(train, env_spec, params)
    features = FEATURE_SUBSETS[env_spec.get('features', 'all4')]
    frozen_columns = set()
    for h in features:
        for name in frozen:
            frozen_columns.add(f'{h}*{name}')
    comp = [cf.comp_centered(r) for r in train]
    comp_scales = cf._rms_scales(comp)
    comp = [c / comp_scales for c in comp]
    raw = []
    column_names = None
    for race in train:
        design, column_names = _env_design(race, env_spec, params, names)
        raw.append(design)
    active = np.array([name not in frozen_columns for name in column_names])
    masked = [m * active for m in raw]
    env_scales = cf._rms_scales(masked)
    designs = [np.hstack([c, m / env_scales])
               for c, m in zip(comp, masked)]
    penalties = np.concatenate([
        np.full(len(COMPONENTS), 1.0),
        np.full(len(column_names), float(lambda_env))])
    theta = penalized_fit(designs, [_log_base(r) for r in train],
                          [_targets(r) for r in train], penalties)
    return {'form': 'env', 'comp_scales': comp_scales,
            'theta_comp': theta[:len(COMPONENTS)],
            'env_coefficients': theta[len(COMPONENTS):],
            'env_scales': env_scales, 'env_active': active,
            'env_columns': column_names, 'env_names': names,
            'env_spec': {'variables': tuple(env_spec['variables']),
                         'form': env_spec['form'],
                         'features': env_spec.get('features', 'all4')},
            'transform_params': params, 'frozen_terms': frozen,
            'training_races': len(train)}


def _env_scores(race, model):
    comp = cf.comp_centered(race) / model['comp_scales']
    score = _log_base(race) + comp @ model['theta_comp']
    design, _ = _env_design(race, model['env_spec'], model['transform_params'],
                            model['env_names'])
    design = design * model['env_active'] / model['env_scales']
    return score + design @ model['env_coefficients']


# ---------------------------------------------------------------------------
# ENV-V: venue / band sensitivity of the ENV-T04 environment layer.
# ---------------------------------------------------------------------------

def _t04_spec(lambda_env):
    return {'variables': ('cushion', 'moisture_goal', 'moisture_corner4'),
            'form': 'linear', 'features': 'all4'}


def _target_columns(model, target):
    if target == 'cushion':
        wanted = {'cushion'}
    else:
        wanted = {'moisture_goal', 'moisture_corner4'}
    return np.array([name.split('*', 1)[1] in wanted
                     for name in model['env_columns']])


def _joint_env_refit(train, base, target_mask, groups, group_penalties,
                     lambda_env):
    """Refit env coefficients jointly with per-group sensitivity params.

    ``groups`` maps each race to a group index or -1.  For group races the
    target columns are multiplied by ``(1 + delta_group)``; non-target
    columns keep multiplier 1.  ``group_penalties`` holds the ridge
    precision of each delta (0 groups -> deltas absent).
    """
    designs, offsets, targets = [], [], []
    for race in train:
        design, _ = _env_design(race, base['env_spec'],
                                base['transform_params'], base['env_names'])
        designs.append(design * base['env_active'] / base['env_scales'])
        comp = cf.comp_centered(race) / base['comp_scales']
        offsets.append(_log_base(race) + comp @ base['theta_comp'])
        targets.append(_targets(race))
    n_env = designs[0].shape[1]
    n_groups = len(group_penalties)
    count = len(train)

    def objective(params):
        beta = params[:n_env]
        deltas = params[n_env:]
        value = 0.5 * float(lambda_env) * float(beta @ beta)
        gradient = np.concatenate([float(lambda_env) * beta,
                                   np.zeros(n_groups)])
        if n_groups:
            value += 0.5 * float(group_penalties @ (deltas ** 2))
            gradient[n_env:] = group_penalties * deltas
        for design, offset, target, group in zip(
                designs, offsets, targets, groups):
            mult = np.ones(n_env)
            if group >= 0:
                mult = np.where(target_mask,
                                1.0 + deltas[group], 1.0)
            score = offset + design @ (beta * mult)
            normalizer = logsumexp(score)
            probability = np.exp(score - normalizer)
            value += float(normalizer - target @ score) / count
            grad_score = (probability - target) / count
            gradient[:n_env] += mult * (design.T @ grad_score)
            if group >= 0:
                gradient[n_env + group] += float(
                    grad_score @ (design[:, target_mask] @ beta[target_mask]))
        return value, gradient

    result = minimize(objective, np.zeros(n_env + n_groups), jac=True,
                      method='L-BFGS-B',
                      options={'maxiter': 1000, 'ftol': 1e-12,
                               'gtol': 1e-8})
    if not result.success or not np.isfinite(result.x).all():
        raise Blocked('ENV-V joint fit failed to converge')
    return result.x[:n_env], result.x[n_env:]


def fit_env_v(train, spec, grid_point):
    lambda_env = float(grid_point['lambda_env'])
    base = fit_env(train, _t04_spec(lambda_env), lambda_env)
    target_mask = _target_columns(base, spec['env']['target'])
    if spec['kind'] == 'env_v_multiplier':
        levels = sorted({r['venue'] for r in train})
        groups = [levels.index(r['venue']) for r in train]
        penalties = np.full(len(levels),
                            1.0 / float(grid_point['prior_scale']) ** 2)
        beta, deltas = _joint_env_refit(train, base, target_mask, groups,
                                        penalties, lambda_env)
        return {'form': 'env_v_multiplier', 'base': base, 'beta': beta,
                'deltas': {level: float(deltas[i])
                           for i, level in enumerate(levels)},
                'target_mask': target_mask,
                'multipliers': {level: 1.0 + float(deltas[i])
                                for i, level in enumerate(levels)},
                'training_races': len(train)}
    # env_v_band: global beta plus per-band deviations on the target vars.
    bands = [b for b in cf.BAND_ORDER if any(r['band'] == b for r in train)]
    frozen = []
    base_values = [env_base_values(r['environment']) for r in train]
    target_vars = ('cushion',) if spec['env']['target'] == 'cushion' else (
        'moisture_goal', 'moisture_corner4')
    usable = []
    for band in bands:
        series = [base_values[i][target_vars[0]]
                  for i, r in enumerate(train) if r['band'] == band]
        if float(np.ptp(series)) <= 1e-10:
            frozen.append(band)
        else:
            usable.append(band)
    # Reuse the joint machinery: one "delta" per band acting on the target
    # columns through a separate gamma vector is a bilinear form, so the
    # band model is fitted with explicit gamma parameters instead.
    return _fit_env_v_band(train, base, target_mask, bands, usable, frozen,
                           lambda_env, float(grid_point['multiplier']))


def _fit_env_v_band(train, base, target_mask, bands, usable, frozen,
                    lambda_env, multiplier):
    designs, offsets, targets = [], [], []
    for race in train:
        design, _ = _env_design(race, base['env_spec'],
                                base['transform_params'], base['env_names'])
        designs.append(design * base['env_active'] / base['env_scales'])
        comp = cf.comp_centered(race) / base['comp_scales']
        offsets.append(_log_base(race) + comp @ base['theta_comp'])
        targets.append(_targets(race))
    n_env = designs[0].shape[1]
    n_target = int(target_mask.sum())
    band_index = {band: i for i, band in enumerate(usable)}
    count = len(train)

    def objective(params):
        beta = params[:n_env]
        gamma = params[n_env:].reshape(len(usable), n_target)
        value = 0.5 * float(lambda_env) * float(beta @ beta)
        value += 0.5 * float(lambda_env * multiplier) * float(
            (gamma ** 2).sum())
        gradient = np.zeros_like(params)
        gradient[:n_env] = float(lambda_env) * beta
        gradient[n_env:] = float(lambda_env * multiplier) * gamma.ravel()
        for design, offset, target, race in zip(
                designs, offsets, targets, train):
            score = offset + design @ beta
            band = race['band']
            if band in band_index:
                score = score + design[:, target_mask] @ gamma[
                    band_index[band]]
            normalizer = logsumexp(score)
            probability = np.exp(score - normalizer)
            value += float(normalizer - target @ score) / count
            grad_score = (probability - target) / count
            gradient[:n_env] += design.T @ grad_score
            if band in band_index:
                gradient[n_env:].reshape(len(usable), n_target)[
                    band_index[band]] += design[:, target_mask].T @ grad_score
        return value, gradient

    result = minimize(objective,
                      np.zeros(n_env + len(usable) * n_target), jac=True,
                      method='L-BFGS-B',
                      options={'maxiter': 1000, 'ftol': 1e-12,
                               'gtol': 1e-8})
    if not result.success or not np.isfinite(result.x).all():
        raise Blocked('ENV-V band fit failed to converge')
    return {'form': 'env_v_band', 'base': base,
            'beta': result.x[:n_env],
            'gamma': result.x[n_env:].reshape(len(usable), n_target),
            'usable_bands': usable, 'target_mask': target_mask,
            'frozen_terms': list(frozen), 'training_races': len(train)}


def _env_v_scores(race, model):
    base = model['base']
    comp = cf.comp_centered(race) / base['comp_scales']
    score = _log_base(race) + comp @ base['theta_comp']
    design, _ = _env_design(race, base['env_spec'], base['transform_params'],
                            base['env_names'])
    design = design * base['env_active'] / base['env_scales']
    if model['form'] == 'env_v_multiplier':
        mult = np.ones(design.shape[1])
        delta = model['deltas'].get(race['venue'], 0.0)
        mult = np.where(model['target_mask'], 1.0 + delta, 1.0)
        return score + design @ (model['beta'] * mult)
    score = score + design @ model['beta']
    if race['band'] in model['usable_bands']:
        idx = model['usable_bands'].index(race['band'])
        score = score + design[:, model['target_mask']] @ model['gamma'][idx]
    return score


# ---------------------------------------------------------------------------
# Segment deviations (SEG family and PACE-09).
# ---------------------------------------------------------------------------

def band3_name(distance) -> str:
    if distance <= 1600:
        return '短距離（〜1600m）'
    if distance <= 2200:
        return '中距離（1601–2200m）'
    return '長距離（2201m〜）'


def band5_name(distance) -> str:
    if distance <= 1200:
        return 'スプリント（〜1200m）'
    if distance <= 1600:
        return 'マイル（1301–1600m）'
    if distance <= 2000:
        return '中距離（1701–2000m）'
    if distance <= 2400:
        return '長距離（2101–2400m）'
    return '長距離+（2501m〜）'


def _segment_key(race, kind):
    if kind == 'venue':
        return race['venue']
    if kind == 'band4':
        return race['band']
    if kind == 'band3':
        return band3_name(race['distance'])
    if kind == 'band5':
        return band5_name(race['distance'])
    raise Blocked(f'Unknown segment kind: {kind}')  # pragma: no cover


def _segment_list(train, spec):
    """[(kind, name, n)] segments admitted by the hypothesis spec."""
    segments = []
    for kind in spec['blocks']:
        if kind == 'cell':
            counts = {}
            for race in train:
                key = (race['venue'], race['band'])
                counts[key] = counts.get(key, 0) + 1
            for (venue, band), n in sorted(counts.items()):
                if n >= int(spec.get('min_cell_races', 15)):
                    segments.append(('cell', f'{venue}|{band}', n))
        else:
            counts = {}
            for race in train:
                key = _segment_key(race, kind)
                counts[key] = counts.get(key, 0) + 1
            for name, n in sorted(counts.items()):
                segments.append((kind, name, n))
    return segments


def _segment_match(race, kind, name):
    if kind == 'cell':
        venue, band = name.split('|', 1)
        return race['venue'] == venue and race['band'] == band
    return _segment_key(race, kind) == name


def _segment_designs(train, base, segments, width, component):
    comp = [cf.comp_centered(r) / base['comp_scales'] for r in train]
    designs = []
    for race, centered in zip(train, comp):
        if width == len(COMPONENTS):
            source = centered
        else:
            source = centered[:, [COMPONENTS.index(component)]]
        parts = [source if _segment_match(race, kind, name)
                 else np.zeros_like(source)
                 for kind, name, _ in segments]
        designs.append(np.hstack(parts) if parts
                       else np.zeros((len(race['rows']), 0)))
    return designs


def _solve_segments(train, base, segments, width, component, penalties,
                    reference_penalties):
    designs = _segment_designs(train, base, segments, width, component)
    offsets = [_global_scores(r, base) for r in train]
    targets = [_targets(r) for r in train]
    theta = penalized_fit(designs, offsets, targets, penalties)
    reference = penalized_fit(designs, offsets, targets,
                              reference_penalties)
    deviations, shrinkage, support = {}, {}, {}
    index = 0
    for kind, name, n in segments:
        block = theta[index:index + width]
        ref = reference[index:index + width]
        deviations[(kind, name)] = block
        ref_norm = float(np.linalg.norm(ref))
        shrink = (float(np.linalg.norm(block) / ref_norm)
                  if ref_norm > 1e-9 else None)
        shrinkage[(kind, name)] = shrink
        support[f'{kind}:{name}'] = {
            'n': n, 'shrinkage': shrink,
            'supported': bool(shrink is not None and shrink >= 0.2
                              and n >= 20)}
        index += width
    return deviations, shrinkage, support


def _eb_penalties(train, base, segments, width, component, floor):
    """Method-of-moments empirical-Bayes penalties (plan-frozen formula).

    A reference fit with multiplier 0.01 gives deviations ``d_s`` and a
    Hessian-based sampling variance ``v_s`` per segment.  The between-
    segment variance is ``tau2 = max(mean(||d_s||^2 / width) - mean(v_s),
    floor)`` and the final penalty is ``(N / n_s) * (vbar / tau2)`` in the
    repo's penalty convention (ridge base 1.0).
    """
    total = len(train)
    ref_penalties = np.concatenate([
        np.full(width, 1.0 * 0.01 * (total / n)) for _, _, n in segments])
    designs = _segment_designs(train, base, segments, width, component)
    offsets = [_global_scores(r, base) for r in train]
    targets = [_targets(r) for r in train]
    theta = penalized_fit(designs, offsets, targets, ref_penalties)
    # Hessian of the mean loss at the reference fit, plus penalty precision.
    hessian = np.diag(ref_penalties.astype(float))
    for design, offset, target in zip(designs, offsets, targets):
        score = offset + design @ theta
        probability = _softmax(score)
        weight_matrix = np.diag(probability) - np.outer(probability,
                                                        probability)
        hessian += design.T @ weight_matrix @ design / total
    covariance = np.linalg.pinv(hessian)
    variances, index = [], 0
    for _, _, _n in segments:
        block = np.diag(covariance)[index:index + width]
        variances.append(float(np.mean(block)))
        index += width
    deviations, index = [], 0
    for _, _, _n in segments:
        deviations.append(theta[index:index + width])
        index += width
    vbar = float(np.mean(variances)) if variances else 0.0
    signal = (float(np.mean([float(d @ d) / width for d in deviations]))
              if deviations else 0.0)
    tau2 = max(signal - vbar, float(floor))
    penalties = np.concatenate([
        np.full(width, (total / n) * (vbar / tau2)) for _, _, n in segments])
    return penalties, {'tau2': float(tau2), 'vbar': vbar,
                       'signal': signal}


def fit_segments(train, spec, grid_point):
    if spec['scheme'] == 'empirical_bayes':
        ridge = 1.0
    else:
        ridge = float(grid_point.get('ridge', 1.0))
    base = fit_global(train, ridge=ridge)
    segments = _segment_list(train, spec)
    if spec['scheme'] == 'hard_pooling':
        threshold = int(grid_point['threshold'])
        segments = [s for s in segments if s[2] >= threshold]
    width = int(spec['width'])
    component = spec.get('component')
    total = len(train)
    if not segments:
        # No segment meets the hypothesis' own admission rule (e.g.
        # SEG-03 cells below the minimum race count): the candidate
        # degenerates to the global fit and K6 / MIN_CELLS decide.
        return {'form': 'segments', 'base': base, 'segments': [],
                'deviations': {}, 'shrinkage': {}, 'support': {},
                'width': width, 'component': component, 'eb': None,
                'training_races': len(train)}
    if spec['scheme'] == 'empirical_bayes':
        penalties, eb = _eb_penalties(train, base, segments, width,
                                      component, grid_point['floor'])
        reference = np.concatenate([
            np.full(width, 0.01 * (total / n)) for _, _, n in segments])
    else:
        multiplier = float(grid_point.get('multiplier', 1.0))
        penalties = np.concatenate([
            np.full(width, ridge * multiplier * (total / n))
            for _, _, n in segments])
        reference = np.concatenate([
            np.full(width, ridge * 0.01 * (total / n))
            for _, _, n in segments])
        eb = None
    deviations, shrinkage, support = _solve_segments(
        train, base, segments, width, component, penalties, reference)
    return {'form': 'segments', 'base': base, 'segments': segments,
            'deviations': deviations, 'shrinkage': shrinkage,
            'support': support, 'width': width, 'component': component,
            'eb': eb, 'training_races': len(train)}


def _segment_scores(race, model):
    score = _global_scores(race, model['base'])
    centered = cf.comp_centered(race) / model['base']['comp_scales']
    if model['width'] == len(COMPONENTS):
        source = centered
    else:
        source = centered[:, [COMPONENTS.index(model['component'])]]
    for (kind, name), deviation in model['deviations'].items():
        if _segment_match(race, kind, name):
            score = score + source @ deviation
    return score


def fit_distance_slope(train, grid_point):
    base = fit_global(train, ridge=1.0)
    log_distance = np.array([math.log(r['distance']) for r in train])
    center = float(log_distance.mean())
    scale = float(np.sqrt(np.mean((log_distance - center) ** 2))) or 1.0
    designs, offsets = [], []
    for race in train:
        g = (math.log(race['distance']) - center) / scale
        designs.append(cf.comp_centered(race) / base['comp_scales'] * g)
        offsets.append(_global_scores(race, base))
    theta = penalized_fit(designs, offsets, [_targets(r) for r in train],
                          np.full(len(COMPONENTS),
                                  float(grid_point['ridge'])))
    return {'form': 'distance_slope', 'base': base, 'theta': theta,
            'center': center, 'scale': scale,
            'training_races': len(train)}


def _distance_slope_scores(race, model):
    g = (math.log(race['distance']) - model['center']) / model['scale']
    design = cf.comp_centered(race) / model['base']['comp_scales'] * g
    return _global_scores(race, model['base']) + design @ model['theta']


# ---------------------------------------------------------------------------
# Extra single terms (UP-04, PACE-02/04/05/10) and PACE-08.
# ---------------------------------------------------------------------------

def _term_column(race, term):
    rows = race['rows']
    front = np.array([float(r['front_rate']) for r in rows])
    closing = np.array([float(r['closing_strength']) for r in rows])
    ability = np.array([float(r['ability_score']) for r in rows])
    pace = np.array([float(r['pace_score']) for r in rows])
    if term == 'front_rate':
        return front - front.mean()
    if term == 'closing_x_pressure':
        return (closing - closing.mean()) * float(front.mean())
    if term == 'front_x_pressure':
        return (front - front.mean()) * float(front.mean())
    if term == 'pace_squared':
        return (pace - pace.mean()) ** 2
    if term == 'tail_ability':
        threshold = float(np.quantile(ability, 0.75))
        return ability * (ability >= threshold)
    raise Blocked(f'Unknown extra term: {term}')  # pragma: no cover


def fit_extra_term(train, base, term, ridge):
    columns = [_term_column(r, term) for r in train]
    scale = float(np.sqrt(np.mean(np.concatenate(columns) ** 2))) or 1.0
    designs = [(c / scale).reshape(-1, 1) for c in columns]
    offsets = [model_scores(r, base) for r in train]
    theta = penalized_fit(designs, offsets, [_targets(r) for r in train],
                          np.array([float(ridge)]))
    return {'form': 'extra_term', 'base': base, 'term': term,
            'coef': float(theta[0]), 'scale': scale,
            'training_races': len(train)}


def _extra_term_scores(race, model):
    column = _term_column(race, model['term']) / model['scale']
    return model_scores(race, model['base']) + column * model['coef']


def fit_pace_split(train, ridge):
    comp = [cf.comp_centered(r) for r in train]
    comp_scales = cf._rms_scales(comp)
    kept = [k for k in COMPONENTS if k != 'pace_score']
    kept_idx = [COMPONENTS.index(k) for k in kept]
    extras = []
    for race in train:
        front = np.array([float(r['front_rate']) for r in race['rows']])
        closing = np.array([float(r['closing_strength'])
                            for r in race['rows']])
        extras.append(np.column_stack([
            0.55 * (front - front.mean()),
            0.45 * (closing - closing.mean())]))
    extra_scales = cf._rms_scales(extras)
    designs = [np.hstack([c[:, kept_idx] / comp_scales[kept_idx],
                          e / extra_scales])
               for c, e in zip(comp, extras)]
    penalties = np.concatenate([np.full(len(kept_idx), 1.0),
                                np.full(2, float(ridge))])
    theta = penalized_fit(designs, [_log_base(r) for r in train],
                          [_targets(r) for r in train], penalties)
    return {'form': 'pace_split', 'comp_scales': comp_scales,
            'kept': kept, 'theta_comp': theta[:len(kept_idx)],
            'extra_scales': extra_scales, 'theta_extra': theta[len(kept_idx):],
            'training_races': len(train)}


def _pace_split_scores(race, model):
    centered = cf.comp_centered(race)
    kept_idx = [COMPONENTS.index(k) for k in model['kept']]
    front = np.array([float(r['front_rate']) for r in race['rows']])
    closing = np.array([float(r['closing_strength']) for r in race['rows']])
    extras = np.column_stack([0.55 * (front - front.mean()),
                              0.45 * (closing - closing.mean())])
    design = np.hstack([centered[:, kept_idx] / model['comp_scales'][kept_idx],
                        extras / model['extra_scales']])
    theta = np.concatenate([model['theta_comp'], model['theta_extra']])
    return _log_base(race) + design @ theta


# ---------------------------------------------------------------------------
# UP transforms and isotonic calibration.
# ---------------------------------------------------------------------------

def fit_isotonic(ps, ys, min_bin):
    """Weighted PAVA isotonic regression with a minimum starters-per-bin.

    Bins are formed greedily in probability order with at least
    ``min_bin`` starters each (a short final bin merges into its
    predecessor), then pool-adjacent-violators enforces monotonicity.
    """
    ps = np.asarray(ps, dtype=float)
    ys = np.asarray(ys, dtype=float)
    order = np.argsort(ps, kind='stable')
    ps, ys = ps[order], ys[order]
    bins = []
    start = 0
    while start < len(ps):
        end = min(start + int(min_bin), len(ps))
        if len(ps) - end < int(min_bin):
            end = len(ps)
        bins.append([start, end])
        start = end
    blocks = []
    for start, end in bins:
        blocks.append({'start': start, 'end': end,
                       'value': float(ys[start:end].mean()),
                       'weight': float(end - start)})
    index = 0
    while index < len(blocks) - 1:
        if blocks[index]['value'] > blocks[index + 1]['value']:
            merged = {
                'start': blocks[index]['start'],
                'end': blocks[index + 1]['end'],
                'value': (blocks[index]['value'] * blocks[index]['weight']
                          + blocks[index + 1]['value']
                          * blocks[index + 1]['weight'])
                / (blocks[index]['weight'] + blocks[index + 1]['weight']),
                'weight': blocks[index]['weight']
                + blocks[index + 1]['weight']}
            blocks[index:index + 2] = [merged]
            index = max(0, index - 1)
        else:
            index += 1
    edges = [float(ps[b['end'] - 1]) for b in blocks]
    values = [b['value'] for b in blocks]
    return {'form': 'isotonic', 'edges': edges, 'values': values,
            'training_starters': len(ps)}


def _isotonic_probs(race, model):
    p0 = np.array([float(r['win_prob']) for r in race['rows']])
    idx = np.searchsorted(model['edges'], p0, side='left')
    idx = np.minimum(idx, len(model['values']) - 1)
    values = np.array([model['values'][i] for i in idx])
    values = np.clip(values, 1e-6, 1.0)
    return values / values.sum()


def _transform_scores(race, model):
    base_probs = model_probs(race, model['base'])
    log_q = np.log(np.maximum(base_probs, 1e-300))
    kind = model['kind']
    if kind == 'up_piecewise_temperature':
        if model['band_split'] == 'median':
            threshold = float(np.median(base_probs))
            upper = base_probs >= threshold
        else:
            low, high = np.quantile(base_probs, [1 / 3, 2 / 3])
            upper = base_probs >= high
            _ = low
        tau = np.where(upper, 1.0, float(model['tau_rel']))
        return log_q / tau
    if kind == 'up_power':
        median = float(np.median(base_probs))
        gamma = float(model['gamma'])
        return np.where(base_probs < median, gamma * log_q, log_q)
    if kind == 'up_race_temperature':
        high_uncertainty = (len(base_probs) >= 16
                            or float(base_probs.max()) < 0.25)
        if high_uncertainty:
            return float(model['tau']) * log_q
        return log_q
    if kind == 'up_clip':
        adjustment = log_q - _log_base(race)
        clipped = np.clip(adjustment, -float(model['cap']),
                          float(model['cap']))
        return _log_base(race) + clipped
    raise Blocked(f'Unknown transform: {kind}')  # pragma: no cover


# ---------------------------------------------------------------------------
# Unified dispatch.
# ---------------------------------------------------------------------------

def model_scores(race, model):
    form = model['form']
    if form == 'global':
        return _global_scores(race, model)
    if form == 'env':
        return _env_scores(race, model)
    if form in ('env_v_multiplier', 'env_v_band'):
        return _env_v_scores(race, model)
    if form == 'segments':
        return _segment_scores(race, model)
    if form == 'distance_slope':
        return _distance_slope_scores(race, model)
    if form == 'extra_term':
        return _extra_term_scores(race, model)
    if form == 'pace_split':
        return _pace_split_scores(race, model)
    if form == 'temperature':
        return float(model['tau']) * _log_base(race)
    if form == 'multiplicative':
        centered = cf.comp_centered(race)
        raw_sum = np.array([[float(r[k]) for k in COMPONENTS]
                            for r in race['rows']]).sum(axis=1)
        return (_log_base(race) + raw_sum
                + (centered / model['comp_scales']) @ model['theta'])
    if form == 'temp_ability':
        ability = cf.comp_centered(race)[:, COMPONENTS.index('ability_score')]
        return (float(model['tau']) * _log_base(race)
                + (ability / model['scale']) * model['theta'])
    if form == 'transform':
        return _transform_scores(race, model)
    raise Blocked(f'Unknown model form: {form}')  # pragma: no cover


def model_probs(race, model):
    if model is None:
        return np.array([float(r['win_prob']) for r in race['rows']])
    if model['form'] == 'isotonic':
        return _isotonic_probs(race, model)
    return _softmax(model_scores(race, model))


def fit_hypothesis(train, spec, grid_point, context=None):
    """Fit one registry hypothesis on a training subset.

    ``context`` may carry ``reference_date`` (CAL-07 decay weights anchor).
    Returns the fitted model dict; metadata used by kill criteria is
    attached under ``funnel_meta``.
    """
    context = context or {}
    kind = spec['kind']
    meta = {}
    if kind == 'env':
        model = fit_env(train, spec['env'], float(grid_point['lambda_env']))
        meta['frozen_terms'] = list(model['frozen_terms'])
    elif kind in ('env_v_multiplier', 'env_v_band'):
        model = fit_env_v(train, spec, grid_point)
        meta['frozen_terms'] = list(model.get('frozen_terms', []))
        if kind == 'env_v_multiplier':
            meta['multipliers'] = model['multipliers']
    elif kind == 'cal_free':
        model = fit_global(train, free=tuple(spec['free']),
                           ridge=float(grid_point['ridge']),
                           bounds_raw=spec.get('bounds'))
        meta['adjust_raw'] = {k: float(v) for k, v in
                              zip(COMPONENTS, model['adjust_raw'])}
    elif kind == 'cal_temperature':
        model = {'form': 'temperature', 'tau': float(grid_point['tau']),
                 'training_races': len(train)}
    elif kind == 'cal_multiplicative':
        comp = [cf.comp_centered(r) for r in train]
        comp_scales = cf._rms_scales(comp)
        designs = [c / comp_scales for c in comp]
        offsets = []
        for race in train:
            raw_sum = np.array([[float(r[k]) for k in COMPONENTS]
                                for r in race['rows']]).sum(axis=1)
            offsets.append(_log_base(race) + raw_sum)
        theta = penalized_fit(designs, offsets,
                              [_targets(r) for r in train],
                              np.full(len(COMPONENTS),
                                      float(grid_point['ridge'])))
        model = {'form': 'multiplicative', 'comp_scales': comp_scales,
                 'theta': theta, 'training_races': len(train)}
    elif kind == 'cal_temperature_ability':
        tau = float(grid_point['tau'])
        ability = [cf.comp_centered(r)[:, COMPONENTS.index('ability_score')]
                   for r in train]
        flat = np.concatenate(ability) if ability else np.zeros(0)
        scale = float(np.sqrt(np.mean(flat ** 2))) if flat.size else 1.0
        if scale < 1e-10:
            scale = 1.0
        designs = [(a / scale).reshape(-1, 1) for a in ability]
        offsets = [tau * _log_base(r) for r in train]
        theta = penalized_fit(designs, offsets,
                              [_targets(r) for r in train],
                              np.array([float(grid_point['ridge'])]))
        model = {'form': 'temp_ability', 'tau': tau, 'scale': float(scale),
                 'theta': float(theta[0]), 'training_races': len(train)}
    elif kind == 'cal_decay':
        ref = context.get('reference_date')
        if ref is None:
            ref = (date.fromisoformat(max(r['date'] for r in train))
                   + timedelta(days=1)).isoformat()
        half = float(grid_point['half_life_days'])
        weights = np.array([
            0.5 ** ((date.fromisoformat(ref)
                     - date.fromisoformat(r['date'])).days / half)
            for r in train])
        model = fit_global(train, ridge=1.0, weights=weights)
    elif kind == 'cal_prior':
        model = fit_global(train, ridge=1.0, prior_raw=spec['prior_raw'],
                           prior_penalty=float(grid_point['prior_penalty']))
    elif kind == 'seg_deviations':
        model = fit_segments(train, spec, grid_point)
        meta['support'] = model['support']
        meta['segment_count'] = len(model['segments'])
    elif kind == 'seg_distance_slope':
        model = fit_distance_slope(train, grid_point)
    elif kind == 'pace_ablation':
        if grid_point['setting'] == 'pace_zero':
            fixed = {'pace_score': 0.0}
        else:
            fixed = {'pace_score': float(spec['pace_fixed_raw'])}
        free = tuple(k for k in COMPONENTS if k != 'pace_score')
        model = fit_global(train, free=free, fixed_raw=fixed, ridge=1.0)
    elif kind == 'pace_split_adjust':
        model = fit_pace_split(train, float(grid_point['ridge']))
    elif kind == 'pace_extra_term':
        base = fit_global(train, ridge=1.0)
        model = fit_extra_term(train, base, spec['term'],
                               float(grid_point['ridge']))
    elif kind == 'up_tail_ability':
        base = fit_global(train, ridge=1.0)
        model = fit_extra_term(train, base, 'tail_ability',
                               float(grid_point['ridge']))
    elif kind == 'up_isotonic':
        ps, ys = [], []
        for race in train:
            target = _targets(race)
            for row, t in zip(race['rows'], target):
                ps.append(float(row['win_prob']))
                ys.append(float(t))
        model = fit_isotonic(ps, ys, int(grid_point['min_bin']))
    elif kind in ('up_piecewise_temperature', 'up_power',
                  'up_race_temperature', 'up_clip'):
        base = fit_global(train, ridge=1.0)
        model = {'form': 'transform', 'kind': kind, 'base': base,
                 'training_races': len(train)}
        for key in ('tau_rel', 'band_split', 'gamma', 'tau', 'cap'):
            if key in grid_point:
                model[key] = grid_point[key]
    else:  # pragma: no cover - registry guards the kind vocabulary
        raise Blocked(f'Hypothesis kind is not fittable: {kind}')
    model['funnel_meta'] = meta
    model['production_eligible'] = False
    return model


def fit_control(train, control, context=None):
    """Fit a named control (ablation) on the same training subset."""
    if control == 'base_form':
        return None
    if control in ('cal01', 'no_env'):
        return fit_global(train, ridge=1.0)
    if control == 'env_t04':
        return fit_env(train, _t04_spec(10.0), 10.0)
    raise Blocked(f'Unknown control: {control}')  # pragma: no cover


def control_grid_points(control):
    """Grid points a control is itself selected over, if any.

    CAL-01 (the ``cal01`` / ``no_env`` control) is selected over its own
    registry grid ridge in {1, 10}; ENV-T04 as a control uses the lambda
    selected by its own hypothesis evaluation.  The screening runner
    resolves the ENV-T04 control through the ENV-T04 result instead of
    this helper; ``fit_control``'s fixed lambda is only a fallback for
    direct unit use.
    """
    if control in ('cal01', 'no_env'):
        return tuple({'ridge': r} for r in (1.0, 10.0))
    return (None,)
