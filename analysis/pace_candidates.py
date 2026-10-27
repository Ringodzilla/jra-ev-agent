"""Canonical conditional ticket probabilities at one observed price snapshot."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
import re

from analysis.candidate_ev import build_candidate_evaluations
from analysis.pace_conditioning import PACES


def build_pace_candidates(conditioned: dict, entries: list[dict], combo_odds: list[dict]) -> dict:
    """Average canonical event probabilities within a pace, not marginal win ranks.

    Repeated source draws are evaluated once and weighted by bootstrap counts.
    All ticket prices here are observed fixed snapshot quotes, not simulated
    combination prices. The underlying ranking model remains canonical PL.
    """
    horse_ids = conditioned['marginal']['horse_ids']
    if ([r['horse_id'] for r in entries] != horse_ids
            or len({str(r['horse_number']) for r in entries}) != len(entries)
            or len({r['race_id'] for r in entries}) != 1):
        raise ValueError('same ordered complete horse metadata for one race required')
    trials = conditioned['marginal']['trials']
    if not trials:
        raise ValueError('sampled conditional trials required')
    candidates = {}
    cache = {}
    totals = Counter(t['pace'] for t in trials)
    grouped = Counter((t['pace'], t['source_trial_id']) for t in trials)
    for trial in trials:
        key = (trial['pace'], trial['source_trial_id'])
        if key in cache:
            if cache[key]['probabilities'] != trial['win_probabilities']:
                raise ValueError('repeated source trial changed probabilities')
        else:
            probs = trial['win_probabilities']
            if trial['pace'] not in PACES or len(probs) != len(entries):
                raise ValueError('one explicit conditional probability per horse required')
            rows = [dict(row, win_prob=p) for row, p in zip(entries, probs)]
            result = build_candidate_evaluations(rows, combo_odds)
            if result['validation']['status'] != 'OK':
                raise ValueError('canonical conditional probabilities or odds are invalid')
            cache[key] = dict(probabilities=deepcopy(probs), candidates=result['candidate_evaluations'])
            for candidate in result['candidate_evaluations']:
                k = candidate['key']
                if k not in candidates:
                    candidates[k] = dict(candidate_key=k, bet_type=candidate['bet_type'],
                        horse_numbers=[] if candidate['bet_type']=='wakuren' else re.findall(r'\d+', candidate['canonical_combination']),
                        frame_numbers=re.findall(r'\d+', candidate['canonical_combination']) if candidate['bet_type']=='wakuren' else [],
                        number_kind='frame' if candidate['bet_type']=='wakuren' else 'horse',
                        source_snapshot_id=candidate['snapshot_id'], source_captured_at=candidate['captured_at'],
                        combination=candidate['canonical_combination'], pace_metrics={})
                metric = candidates[k]['pace_metrics'].setdefault(trial['pace'], dict(hit_probability=0.0,
                    odds=candidate['official_odds'], ev=0.0, status='EVALUATED'))
                weight = grouped[key] / totals[trial['pace']]
                metric['hit_probability'] += candidate['hit_prob'] * weight
                metric['ev'] += candidate['ev'] * weight
    for candidate in candidates.values():
        for pace in PACES:
            candidate['pace_metrics'].setdefault(pace, dict(status='NOT_EVALUATED', reason='NO_SAMPLED_TRIALS_FOR_PACE'))
        if any(not math.isfinite(m['ev']) for m in candidate['pace_metrics'].values() if m['status']=='EVALUATED'):
            raise ValueError('conditional EV must be finite')
    return dict(kind='RESEARCH_CONDITIONAL_CANDIDATES_NOT_FORMAL_TICKETS',
                price_basis='fixed_current_official_snapshot', probability_basis='mean_of_trial_canonical_PL_event_probabilities',
                selected_pace=conditioned['mainline']['pace'] if conditioned['mainline'].get('statistical_confirmation') is True else None,
                pace_confirmation=deepcopy(conditioned['mainline'].get('fixing_status', 'UNCONFIRMED')),
                candidates=list(candidates.values()),
                limitations=['Frame combinations use frame_numbers; horse combinations use horse_numbers.',
                    'Conditional event probabilities retain the canonical ranking model assumptions.',
                    'This adapter does not predict combination-price responses.'])
