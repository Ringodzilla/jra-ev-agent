"""Run the Issue #40 hypothesis-funnel screening (integrated pool).

Two modes:

- ``--write-plan``: build the screening-pool manifest (summary metadata
  only — no audit artifact, outcome or label is opened) and write the
  preregistered ``research_plan.json`` (protocol section 11.1).  No
  metric of any kind is computed in this mode.
- default: execute the screening.  This is code-gated on AP-0: unless
  the plan's approval record says ``approved``, the run refuses before
  any race is loaded.  Confirmation-pool races cannot be loaded at all:
  inputs come only from the plan-bound manifest, whose expansion entries
  are train-split only (see ``funnel_data``).

Research track only; outputs carry ``production_eligible=false``.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import condition_fit as cf  # noqa: E402
import funnel_data as fd  # noqa: E402
import funnel_registry as fr  # noqa: E402
import funnel_screening as fs  # noqa: E402

WORKSPACE = ROOT.parent
REGISTRY_PATH = WORKSPACE / 'jra-funnel' / 'hypothesis_registry.md'
PROTOCOL_PATH = WORKSPACE / 'jra-funnel' / 'protocol.md'
CONFIRMATION_MANIFEST_PATH = (WORKSPACE / 'codex-briefs' / 'handover'
                              / 'confirmation_blocks_manifest.json')
RUN_DIR = (ROOT / 'report' / 'research'
           / 'hypothesis_funnel_integrated_20261110')
CODE_FILES = {
    'funnel_registry.py': 'skills/jra-environment-research/scripts/'
                          'funnel_registry.py',
    'funnel_data.py': 'skills/jra-environment-research/scripts/'
                      'funnel_data.py',
    'funnel_engine.py': 'skills/jra-environment-research/scripts/'
                        'funnel_engine.py',
    'funnel_screening.py': 'skills/jra-environment-research/scripts/'
                           'funnel_screening.py',
    'run_funnel_screening.py': 'skills/jra-environment-research/scripts/'
                               'run_funnel_screening.py',
    'condition_fit.py': 'skills/jra-environment-research/scripts/'
                        'condition_fit.py',
    'structural_features.py': 'skills/jra-environment-research/scripts/'
                              'structural_features.py',
    'analysis/environment_model.py': 'analysis/environment_model.py',
}
BASELINE_VARIANT = cf.BASELINE_VARIANT
TRANSFER_SHORTLIST = ['CAL-02', 'CAL-06', 'CAL-01', 'UP-02', 'SEG-05',
                      'SEG-08', 'PACE-02', 'SEG-02']
ADOPTION_GATES = [
    'Counts: at least 100 train / 20 validation / 20 test races in the '
    'splits the candidate is evaluated on.',
    'Required environment terms must be identifiable (hypotheses where '
    'applicable).',
    'Log loss must improve over the base form on both validation and '
    'test.',
    'Test Brier score must not worsen.',
    'Win-top1 policy ROI (100 yen on the top-probability horse) must '
    'not worsen on either validation or test.',
]


def _jsonable(value):
    import numpy as np
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    return value


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False,
                               indent=2) + '\n', encoding='utf-8')


def confirmation_blocks() -> dict:
    payload = json.loads(CONFIRMATION_MANIFEST_PATH.read_text(
        encoding='utf-8'))
    blocks = {}
    for block in payload['blocks']:
        blocks[block['split']] = {
            'race_count': block['race_count'],
            'by_surface': block['by_surface'],
            'block_manifest_sha256': block['block_manifest_sha256'],
            'date_min': block['date_min'],
            'date_max': block['date_max'],
        }
    return {'source_file': str(CONFIRMATION_MANIFEST_PATH),
            'source_sha256': cf.sha256(CONFIRMATION_MANIFEST_PATH),
            'blocks': blocks,
            'opened': False}


def build_plan(manifest, manifest_path=None) -> dict:
    blocks = confirmation_blocks()
    return {
        'experiment_id': 'issue40_hypothesis_funnel_integrated_v1',
        'experiment_kind': 'hypothesis_funnel_screening',
        'issue': 'task-management #40 (hypothesis funnel); pool from '
                 '#39; mission #41',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'baseline_variant': BASELINE_VARIANT,
        'registry': {
            'version': fr.REGISTRY_VERSION,
            'path': str(REGISTRY_PATH),
            'sha256': fr.registry_sha256(REGISTRY_PATH),
            'n_hypotheses': len(fr.HYPOTHESES),
            'family_counts': fr.FAMILY_COUNTS,
        },
        'protocol': {'path': str(PROTOCOL_PATH),
                     'sha256': cf.sha256(PROTOCOL_PATH)},
        'pool': {
            'manifest_path': str(manifest_path or
                                 (RUN_DIR / 'screening_pool_manifest.json')),
            'manifest_sha256': manifest['manifest_sha256'],
            'counts': manifest['counts'],
            'screening_legal_basis': 'Migrated pool: all splits already '
                                     'inspected (Issue #37). Expansion '
                                     'pool: train split only.',
        },
        'confirmation_pool': blocks,
        'folds': {
            'definition': 'Rolling-origin CV inside the integrated '
                          'train pool (migrated train + expansion '
                          'train): whole race days split into 5 '
                          'contiguous blocks of near-equal race counts; '
                          'fold i fits blocks 1..i and evaluates block '
                          'i+1 (4 folds). Migrated validation/test are '
                          'reference blocks only, never mixed into '
                          'folds and never kill-primary.',
            'n_blocks': 5,
            'n_folds': 4,
        },
        'grid_selection': 'Each grid point is fitted per fold; the '
                          'hypothesis grid point is selected by mean '
                          'fold log-loss across the 4 folds (screening '
                          'data only) and disclosed in the results.',
        'kill_criteria': fs.KILL_THRESHOLDS,
        'shortlist_caps': {'total': fs.SHORTLIST_CAP,
                           'per_family': fs.FAMILY_CAP},
        'adoption_gates': ADOPTION_GATES,
        'mandated_statements': [
            'Dirt hypotheses are screening-only in this run: the '
            'expansion test block holds 19 dirt races, below the '
            'confirmation minimum of 20 per block, so no dirt '
            'hypothesis may advance to the confirmation layer.',
            'Transfer-pool provisional shortlist (Codex run, plan '
            'SHA-256 ca32c9210a1c139d1f38a8e5cc1c7dd71aad41d2e716c3'
            'dcc111e992ff03efbd): CAL-02, CAL-06, CAL-01, UP-02, '
            'SEG-05, SEG-08, PACE-02, SEG-02. It is comparison / '
            'co-disclosure material only; only the integrated run '
            'shortlist may advance to confirmation.',
            'Held hypotheses PACE-03, PACE-06, PACE-07 stay in the '
            'total N=60 with recorded reasons (required observations '
            'absent from the admission artifacts).',
        ],
        'estimation_definitions': [
            'Base CAL-01 ridge inside dependent families (ENV / SEG / '
            'UP / PACE bases) is fixed at 1.0, the Issue #37 selected '
            'value; ENV families fit components and environment jointly '
            'with component penalty 1.0 and environment penalty '
            'lambda_env from the registry grid.',
            'Environment transforms: hinge = two half-lines at the '
            'fold-train median; piecewise-linear = linear term plus '
            'hinges at the fold-train tertiles; moisture_mean is the '
            'mean of the two unit-converted moisture readings.',
            'SEG-04 empirical-Bayes penalty (frozen formula): with a '
            'multiplier-0.01 reference fit, per-segment sampling '
            'variance v_s from the Hessian of the mean loss; '
            'tau2 = max(mean(||d_s||^2 / width) - mean(v_s), floor); '
            'penalty_s = (N / n_s) * (vbar / tau2), ridge base 1.0.',
            'CAL-07 decay weights anchor at the fold evaluation '
            'block first day (full-train fit: the day after the last '
            'train race).',
            'UP-02 bands are defined by within-race quantiles of the '
            'base model probability; the upper band keeps tau = 1.',
            'UP-05 isotonic calibration is fitted on the base-form '
            'probability p0 (the registry difference spec), binned '
            'with at least min_bin starters per bin, PAVA-pooled.',
            'PACE-04 / PACE-05 interaction terms center the horse '
            'feature within the race and multiply by the race mean of '
            'front_rate (the registered pressure proxy).',
            'PACE-08 sub-adjustments carry the registry grid ridge; '
            'the other component adjustments carry penalty 1.0.',
            'SEG-08 band3 boundaries: <=1600 / 1601-2200 / >=2201 m. '
            'SEG-09 band5 boundaries: <=1200 / 1301-1600 / 1701-2000 '
            '/ 2101-2400 / >=2501 m.',
            'Segment support (K6) uses the full-train fit shrinkage '
            'against a multiplier-0.01 reference fit, threshold '
            'shrinkage >= 0.2 and n >= 20 (Issue #37 '
            'partially_supported criterion).',
        ],
        'metrics': ['log_loss', 'brier'],
        'label_policy': 'Final odds are benchmark-only and are never '
                        'used in estimation (poison-tested); the '
                        'longshot diagnostic is report-only (protocol '
                        'section 9).',
        'code_sha256': {name: cf.sha256(ROOT / rel)
                        for name, rel in CODE_FILES.items()},
        'approval': {
            'point': 'AP-0',
            'status': 'pending',
            'approved_by': None,
            'approved_at': None,
            'carried_rulings': [
                '2026-11-10 ruling on UP-02 / UP-08: the final-odds-'
                'band diagnostic is report-only and never a kill, '
                'selection or gate input (protocol section 9 takes '
                'precedence); the standard kills K1-K7 apply and the '
                'K7 count gate is maintained.',
            ],
        },
        'production_eligible': False,
    }


def write_plan(output_dir=RUN_DIR) -> dict:
    manifest = fd.build_manifest()
    manifest_path = Path(output_dir) / 'screening_pool_manifest.json'
    _write(manifest_path, manifest)
    plan = build_plan(manifest, manifest_path=manifest_path)
    _write(Path(output_dir) / 'research_plan.json', plan)
    return plan


def allowed_roots() -> list:
    return [ROOT / 'report' / 'research'
            / 'dataset_20260502_20260927_training_20261108T154310Z'
            / 'audit',
            ROOT / 'report' / 'research'
            / 'dataset_expansion_20251001_20260430' / 'audit_run_full']


def run(plan_path) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    if plan['approval']['status'] != 'approved':
        raise SystemExit(
            'AP-0 not approved: screening on real data is refused '
            '(plan approval status = '
            + str(plan['approval']['status']) + ')')
    manifest = json.loads(Path(plan['pool']['manifest_path']).read_text(
        encoding='utf-8'))
    if manifest['manifest_sha256'] != plan['pool']['manifest_sha256']:
        raise SystemExit('Screening manifest does not match the plan')
    fd.verify_manifest(manifest)
    races = fd.load_screening_races(manifest, allowed_roots())
    counts = {split: block['by_surface'] for split, block
              in plan['confirmation_pool']['blocks'].items()}
    results = fs.run_screening(races, counts)
    results['experiment_id'] = plan['experiment_id']
    results['plan_manifest_sha256'] = plan['pool']['manifest_sha256']
    output_dir = Path(plan_path).parent
    _write(output_dir / 'screening_results.json', results)
    _write(output_dir / 'shortlist.json', results['shortlist'])
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-plan', action='store_true',
                        help='build manifest + preregistered plan only')
    parser.add_argument('--plan', default=str(RUN_DIR
                                              / 'research_plan.json'))
    args = parser.parse_args(argv)
    if args.write_plan:
        plan = write_plan()
        print(json.dumps({'plan_written': True,
                          'experiment_id': plan['experiment_id'],
                          'manifest_sha256':
                              plan['pool']['manifest_sha256'],
                          'counts': plan['pool']['counts']},
                         ensure_ascii=False))
        return 0
    results = run(args.plan)
    print(json.dumps({'status_counts': results['status_counts'],
                      'shortlist': [s['id'] for s in
                                    results['shortlist']['shortlist']]},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
