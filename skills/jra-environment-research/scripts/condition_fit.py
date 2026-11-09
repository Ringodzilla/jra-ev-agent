"""Issue #37 condition-coefficient research fit over the admitted turf pool.

Pure research producers: no production model, gate, or shared learning state
is read for update or written.  The base form is the exact structural softmax
used by the 2026-10-25 research fit (variant
``exact_structural_softmax_temperature1_no_mc_no_market``); every candidate is
a logit correction on top of ``log(p0)``:

    score(i) = log p0(i)
        + sum_k a_k * (comp_k(i) - race_mean(comp_k))            [global]
        + sum_{h,e} beta[h,e] * (hfeat_h(i) - race_mean(hfeat_h))
              * env_e(race) / scale[h,e]                          [course layer]
        + sum_k b[venue,k] * (comp_k(i) - race_mean(comp_k))     [venue layer]
        + sum_k c[band,k]  * (comp_k(i) - race_mean(comp_k))     [distance layer]

The course layer is the environment interaction of docs/environment_model.md
(weather, cushion value and both turf moisture readings as continuous
variables, linear or quadratic family).  Segment deviations are estimated in
a second stage on the residuals of the global fit, with a per-segment ridge
penalty proportional to N_train / n_segment, i.e. shrunk toward the global
coefficients; thin segments therefore stay at the global model instead of
asserting their own coefficients.

Final odds are labels: they are loaded only into the market benchmark and
the win-payout fields used by the repo's ROI metric, never into any design
matrix.  Structural inputs come exclusively from the admission run's
normalized/audit artifacts, whose hashes are re-verified here.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from structural_features import structural_features, COMPONENT_WEIGHTS  # noqa: E402

COMPONENTS = tuple(COMPONENT_WEIGHTS)
BAND_ORDER = ('短距離（〜1400m）', 'マイル（1500–1800m）', '中距離（1900–2200m）',
              '長距離（2300m〜）')
BASELINE_VARIANT = 'exact_structural_softmax_temperature1_no_mc_no_market'


class Blocked(ValueError):
    """Raised when a research input fails verification; never repaired."""


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _softmax(values):
    values = np.asarray(values, dtype=float)
    return np.exp(values - logsumexp(values))


def _targets(race) -> np.ndarray:
    """Fractional win target; dead heats split the mass (repo convention)."""
    winners = {str(n) for n in race['winners']}
    numbers = [str(r['horse_number']) for r in race['rows']]
    if not winners or not winners.issubset(set(numbers)):
        raise Blocked('Winner labels outside the field universe')
    return np.array([1.0 / len(winners) if n in winners else 0.0 for n in numbers])


# --------------------------------------------------------------------------
# Loading: structural inputs from the admission run, labels from outcomes.
# --------------------------------------------------------------------------

def _load_outcomes(dataset_dir: Path) -> dict:
    outcomes = {}
    for path in sorted(dataset_dir.glob('outcomes_*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    outcomes[record['race_id']] = record
    return outcomes


def _race_labels(outcome: dict, starter_numbers: set,
                 nonstarters: dict) -> dict:
    """Labels over the starting field.

    Official scratches/exclusions （取消・除外） appear in the archive's
    result and final-odds rows but are not starters.  They are admitted
    into the label record only when the audit's official_nonstarters list
    names exactly those horses, and they are then excluded from every
    label universe (they cannot win and their pre-scratch odds are not a
    market price for the race that was run).
    """
    rows = outcome['observations']['rows']
    odds_rows = outcome['final_odds']['rows']
    result_numbers = {str(r['horse_number']) for r in rows}
    odds_numbers = {str(r['horse_number']) for r in odds_rows}
    expected = starter_numbers | set(nonstarters)
    if result_numbers != expected or odds_numbers != expected:
        raise Blocked('Label universes differ from starters + nonstarters')
    for row in rows:
        number = str(row['horse_number'])
        if number in nonstarters:
            if str(row['position']).strip() not in {'取消', '除外'}:
                raise Blocked('Nonstarter without scratch position')
            if row.get('horse_name') != nonstarters[number]:
                raise Blocked('Nonstarter identity mismatch')
    rows = [r for r in rows if str(r['horse_number']) in starter_numbers]
    odds_rows = [r for r in odds_rows
                 if str(r['horse_number']) in starter_numbers]
    if outcome['final_odds'].get('field_universe_match') is not True:
        raise Blocked('Final odds field universe not verified by the archive')
    final_odds = {}
    for row in odds_rows:
        value = float(row['odds'])
        if not math.isfinite(value) or value <= 1.0:
            raise Blocked('Invalid final win odds in label record')
        final_odds[str(row['horse_number'])] = value
    winners = sorted(str(r['horse_number']) for r in rows
                     if str(r['position']).strip() == '1')
    if not winners:
        raise Blocked('No winner in label record')
    # The archive's payouts_factual is empty; the JRA final win odds are the
    # payout rate, so the win payout per 100 yen is final odds * 100.  This
    # substitution is applied identically to every compared model.
    win_payouts = {number: final_odds[number] * 100.0 for number in winners}
    return {'winners': winners, 'win_payouts': win_payouts,
            'final_odds': final_odds,
            'popularity': {str(r['horse_number']): int(r['popularity'])
                           for r in rows if str(r.get('popularity', '')).isdigit()}}


def load_pool(summary_path, dataset_dir) -> list:
    """Build fit races for admitted turf races of an admission-run summary.

    Structural features are recomputed from the hash-verified normalized
    artifacts with the common base form; market fields on the source rows
    are never read.  Labels are attached only after the feature side of a
    race is complete.
    """
    summary_path = Path(summary_path)
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    outcomes = _load_outcomes(Path(dataset_dir))
    races = []
    for item in summary['races']:
        if item['research_admission']['status'] != 'ADMITTED_RESEARCH':
            continue
        if item['segment']['surface'] != '芝':
            continue
        audit_ref = item['audit']
        if sha256(audit_ref['path']) != audit_ref['sha256']:
            raise Blocked('Audit artifact changed: ' + item['race_id'])
        audit = json.loads(Path(audit_ref['path']).read_text(encoding='utf-8'))
        if audit['environment_gate'] != 'PASS' or audit['surface_gate'] != 'PASS':
            raise Blocked('Admitted race without PASS gates: ' + item['race_id'])
        if audit['split'] != item['split'] or audit['segment'] != item['segment']:
            raise Blocked('Summary/audit segment mismatch: ' + item['race_id'])
        normalized_ref = audit['normalized_artifact']
        if sha256(normalized_ref['path']) != normalized_ref['sha256']:
            raise Blocked('Normalized artifact changed: ' + item['race_id'])
        normalized = json.loads(
            Path(normalized_ref['path']).read_text(encoding='utf-8'))
        groups = defaultdict(list)
        for row in normalized['rows']:
            groups[str(row['horse_number'])].append(row)
        entries = normalized['entries']
        if set(groups) != {str(e['horse_number']) for e in entries}:
            raise Blocked('Full horse set missing: ' + item['race_id'])
        rows = []
        for entry in entries:
            features = structural_features(groups[str(entry['horse_number'])], entry)
            rows.append(features)
        scores = np.array([math.fsum(COMPONENT_WEIGHTS[k] * float(r[k])
                                     for k in COMPONENT_WEIGHTS) for r in rows])
        base = _softmax(scores)
        for row, score, prob in zip(rows, scores, base):
            row['S_base'] = float(score)
            row['win_prob'] = float(prob)
        target = entries[0]
        env_measured = audit['environment']
        post = datetime.fromisoformat(audit['post_time'])
        for key in ('cushion', 'moisture'):
            stamp = datetime.fromisoformat(env_measured[key]['measured_at'])
            if stamp.date().isoformat() != target['target_race_date'] or stamp >= post:
                raise Blocked('Environment measurement not same-day pre-race: '
                              + item['race_id'])
        environment = {
            'cushion': float(env_measured['cushion']['cushion']),
            'turf_moisture_goal': float(env_measured['moisture']['turf_moisture_goal']),
            'turf_moisture_corner4': float(
                env_measured['moisture']['turf_moisture_corner4']),
            'weather': target['target_weather'],
            'track_condition': target['target_track_condition'],
        }
        nonstarters = {str(n['horse_number']): n['horse_name']
                       for n in audit.get('official_nonstarters') or []}
        labels = _race_labels(outcomes[item['race_id']],
                              {str(e['horse_number']) for e in entries},
                              nonstarters)
        market = np.array([1.0 / labels['final_odds'][str(r['horse_number'])]
                           for r in rows])
        races.append({
            'race_id': item['race_id'],
            'date': target['target_race_date'],
            'split': item['split'],
            'venue': item['segment']['venue'],
            'distance': int(item['segment']['distance']),
            'band': item['segment']['band'],
            'rows': rows,
            'environment': environment,
            'winners': labels['winners'],
            'win_payouts': labels['win_payouts'],
            'final_odds': labels['final_odds'],
            'popularity': labels['popularity'],
            'market_probs': (market / market.sum()).tolist(),
        })
    races.sort(key=lambda r: (r['date'], r['race_id']))
    return races


# --------------------------------------------------------------------------
# Design matrices.
# --------------------------------------------------------------------------

def comp_centered(race) -> np.ndarray:
    values = np.array([[float(r[k]) for k in COMPONENTS] for r in race['rows']])
    return values - values.mean(axis=0)


def env_design(race, family):
    from analysis.environment_model import design_matrix
    return design_matrix(race['rows'], race['environment'], family)


def _rms_scales(matrices) -> np.ndarray:
    scales = np.sqrt(np.mean(np.vstack(matrices) ** 2, axis=0))
    scales[scales < 1e-10] = 1.0
    return scales


def _fit_penalized(designs, offsets, targets, penalties) -> np.ndarray:
    """Conditional log-loss with per-column ridge penalties (repo form)."""
    penalties = np.asarray(penalties, dtype=float)
    count = len(designs)

    def objective(theta):
        value = 0.5 * float(penalties @ (theta ** 2))
        gradient = penalties * theta
        for design, offset, target in zip(designs, offsets, targets):
            score = offset + design @ theta
            normalizer = logsumexp(score)
            probability = np.exp(score - normalizer)
            value += float(normalizer - target @ score) / count
            gradient = gradient + design.T @ (probability - target) / count
        return value, gradient

    result = minimize(objective, np.zeros(designs[0].shape[1]), jac=True,
                      method='L-BFGS-B',
                      options={'maxiter': 1000, 'ftol': 1e-12, 'gtol': 1e-8})
    if not result.success or not np.isfinite(result.x).all():
        raise Blocked('Coefficient fit failed to converge')
    return result.x


# --------------------------------------------------------------------------
# Two-stage fit: global coefficients, then segment deviations on residuals.
# --------------------------------------------------------------------------

def fit_stage1(train, family, ridge, use_env=True) -> dict:
    from analysis.environment_model import environment_vector, HORSE_FEATURES
    comp = [comp_centered(r) for r in train]
    comp_scales = _rms_scales(comp)
    comp = [c / comp_scales for c in comp]
    env_names, env_scales, active = [], None, None
    if use_env:
        raw = [env_design(r, family)[0] for r in train]
        _, env_names = env_design(train[0], family)
        env_values = np.array([environment_vector(r['environment'], family)[0]
                               for r in train])
        env_names_full = environment_vector(train[0]['environment'], family)[1]
        varying = np.ptp(env_values, axis=0) > 1e-10
        active = np.tile(varying, len(HORSE_FEATURES))
        masked = [m * active for m in raw]
        env_scales = _rms_scales(masked)
        env = [m / env_scales for m in masked]
        designs = [np.hstack([c, e]) for c, e in zip(comp, env)]
        unsupported = [name for name, flag in zip(env_names_full, varying)
                       if not flag]
        ranges = {name: [float(min(v)), float(max(v))]
                  for name, v in zip(env_names_full, env_values.T)}
    else:
        designs = comp
        unsupported, ranges = [], {}
    offsets = [np.log(np.array([float(r['win_prob']) for r in race['rows']]))
               for race in train]
    targets = [_targets(r) for r in train]
    theta = _fit_penalized(designs, offsets, targets,
                           np.full(designs[0].shape[1], float(ridge)))
    return {'family': family, 'ridge': float(ridge), 'use_env': use_env,
            'comp_scales': comp_scales, 'env_scales': env_scales,
            'env_active': active, 'env_feature_names': env_names,
            'component_adjust': theta[:len(COMPONENTS)],
            'env_coefficients': theta[len(COMPONENTS):] if use_env else None,
            'unsupported_environment_terms': unsupported,
            'training_environment_ranges': ranges,
            'training_races': len(train),
            'training_end_date': max(r['date'] for r in train)}


def stage1_scores(race, stage1) -> np.ndarray:
    score = np.log(np.array([float(r['win_prob']) for r in race['rows']]))
    comp = comp_centered(race) / stage1['comp_scales']
    score = score + comp @ stage1['component_adjust']
    if stage1['use_env']:
        design = env_design(race, stage1['family'])[0]
        design = design * stage1['env_active'] / stage1['env_scales']
        score = score + design @ stage1['env_coefficients']
    return score


def segment_blocks(train) -> dict:
    venues = sorted({r['venue'] for r in train})
    bands = [b for b in BAND_ORDER if any(r['band'] == b for r in train)]
    counts = {'venue': {v: sum(1 for r in train if r['venue'] == v)
                        for v in venues},
              'band': {b: sum(1 for r in train if r['band'] == b)
                       for b in bands}}
    return {'venue': venues, 'band': bands, 'counts': counts}


def _segment_designs(train, stage1, blocks):
    designs = []
    for race in train:
        comp = comp_centered(race) / stage1['comp_scales']
        parts = []
        for venue in blocks['venue']:
            parts.append(comp if race['venue'] == venue
                         else np.zeros_like(comp))
        for band in blocks['band']:
            parts.append(comp if race['band'] == band
                         else np.zeros_like(comp))
        designs.append(np.hstack(parts))
    return designs


def _segment_penalties(blocks, ridge, multiplier, total) -> np.ndarray:
    penalties = []
    for kind in ('venue', 'band'):
        for name in blocks[kind]:
            n_seg = blocks['counts'][kind][name]
            penalties.extend([ridge * multiplier * (total / n_seg)]
                             * len(COMPONENTS))
    return np.array(penalties)


def fit_stage2(train, stage1, ridge, multiplier, reference_multiplier=0.01) -> dict:
    """Segment deviations on stage-1 residuals, shrunk toward the global fit.

    A reference fit with a near-zero multiplier is kept only to report, per
    segment, how much of the unshrunk deviation survived the shrinkage.
    """
    blocks = segment_blocks(train)
    designs = _segment_designs(train, stage1, blocks)
    offsets = [stage1_scores(r, stage1) for r in train]
    targets = [_targets(r) for r in train]
    total = len(train)

    def solve(mult):
        penalties = _segment_penalties(blocks, ridge, mult, total)
        return _fit_penalized(designs, offsets, targets, penalties), penalties

    theta, penalties = solve(multiplier)
    reference, _ = solve(reference_multiplier)
    deviations, shrinkage, penalty_map = {}, {}, {}
    index = 0
    for kind in ('venue', 'band'):
        deviations[kind], shrinkage[kind], penalty_map[kind] = {}, {}, {}
        for name in blocks[kind]:
            width = len(COMPONENTS)
            block = theta[index:index + width]
            ref = reference[index:index + width]
            deviations[kind][name] = block
            ref_norm = float(np.linalg.norm(ref))
            shrinkage[kind][name] = (
                float(np.linalg.norm(block) / ref_norm) if ref_norm > 1e-9
                else None)
            penalty_map[kind][name] = float(penalties[index])
            index += width
    return {'blocks': blocks, 'deviations': deviations,
            'shrinkage_factors': shrinkage, 'penalties': penalty_map,
            'multiplier': float(multiplier)}


def fit_variant(train, family, ridge, multiplier, use_env, use_segments) -> dict:
    stage1 = fit_stage1(train, family, ridge, use_env=use_env)
    stage2 = (fit_stage2(train, stage1, ridge, multiplier)
              if use_segments else None)
    return {'stage1': stage1, 'stage2': stage2,
            'family': family, 'ridge': float(ridge),
            'multiplier': float(multiplier) if use_segments else None,
            'use_env': use_env, 'use_segments': use_segments,
            'status': 'candidate_not_promoted'}


def variant_scores(race, model) -> np.ndarray:
    score = stage1_scores(race, model['stage1'])
    if model['stage2'] is not None:
        stage1 = model['stage1']
        comp = comp_centered(race) / stage1['comp_scales']
        deviations = model['stage2']['deviations']
        if race['venue'] in deviations['venue']:
            score = score + comp @ deviations['venue'][race['venue']]
        if race['band'] in deviations['band']:
            score = score + comp @ deviations['band'][race['band']]
    return score


def variant_probs(race, model) -> np.ndarray:
    if model is None:
        return np.array([float(r['win_prob']) for r in race['rows']])
    return _softmax(variant_scores(race, model))


def market_probs(race) -> np.ndarray:
    return np.array(race['market_probs'], dtype=float)


# --------------------------------------------------------------------------
# Metrics: definitions identical to analysis.environment_model.evaluate_model.
# --------------------------------------------------------------------------

def evaluate(races, probs_of) -> dict:
    losses, briers, hits, returns = [], [], [], []
    for race in races:
        p = np.asarray(probs_of(race), dtype=float)
        target = _targets(race)
        losses.append(-float(target @ np.log(np.maximum(p, 1e-300))))
        briers.append(float(((p - target) ** 2).sum()))
        numbers = [str(r['horse_number']) for r in race['rows']]
        leader = min(range(len(p)), key=lambda i: (-p[i], int(numbers[i])))
        hits.append(numbers[leader] in set(race['winners']))
        returns.append(float(race['win_payouts'].get(numbers[leader], 0.0)))
    return {'race_count': len(races), 'log_loss': float(np.mean(losses)),
            'brier': float(np.mean(briers)),
            'top1_hit_rate': float(np.mean(hits)),
            'win_top1_policy_roi': sum(returns) / (100 * len(races))}


def segment_metrics(races, variants) -> list:
    """Per-segment log-loss for each variant; variants maps name -> probs_of."""
    groups = {}
    for race in races:
        for kind, name in (('venue', race['venue']), ('band', race['band']),
                           ('distance', str(race['distance']))):
            groups.setdefault((kind, name), []).append(race)
    table = []
    for (kind, name), members in sorted(groups.items()):
        row = {'segment_kind': kind, 'segment': name,
               'race_count': len(members)}
        for variant, probs_of in variants.items():
            row[variant] = evaluate(members, probs_of)['log_loss']
        table.append(row)
    return table


# --------------------------------------------------------------------------
# Exploratory upset analysis (test split only; never a selection input).
# --------------------------------------------------------------------------

def _rank_of(numbers, probs, wanted) -> int:
    order = sorted(range(len(numbers)), key=lambda i: (-probs[i], int(numbers[i])))
    for position, index in enumerate(order, start=1):
        if numbers[index] in wanted:
            return position
    raise Blocked('Winner not ranked')


def upset_analysis(races, probs_of_model, probs_of_global) -> dict:
    detail = []
    for race in races:
        numbers = [str(r['horse_number']) for r in race['rows']]
        winners = set(race['winners'])
        # For a dead heat, follow the winner the market rated highest.
        market = market_probs(race)
        winner = min(winners, key=lambda n: (-market[numbers.index(n)],
                                              int(n)))
        model = probs_of_model(race)
        glob = probs_of_global(race)
        detail.append({
            'race_id': race['race_id'], 'venue': race['venue'],
            'band': race['band'], 'distance': race['distance'],
            'winner_number': winner,
            'winner_final_odds': race['final_odds'][winner],
            'winner_popularity': race['popularity'].get(winner),
            'dead_heat': len(winners) > 1,
            'rank_model': _rank_of(numbers, model, {winner}),
            'rank_global': _rank_of(numbers, glob, {winner}),
            'rank_market': _rank_of(numbers, market, {winner}),
            'prob_model': float(model[numbers.index(winner)]),
            'prob_global': float(glob[numbers.index(winner)]),
            'prob_market': float(market[numbers.index(winner)]),
        })

    def summarize(rows):
        if not rows:
            return {'race_count': 0}
        return {
            'race_count': len(rows),
            'model_rank_better_than_market': sum(
                1 for r in rows if r['rank_model'] < r['rank_market']),
            'model_rank_equal_to_market': sum(
                1 for r in rows if r['rank_model'] == r['rank_market']),
            'model_rank_worse_than_market': sum(
                1 for r in rows if r['rank_model'] > r['rank_market']),
            'mean_rank_model': float(np.mean([r['rank_model'] for r in rows])),
            'mean_rank_market': float(np.mean([r['rank_market'] for r in rows])),
            'mean_rank_global': float(np.mean([r['rank_global'] for r in rows])),
            'mean_prob_model': float(np.mean([r['prob_model'] for r in rows])),
            'mean_prob_market': float(np.mean([r['prob_market'] for r in rows])),
        }

    longshots = [r for r in detail if r['winner_final_odds'] >= 10.0]
    longshots20 = [r for r in detail if r['winner_final_odds'] >= 20.0]
    by_band, by_venue = {}, {}
    for row in longshots:
        by_band.setdefault(row['band'], []).append(row)
        by_venue.setdefault(row['venue'], []).append(row)
    return {
        'scope': 'test split only; exploratory; not used for model selection',
        'longshot_definition': 'winner final win odds >= 10 (also >= 20)',
        'all_test_races': summarize(detail),
        'longshot_winners_odds_ge_10': summarize(longshots),
        'longshot_winners_odds_ge_20': summarize(longshots20),
        'longshot_by_band': {k: summarize(v) for k, v in sorted(by_band.items())},
        'longshot_by_venue': {k: summarize(v) for k, v in sorted(by_venue.items())},
        'detail': detail,
    }
