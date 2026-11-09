"""Run the Issue #37 condition-coefficient research fit (turf pool).

Writes the preregistered plan before any label-dependent computation, fits
the two-stage condition model on the dataset train split, selects the
family/ridge/shrinkage configuration on validation only, and evaluates once
on test.  Research only: no production model, gate, or shared learning
state is modified (shared-state hashes are asserted unchanged).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import condition_fit as cf  # noqa: E402
from research_numeric import shared  # noqa: E402

GRID = [(family, ridge, mult)
        for family in ('linear', 'quadratic')
        for ridge in (1.0, 10.0, 100.0)
        for mult in (1.0, 10.0)]
PARITY = ('linear', 100.0, 1.0)
ESSENTIAL_ENV_TERMS = {'rain', 'cushion', 'moisture_goal', 'moisture_corner4',
                       'condition'}
MINIMUM = {'train': 100, 'validation': 20, 'test': 20}


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def _write(path, payload):
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False,
                               indent=2) + '\n', encoding='utf-8')


def _support_flag(n_segment, shrinkage):
    if shrinkage is None:
        return 'no_unshrunk_signal'
    if shrinkage >= 0.5 and n_segment >= 20:
        return 'supported'
    if shrinkage >= 0.2:
        return 'partially_supported'
    return 'shrunk_to_global'


def _model_payload(model):
    stage1 = model['stage1']
    payload = {
        'family': model['family'], 'ridge_global': model['ridge'],
        'segment_multiplier': model['multiplier'],
        'use_env': model['use_env'], 'use_segments': model['use_segments'],
        'status': model['status'],
        'components': list(cf.COMPONENTS),
        'comp_scales': stage1['comp_scales'],
        'global_component_adjust_scaled': stage1['component_adjust'],
        'global_component_adjust_raw': (
            stage1['component_adjust'] / stage1['comp_scales']),
        'unsupported_environment_terms': stage1['unsupported_environment_terms'],
        'training_environment_ranges': stage1['training_environment_ranges'],
        'training_races': stage1['training_races'],
        'training_end_date': stage1['training_end_date'],
    }
    if model['use_env']:
        payload['env_feature_names'] = stage1['env_feature_names']
        payload['env_scales'] = stage1['env_scales']
        payload['env_coefficients_scaled'] = stage1['env_coefficients']
        payload['env_coefficients_raw'] = (
            stage1['env_coefficients'] / stage1['env_scales'])
    if model['stage2'] is not None:
        stage2 = model['stage2']
        segments = {}
        for kind in ('venue', 'band'):
            segments[kind] = {}
            for name in stage2['blocks'][kind]:
                deviation = stage2['deviations'][kind][name]
                n_seg = stage2['blocks']['counts'][kind][name]
                shrinkage = stage2['shrinkage_factors'][kind][name]
                segments[kind][name] = {
                    'train_races': n_seg,
                    'penalty': stage2['penalties'][kind][name],
                    'deviation_scaled': deviation,
                    'deviation_raw': deviation / stage1['comp_scales'],
                    'deviation_norm_scaled': float(np.linalg.norm(deviation)),
                    'shrinkage_factor': shrinkage,
                    'support': _support_flag(n_seg, shrinkage),
                }
        payload['segments'] = segments
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--dataset-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)

    before = shared(ROOT)
    plan = {
        'experiment_id': 'issue37_condition_coefficients_turf_20260502_20260927',
        'experiment_kind': 'timeseries_research_fit',
        'issue': 'task-management #37 (condition coefficients), pool by #39',
        'baseline_variant': cf.BASELINE_VARIANT,
        'pool': {
            'admission_summary': str(args.summary),
            'admission_summary_sha256': cf.sha256(args.summary),
            'dataset_dir': str(args.dataset_dir),
            'selection': 'ADMITTED_RESEARCH races with surface 芝 only; '
                         'whole starting fields; no horse-level exclusion',
            'splits': 'fixed by the dataset (chronological): train 134, '
                      'validation 45, test 39',
        },
        'legacy_18_treatment': {
            'included_in_headline_fit': False,
            'reason': 'The fixed evaluation set is a separately governed '
                      'asset whose splits must not change '
                      '(docs/environment_model.md); the 2026-10-24 pair '
                      'belongs to the preserved Saturday/Sunday experiment; '
                      'the 2026-10-25 ten already served as the training '
                      'set of the posthoc single-day fit, so their labels '
                      'are previously inspected and they cannot act as an '
                      'independent validation or test here.',
        },
        'model_form': 'score = log p0 + global component recalibration '
                      '+ horse-feature x environment interactions (course '
                      'layer; cushion and both turf moisture readings as '
                      'continuous variables) + venue deviations + distance '
                      'band deviations on race-centred components',
        'surface_layer': 'not identifiable: the pool is turf-only, so the '
                         'surface coefficient has a single level',
        'distance_bands': 'audit driver distance_band(): 短距離（〜1400m）, '
                          'マイル（1500–1800m）, 中距離（1900–2200m）, '
                          '長距離（2300m〜）',
        'estimation': 'stage 1 global fit, then stage 2 segment deviations '
                      'on stage-1 residuals; per-segment ridge penalty '
                      '= ridge_global * multiplier * (N_train / n_segment) '
                      '(shrinkage toward the global coefficients)',
        'candidate_grid': [{'family': f, 'ridge': r, 'multiplier': m}
                           for f, r, m in GRID],
        'selection_rule': 'minimum validation log-loss over the grid for '
                          'the FULL variant; ties keep grid order; test is '
                          'never consulted during selection',
        'parity_configuration': {'family': PARITY[0], 'ridge': PARITY[1],
                                 'multiplier': PARITY[2],
                                 'note': 'closest to the 2026-10-25 fixed '
                                         'linear/ridge100 research fit'},
        'baselines': {
            'base': 'exact structural softmax, no fitted coefficients',
            'global_only': 'stage 1 only (global recalibration + env)',
            'no_environment': 'global recalibration + segment deviations '
                              'with the environment layer removed',
            'market': 'normalized inverse final win odds; comparator only',
        },
        'metrics': ['log_loss', 'brier', 'top1_hit_rate',
                    'win_top1_policy_roi'],
        'label_policy': 'outcomes are labels only. Final odds enter only '
                        'the market benchmark and win payouts. '
                        'payouts_factual is empty archive-wide, so win '
                        'payout per 100 yen = final win odds * 100, '
                        'applied identically to every model.',
        'adoption_gates': 'docs/environment_model.md / train_and_compare: '
                          'counts >= 100/20/20; essential env terms '
                          'identifiable; validation and test log-loss '
                          'improve over the base form; test Brier does not '
                          'worsen; validation and test win-policy ROI do '
                          'not worsen. Passing still changes nothing in '
                          'production from this run.',
        'segment_support_reporting': 'supported: shrinkage >= 0.5 and '
                                     'n >= 20; partially_supported: '
                                     'shrinkage >= 0.2; otherwise '
                                     'shrunk_to_global / no_unshrunk_signal',
        'labels_previously_inspected': True,
        'label_note': 'Archive outcomes were collected at dataset creation '
                      'and were present during admission; this run fixed '
                      'its grid, selection rule and gates in this plan '
                      'before computing any fit or metric.',
        'pre_race_capture': False,
        'production_eligible': False,
        'production_model_changed': False,
        'code_sha256': {
            'condition_fit.py': cf.sha256(Path(__file__).parent
                                          / 'condition_fit.py'),
            'run_condition_fit.py': cf.sha256(Path(__file__)),
            'structural_features.py': cf.sha256(
                Path(__file__).parent / 'structural_features.py'),
            'analysis/environment_model.py': cf.sha256(
                ROOT / 'analysis/environment_model.py'),
        },
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    _write(output / 'research_plan.json', plan)

    races = cf.load_pool(args.summary, args.dataset_dir)
    by_split = {name: [r for r in races if r['split'] == name]
                for name in ('train', 'validation', 'test')}
    counts = {k: len(v) for k, v in by_split.items()}
    if counts != {'train': 134, 'validation': 45, 'test': 39}:
        raise cf.Blocked(f'Unexpected split counts: {counts}')
    train, validation, test = (by_split['train'], by_split['validation'],
                               by_split['test'])

    # Independent cross-check of the base-form metrics (repo definitions).
    from analysis.environment_model import evaluate_model
    for name, block in (('validation', validation), ('test', test)):
        mine = cf.evaluate(block, lambda r: cf.variant_probs(r, None))
        theirs = evaluate_model(block)
        if abs(mine['log_loss'] - theirs['log_loss']) > 1e-9:
            raise cf.Blocked('Base metric mismatch on ' + name)

    trials = []
    fitted = {}
    for family, ridge, mult in GRID:
        model = cf.fit_variant(train, family, ridge, mult,
                               use_env=True, use_segments=True)
        fitted[(family, ridge, mult)] = model
        trials.append({'family': family, 'ridge': ridge, 'multiplier': mult,
                       'validation': cf.evaluate(
                           validation, lambda r: cf.variant_probs(r, model))})
    selected_key = min(
        GRID, key=lambda k: trials[GRID.index(k)]['validation']['log_loss'])
    full = fitted[selected_key]
    parity_model = fitted[PARITY]
    family, ridge, mult = selected_key
    global_only = cf.fit_variant(train, family, ridge, mult,
                                 use_env=True, use_segments=False)
    no_env = cf.fit_variant(train, family, ridge, mult,
                            use_env=False, use_segments=True)

    variants = {
        'base': lambda r: cf.variant_probs(r, None),
        'global_only': lambda r: cf.variant_probs(r, global_only),
        'no_environment': lambda r: cf.variant_probs(r, no_env),
        'full': lambda r: cf.variant_probs(r, full),
        'full_parity_config': lambda r: cf.variant_probs(r, parity_model),
        'market': cf.market_probs,
    }
    metrics = {split: {name: cf.evaluate(block, fn)
                       for name, fn in variants.items()}
               for split, block in by_split.items()}
    _write(output / 'metrics.json',
           {'trials': trials, 'selected': {'family': family, 'ridge': ridge,
                                           'multiplier': mult},
            'metrics': metrics})

    tables = {}
    for name, block in (('validation', validation), ('test', test),
                        ('validation_plus_test', validation + test)):
        table = cf.segment_metrics(block, {
            k: variants[k] for k in ('base', 'global_only', 'no_environment',
                                     'full', 'market')})
        for row in table:
            row['delta_full_minus_global'] = row['full'] - row['global_only']
            row['delta_full_minus_base'] = row['full'] - row['base']
            row['thin_cell'] = row['race_count'] < 5
        tables[name] = table
    _write(output / 'segment_tables.json', tables)

    upset = cf.upset_analysis(
        test, lambda r: cf.variant_probs(r, full),
        lambda r: cf.variant_probs(r, global_only))
    _write(output / 'upset_analysis.json', upset)

    _write(output / 'coefficients.json', {
        'selected_configuration': {'family': family, 'ridge': ridge,
                                   'multiplier': mult},
        'full': _model_payload(full),
        'global_only': _model_payload(global_only),
        'no_environment': _model_payload(no_env),
        'parity_configuration': {'family': PARITY[0], 'ridge': PARITY[1],
                                 'multiplier': PARITY[2],
                                 'model': _model_payload(parity_model)},
    })

    base_m, full_m = metrics['validation'], metrics['test']
    enough = all(counts[k] >= MINIMUM[k] for k in MINIMUM)
    identifiable = not ESSENTIAL_ENV_TERMS.intersection(
        full['stage1']['unsupported_environment_terms'])
    improved = (
        full_m['full']['log_loss'] < full_m['base']['log_loss']
        and base_m['full']['log_loss'] < base_m['base']['log_loss']
        and full_m['full']['brier'] <= full_m['base']['brier']
        and base_m['full']['win_top1_policy_roi']
        >= base_m['base']['win_top1_policy_roi']
        and full_m['full']['win_top1_policy_roi']
        >= full_m['base']['win_top1_policy_roi'])
    status = ('INSUFFICIENT_DATA' if not enough else
              'INSUFFICIENT_ENVIRONMENT_COVERAGE' if not identifiable else
              'ELIGIBLE_FOR_REVIEW' if improved else 'REJECTED')
    supported = {'venue': [], 'band': []}
    payload = _model_payload(full)
    for kind in ('venue', 'band'):
        for name, seg in payload['segments'][kind].items():
            if seg['support'] == 'supported':
                supported[kind].append(name)
    after = shared(ROOT)
    if after != before:
        raise cf.Blocked('Shared state changed during the fit')
    artifacts = {}
    for filename in ('research_plan.json', 'coefficients.json',
                     'metrics.json', 'segment_tables.json',
                     'upset_analysis.json'):
        artifacts[filename] = {'path': str(output / filename),
                               'sha256': cf.sha256(output / filename)}
    report = {
        'claim_scope': 'timeseries_research_only',
        'status': status,
        'validation_status': 'EXECUTED',
        'test_status': 'EXECUTED',
        'production_eligible': False,
        'production_model_changed': False,
        'tickets': [],
        'pool_counts': counts,
        'selected_configuration': {'family': family, 'ridge': ridge,
                                   'multiplier': mult},
        'gates': {'minimum_counts_met': enough,
                  'essential_env_terms_identifiable': identifiable,
                  'improvement_criteria_met': improved},
        'supported_segments': supported,
        'headline': {
            'validation': {k: metrics['validation'][k]
                           for k in ('base', 'global_only', 'no_environment',
                                     'full', 'market')},
            'test': {k: metrics['test'][k]
                     for k in ('base', 'global_only', 'no_environment',
                               'full', 'market')},
        },
        'limitations': [
            'Posthoc-reconstructed archive (pre_race_capture=false); '
            'results are research-only and never production evidence.',
            'Surface coefficient is not identifiable: the pool is '
            'turf-only by design of this run.',
            'Segment deviations are shrunk toward the global fit; '
            'segments reported as shrunk_to_global carry no separate '
            'coefficient claim.',
            'Win payout uses final odds * 100 because the archive '
            'payouts_factual is empty; identical for all compared models.',
            'Exploratory upset analysis is descriptive counts on the '
            'test split, not a selection or adoption input.',
        ],
        'next_action': 'Reviewer confirms the verdict and the supported '
                       'segments; any production use requires the separate '
                       'condition-model adoption procedure and forward '
                       'Stage 0 evidence.',
        'shared_before': before, 'shared_after': after,
        'artifacts': artifacts,
        'reported_at': datetime.now(timezone.utc).isoformat(),
    }
    _write(output / 'training_report.json', report)
    print(json.dumps({'status': status, 'selected': selected_key,
                      'counts': counts,
                      'validation_full': metrics['validation']['full'],
                      'test_full': metrics['test']['full']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
