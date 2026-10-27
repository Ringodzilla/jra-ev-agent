#!/usr/bin/env python3
"""Extract a descriptive publication-window panel, never an estimated causal effect."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('explicit UTC offset required for every observation')
    return parsed


def build_panel(before: dict, after: dict, *, post_time: str) -> dict:
    start = _time(post_time)
    first, last = [_time(d['completed_at']) for d in [before, after]]
    if not first < last < start:
        raise ValueError('ordered pre-start snapshots required')
    maps = [{(str(e['race_id']), str(e['horse_id'])): e for e in d['entries']} for d in [before, after]]
    if (not maps[0] or maps[0].keys() != maps[1].keys()
            or any(len(m) != len(d['entries']) for m, d in zip(maps, [before, after]))):
        raise ValueError('same complete unique horse set required')
    rows = []
    for key, old in maps[0].items():
        new = maps[1][key]
        row = dict(race_id=key[0], horse_id=key[1], horse_number=new['horse_number'],
                   status='UNMEASURED', log_odds_change=None, reason=None)
        if old['body_weight_status'] != 'unpublished' or new['body_weight_status'] != 'published':
            rows.append(dict(row, reason='NO_UNPUBLISHED_TO_PUBLISHED_BRACKET'))
            continue
        left, right = [_time(e['body_weight_captured_at']) for e in [old, new]]
        if not left <= first < right <= last:
            raise ValueError('body-weight observation times must match snapshot order')
        quotes = []
        for data, entry, cutoff in [(before, old, first), (after, new, last)]:
            matched = [q for q in data['odds_snapshots'] if str(q['race_id']) == key[0]
                       and str(q['horse_number']) == str(entry['horse_number'])
                       and _time(q['captured_at']) <= cutoff]
            if not matched:
                break
            latest = max(_time(q['captured_at']) for q in matched)
            values = {float(q['current_odds']) for q in matched if _time(q['captured_at']) == latest}
            if len(values) != 1:
                break
            value = values.pop()
            if not math.isfinite(value) or value <= 1:
                break
            quotes.append(dict(odds=value, captured_at=latest.isoformat()))
        if len(quotes) != 2:
            rows.append(dict(row, reason='MISSING_INVALID_OR_AMBIGUOUS_QUOTES'))
            continue
        weight, change = float(new['current_body_weight']), float(new['body_weight_change'])
        if not all(math.isfinite(v) for v in [weight, change]) or weight <= 0 or weight - change <= 0:
            raise ValueError('observed body weight and signed change required')
        if not _time(quotes[0]['captured_at']) < _time(quotes[1]['captured_at']):
            rows.append(dict(row, reason='NO_NEW_ODDS_OBSERVATION'))
            continue
        rows.append(dict(row, status='OBSERVED_SNAPSHOT_PRICE_CHANGE', reason=None,
            current_body_weight=weight, body_weight_change=change,
            publication_window=dict(after=left.isoformat(), on_or_before=right.isoformat()),
            earlier_snapshot_quote=quotes[0], later_snapshot_quote=quotes[1],
            later_quote_confirmed_after_publication=(_time(quotes[1]['captured_at']) >= right),
            log_odds_change=math.log(quotes[1]['odds']) - math.log(quotes[0]['odds'])))
    return dict(kind='DESCRIPTIVE_WINDOW_PANEL_NOT_CAUSAL_OR_PREDICTION', horses=rows,
                measured_count=sum(r['status']=='OBSERVED_SNAPSHOT_PRICE_CHANGE' for r in rows),
                limitations=['Capture times bracket publication but are not exact release times.',
                    'A later-snapshot quote can precede publication; inspect later_quote_confirmed_after_publication.',
                    'Market movements within the interval include other information and demand.',
                    'These changes cannot be used as a future body-weight coefficient without training and validation.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--post-time', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = build_panel(json.loads(args.before.read_text()), json.loads(args.after.read_text()), post_time=args.post_time)
    report['input_artifacts'] = [dict(path=str(p.resolve()), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in [args.before,args.after]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(dict(output=str(args.output.resolve()), measured_count=report['measured_count'])))


if __name__ == '__main__':
    main()
