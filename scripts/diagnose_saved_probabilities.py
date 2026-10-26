#!/usr/bin/env python3
"""Retrospective diagnostics of frozen probabilities; never runs a prediction."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def diagnose(ev_rows: list[dict], finishers: list[dict], *, race_id: str) -> dict:
    """Compare stored estimates with uniform and normalized pre-race odds."""
    numbers = [str(r['horse_number']) for r in ev_rows]
    actual = {str(r['horse_number']): int(r['place']) for r in finishers}
    if (len(numbers) < 2 or len(set(numbers)) != len(numbers)
            or len(actual) != len(finishers) or set(numbers) != set(actual)
            or sorted(actual.values()) != list(range(1, len(numbers) + 1))
            or any(r.get('race_id') != race_id for r in ev_rows)):
        raise ValueError('complete unique same-race lineup and complete result required')
    probs = [float(r['win_prob']) for r in ev_rows]
    odds = [float(r['current_odds']) for r in ev_rows]
    if (not all(math.isfinite(p) and 0 < p < 1 for p in probs)
            or abs(sum(probs) - 1) > len(probs) * 0.0000005
            or not all(math.isfinite(o) and o > 1 for o in odds)):
        raise ValueError('valid serialized probability simplex and pre-race odds required')
    # Normalize only the serialization residual, preserving stored values below.
    normalized = [p / sum(probs) for p in probs]
    implied = [1 / o for o in odds]
    market = [p / sum(implied) for p in implied]
    uniform = [1 / len(numbers)] * len(numbers)
    winner = numbers.index(next(n for n in numbers if actual[n] == 1))

    def metrics(ps):
        entropy = -sum(p * math.log(p) for p in ps)
        return dict(winner_log_loss=-math.log(ps[winner]),
                    multiclass_brier_sum=sum((p - (i == winner)) ** 2 for i, p in enumerate(ps)),
                    entropy=entropy, effective_field_size=math.exp(entropy),
                    min_probability=min(ps), max_probability=max(ps))

    model_high = {n for n, p, o in zip(numbers, probs, odds) if p * o >= 1.1}
    uniform_high = {n for n, o in zip(numbers, odds) if o / len(numbers) >= 1.1}
    rows = [dict(horse_number=n, horse_name=r['horse_name'], saved_win_prob=p,
                 saved_odds=o, saved_probability_times_odds=p * o,
                 uniform_probability_times_odds=o / len(numbers), finish=actual[n])
            for n, r, p, o in zip(numbers, ev_rows, probs, odds)]
    return dict(race_id=race_id, field_size=len(numbers), metrics={
        'saved_model': metrics(normalized), 'uniform': metrics(uniform),
        'normalized_pre_race_market': metrics(market)},
        high_ev_comparison=dict(threshold=1.1, model_count=len(model_high), uniform_count=len(uniform_high),
            overlap_count=len(model_high & uniform_high),
            model_only=sorted(model_high - uniform_high, key=int),
            uniform_only=sorted(uniform_high - model_high, key=int)),
        horses=sorted(rows, key=lambda r: -r['saved_probability_times_odds']),
        limitations=['One race cannot establish calibration, long-run EV, or a promotion decision.',
            'Baselines are evaluation references, not production probabilities or purchase recommendations.',
            'Saved model inputs are not re-imputed, inferred or recomputed by this diagnostic.'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--retrospective', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = [args.run_dir / n for n in ['04_ev_calculator.json', 'final_decision.json']] + [args.retrospective]
    ev, decision, retrospective = [json.loads(p.read_text()) for p in paths]
    if (retrospective['race_id'] != decision['race_id']
            or datetime.fromisoformat(decision['issued_at']) >= datetime.fromisoformat(decision['post_time'])):
        raise ValueError('only same-race frozen pre-start outputs accepted')
    recorded = {x['path']: x['sha256'] for x in retrospective['input_artifacts']}
    if any(recorded.get(str(p.resolve())) != hashlib.sha256(p.read_bytes()).hexdigest() for p in paths[:2]):
        raise ValueError('frozen artifacts differ from retrospective provenance')
    report = diagnose(ev['ev_rows'], retrospective['official_result']['finishers'], race_id=decision['race_id'])
    report['kind'] = 'POST_RACE_FROZEN_OUTPUT_DIAGNOSTIC_NOT_NEW_PREDICTION'
    report['input_artifacts'] = [dict(path=str(p.resolve()), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps(dict(output=str(args.output.resolve()), metrics=report['metrics'],
                         high_ev_comparison=report['high_ev_comparison']), ensure_ascii=False))


if __name__ == '__main__':
    main()
