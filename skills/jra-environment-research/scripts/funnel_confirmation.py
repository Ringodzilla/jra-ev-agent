"""Confirmation-layer data access and gate evaluation (Issue #40).

Implements protocol sections 3.2, 6, 7 and 8 for the integrated funnel
run.  The confirmation pool is reachable only through this module:

- The validation block enters through an explicit race list built from
  admission-summary metadata (race IDs, splits, segments) and is bound
  by the handover block hash: SHA-256 of the canonical JSON array of
  ``{race_id, surface}`` sorted by race_id (UTF-8, ``sort_keys``, default
  separators), exactly as recorded in
  ``confirmation_blocks_manifest.json``.  The hash is verified before
  any race artifact is opened.
- This loader refuses every split other than ``validation`` and every
  source other than the expansion archive, so the test block cannot be
  loaded through it at all.  Outcome records are selected by race ID;
  only manifest-listed validation races are ever accessed for labels.
- Estimation stays on the screening side: candidates are fitted with
  ``funnel_engine.fit_hypothesis`` on the screening train pool at the
  grid frozen in the shortlist, and the confirmation block is used for
  evaluation only (``condition_fit.evaluate``, the repo metric
  definitions, unchanged).

Gate (protocol section 8, transcribed in the research plan): counts at
least 100 train / 20 validation / 20 test in the splits the candidate
is evaluated on; log loss must improve over the base form on both
validation and test; test Brier must not worsen; the win-top1 policy
ROI must not worsen on either block.  This module evaluates the
validation side; a hypothesis proceeds to the test block only when
every validation-side condition holds.  Margins are recorded with each
judgement, and a pass whose log-loss margin lies within the screening
fold-to-fold variability is flagged selection-fragile (section 7).

Research track only: every record carries ``production_eligible=False``.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import condition_fit as cf  # noqa: E402
import funnel_data as fd  # noqa: E402
import funnel_engine as fe  # noqa: E402
from structural_features import structural_features, COMPONENT_WEIGHTS  # noqa: E402

EXPANSION_SOURCE = 'expansion_20251001_20260430'
CONFIRMATION_MINIMUM = {'train': 100, 'validation': 20, 'test': 20}


class Blocked(cf.Blocked):
    """Confirmation setup failure; never repaired silently."""


# ---------------------------------------------------------------------------
# Block list binding (handover manifest definition).
# ---------------------------------------------------------------------------

def block_list_sha256(rows) -> str:
    """SHA-256 of the canonical {race_id, surface} array.

    ``rows`` is an iterable of mappings with ``race_id`` and ``surface``;
    the array is sorted by race_id and serialized with
    ``ensure_ascii=False`` and ``sort_keys`` (default separators),
    matching the handover block-manifest definition exactly.
    """
    array = sorted(({'race_id': r['race_id'], 'surface': r['surface']}
                    for r in rows), key=lambda r: r['race_id'])
    text = json.dumps(array, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_block_entries(summary, split='validation') -> list:
    """Manifest entries for one confirmation block, from metadata only.

    Only admission-summary metadata is read (race IDs, splits, segments
    and audit references); no audit artifact, outcome or label is
    opened.  Entries are restricted to ADMITTED_RESEARCH races of the
    requested split.
    """
    entries = []
    for item in summary['races']:
        if item['research_admission']['status'] != 'ADMITTED_RESEARCH':
            continue
        if item['split'] != split:
            continue
        segment = item['segment']
        race_id = item['race_id']
        entries.append({
            'race_id': race_id,
            'source': EXPANSION_SOURCE,
            'split': split,
            'pool_block': 'confirmation_' + split,
            'surface': segment['surface'],
            'venue': segment['venue'],
            'distance': int(segment['distance']),
            'band': segment['band'],
            'date': (f"{race_id[0:4]}-{race_id[4:6]}-{race_id[6:8]}"),
            'audit_path': item['audit']['path'],
            'audit_sha256': item['audit']['sha256'],
        })
    entries.sort(key=lambda e: e['race_id'])
    if len({e['race_id'] for e in entries}) != len(entries):
        raise Blocked('Duplicate race ID in confirmation block')
    return entries


def check_block_entries(entries, split='validation'):
    """Refuse anything that is not a pure validation-block entry list."""
    for entry in entries:
        if entry['source'] != EXPANSION_SOURCE:
            raise Blocked('Confirmation entry from a foreign source: '
                          + entry['race_id'])
        if entry['split'] != split:
            raise Blocked(
                f"Confirmation loader takes split {split!r} only; "
                f"refused {entry['split']!r} entry: " + entry['race_id'])


# ---------------------------------------------------------------------------
# Loading: validation races only, full hash verification per race.
# ---------------------------------------------------------------------------

def load_confirmation_races(entries, summary_path, summary_sha256,
                            dataset_dir, allowed_root,
                            expected_block_sha256) -> list:
    """Load exactly the manifest's validation races.

    The block hash is verified before any race artifact is opened, and
    every per-race check mirrors ``funnel_data.load_screening_races``
    (summary hash, admission status, audit hash and PASS gates, segment
    agreement, normalized hash, full starting field).  Labels are
    attached only after the feature side of a race is complete.
    """
    check_block_entries(entries)
    digest = block_list_sha256(entries)
    if digest != expected_block_sha256:
        raise Blocked('Confirmation block hash mismatch: computed '
                      + digest + ', expected ' + expected_block_sha256)
    summary_path = fd._checked_path(summary_path, [allowed_root])
    if cf.sha256(summary_path) != summary_sha256:
        raise Blocked('Expansion summary changed')
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    by_id = {item['race_id']: item for item in summary['races']}
    outcomes_all = cf._load_outcomes(Path(dataset_dir))
    wanted = {e['race_id']: e for e in entries}
    outcomes = {}
    for race_id in wanted:
        if race_id not in outcomes_all:
            raise Blocked('Outcome missing for confirmation race: '
                          + race_id)
        outcomes[race_id] = outcomes_all[race_id]
    del outcomes_all
    races = []
    for race_id, entry in wanted.items():
        item = by_id.get(race_id)
        if item is None:
            raise Blocked('Confirmation race absent from summary: '
                          + race_id)
        if item['research_admission']['status'] != 'ADMITTED_RESEARCH':
            raise Blocked('Confirmation race not admitted: ' + race_id)
        if item['split'] != entry['split'] \
                or item['segment']['surface'] != entry['surface']:
            raise Blocked('Manifest/summary mismatch: ' + race_id)
        audit_path = fd._checked_path(entry['audit_path'], [allowed_root])
        if cf.sha256(audit_path) != entry['audit_sha256']:
            raise Blocked('Audit artifact changed: ' + race_id)
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
        if audit['environment_gate'] != 'PASS' \
                or audit['surface_gate'] != 'PASS':
            raise Blocked('Admitted race without PASS gates: ' + race_id)
        if audit['split'] != item['split'] \
                or audit['segment'] != item['segment']:
            raise Blocked('Summary/audit segment mismatch: ' + race_id)
        normalized_ref = audit['normalized_artifact']
        normalized_path = fd._checked_path(normalized_ref['path'],
                                           [allowed_root])
        if cf.sha256(normalized_path) != normalized_ref['sha256']:
            raise Blocked('Normalized artifact changed: ' + race_id)
        normalized = json.loads(normalized_path.read_text(encoding='utf-8'))
        groups = defaultdict(list)
        for row in normalized['rows']:
            groups[str(row['horse_number'])].append(row)
        starters = normalized['entries']
        if set(groups) != {str(e['horse_number']) for e in starters}:
            raise Blocked('Full horse set missing: ' + race_id)
        rows = []
        for starter in starters:
            rows.append(structural_features(
                groups[str(starter['horse_number'])], starter))
        scores = np.array([math.fsum(
            COMPONENT_WEIGHTS[k] * float(r[k])
            for k in COMPONENT_WEIGHTS) for r in rows])
        base = cf._softmax(scores)
        for row, score, prob in zip(rows, scores, base):
            row['S_base'] = float(score)
            row['win_prob'] = float(prob)
        target = starters[0]
        environment = fd._environment(audit, target, race_id,
                                      entry['surface'])
        nonstarters = {str(n['horse_number']): n['horse_name']
                       for n in audit.get('official_nonstarters') or []}
        labels = cf._race_labels(
            outcomes[race_id],
            {str(e['horse_number']) for e in starters}, nonstarters)
        market = np.array([1.0 / labels['final_odds'][
            str(r['horse_number'])] for r in rows])
        races.append({
            'race_id': race_id,
            'date': target['target_race_date'],
            'split': item['split'],
            'pool_block': entry['pool_block'],
            'source': EXPANSION_SOURCE,
            'surface': entry['surface'],
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
    if len(races) != len(wanted):
        missing = sorted(set(wanted) - {r['race_id'] for r in races})
        raise Blocked(f'Confirmation races missing from sources: {missing}')
    races.sort(key=lambda r: (r['date'], r['race_id']))
    return races


# ---------------------------------------------------------------------------
# Gate evaluation (protocol section 8, validation side) and margins.
# ---------------------------------------------------------------------------

def validation_gate(candidate, base, fit_races, eval_races) -> dict:
    """Validation-side gate checks with margins (sections 7 and 8)."""
    log_loss_margin = float(base['log_loss'] - candidate['log_loss'])
    roi_margin = float(candidate['win_top1_policy_roi']
                       - base['win_top1_policy_roi'])
    checks = {
        'train_count_min_100': {
            'passed': fit_races >= CONFIRMATION_MINIMUM['train'],
            'fit_races': fit_races,
            'minimum': CONFIRMATION_MINIMUM['train']},
        'validation_count_min_20': {
            'passed': eval_races >= CONFIRMATION_MINIMUM['validation'],
            'validation_races': eval_races,
            'minimum': CONFIRMATION_MINIMUM['validation']},
        'log_loss_improves_over_base': {
            'passed': log_loss_margin > 0.0,
            'margin': log_loss_margin,
            'candidate_log_loss': candidate['log_loss'],
            'base_log_loss': base['log_loss']},
        'win_policy_roi_not_worse': {
            'passed': roi_margin >= 0.0,
            'margin': roi_margin,
            'candidate_roi': candidate['win_top1_policy_roi'],
            'base_roi': base['win_top1_policy_roi']},
    }
    passed = all(c['passed'] for c in checks.values())
    return {
        'checks': checks,
        'log_loss_margin': log_loss_margin,
        'roi_margin': roi_margin,
        'brier_change': float(candidate['brier'] - base['brier']),
        'verdict': ('proceed_to_test' if passed
                    else 'gate_failed_at_validation'),
        'failed_checks': [name for name, c in checks.items()
                          if not c['passed']],
    }


def fold_variability(fold_improvements) -> dict:
    """Screening fold-to-fold variability of the log-loss improvement."""
    values = [float(v) for v in fold_improvements]
    return {
        'fold_improvements_vs_base': values,
        'std': float(np.std(values)) if values else 0.0,
        'min': float(min(values)) if values else 0.0,
        'max': float(max(values)) if values else 0.0,
        'range': (float(max(values) - min(values)) if values else 0.0),
    }


def selection_fragile(margin, variability) -> bool:
    """A pass whose margin is within fold variability (section 7)."""
    return 0.0 < margin <= variability['std']


def _subset(races, surface):
    if surface == 'both':
        return list(races)
    return [r for r in races if r['surface'] == surface]


def evaluate_confirmation(train_races, eval_races, spec, grid_point,
                          screening_fold_improvements) -> dict:
    """Fit one frozen shortlist hypothesis and evaluate it once.

    Estimation uses only ``train_races`` (the screening train pool);
    ``eval_races`` is the opened confirmation block, evaluation only.
    The comparator is the base form, per the section 8 gate wording.
    """
    surface = spec['surface']
    train = _subset(train_races, surface)
    evals = _subset(eval_races, surface)
    model = fe.fit_hypothesis(train, spec, grid_point,
                              {'reference_date': None})
    candidate = cf.evaluate(evals, lambda race: fe.model_probs(race, model))
    base = cf.evaluate(evals, lambda race: fe.model_probs(race, None))
    market = cf.evaluate(evals, cf.market_probs)
    gate = validation_gate(candidate, base, len(train), len(evals))
    variability = fold_variability(screening_fold_improvements)
    fragile = (gate['verdict'] == 'proceed_to_test'
               and selection_fragile(gate['log_loss_margin'], variability))
    diagnostic = cf.upset_analysis(
        evals, lambda race: fe.model_probs(race, model),
        lambda race: fe.model_probs(race, None))
    return {
        'id': spec['id'],
        'family': spec['family'],
        'surface': surface,
        'selected_grid': grid_point,
        'fit_races': len(train),
        'eval_races': len(evals),
        'candidate': candidate,
        'base_form': base,
        'market': market,
        'log_loss_improvement_over_base': gate['log_loss_margin'],
        'gate_validation_side': gate,
        'screening_fold_variability_vs_base': variability,
        'selection_fragile': fragile,
        'longshot_diagnostic_report_only': {
            'scope': 'validation block; descriptive only (protocol '
                     'section 9); never a selection, kill or gate input',
            'longshot_winners_odds_ge_10':
                diagnostic['longshot_winners_odds_ge_10'],
            'longshot_winners_odds_ge_20':
                diagnostic['longshot_winners_odds_ge_20'],
        },
        'production_eligible': False,
    }
