"""Verify the published race archive without external packages."""
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_PATH = re.compile(rb'/(?:Users|home|private/tmp)/[^"\s,;]+')
BODY = re.compile(rb'<(?:html|body|script|div|table|td|span|a)\b', re.I)


def verify():
    manifest = json.loads((ROOT / 'manifest.json').read_bytes())
    ids = {group: set() for group in ('races', 'features', 'outcomes', 'audit_preparation')}
    stats = Counter()
    counts = {}
    splits = {}
    source_urls, result_urls = set(), set()
    for item in manifest['files']:
        name = item['file']
        path = ROOT / name
        if path.stat().st_size != item['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f'Archive size/hash changed: {name}')
        group = name.removesuffix('.jsonl.gz')
        group = re.sub(r'_\d{4}-\d\d$', '', group)
        num = 0
        with gzip.open(path, 'rb') as rows:
            for line in rows:
                if LOCAL_PATH.search(line) or BODY.search(line):
                    raise ValueError(f'Unexpected local path or source markup: {name}')
                row = json.loads(line)
                num += 1
                if group in ids:
                    rid = row['race_id']
                    if rid in ids[group]:
                        raise ValueError(f'Duplicate {group} race: {rid}')
                    ids[group].add(rid)
                if group == 'races':
                    existing = splits.setdefault(row['date'], row['split'])
                    if existing != row['split']:
                        raise ValueError('One date appears in multiple splits')
                    result_urls.add(row['source_url'])
                elif group == 'features':
                    if 'payouts_factual' in row or 'final_odds' in row or 'observations' in row:
                        raise ValueError('Target result present in features')
                    stats['races'] += 1
                    stats['declared'] += len(row['horses'])
                    stats['starters'] += sum(h['target_start_status'] == 'STARTER' for h in row['horses'])
                    stats['histories'] += sum(len(h['history_pre_target']) for h in row['horses'])
                elif group == 'source_index':
                    source_urls.add(row['url'])
        if num != item['records']:
            raise ValueError(f'Wrong row count: {name}')
        counts[group] = counts.get(group, 0) + num
    if any(group != ids['races'] for group in ids.values()) or len(ids['races']) != 1464:
        raise ValueError('Race identity sets do not match')
    if stats != manifest['totals'] or len(splits) != manifest['days']:
        raise ValueError('Aggregate count mismatch')
    if Counter(splits.values()) != {'train':28,'validation':8,'test':8}:
        raise ValueError('Date split mismatch')
    if (len(source_urls) != counts['source_index'] or not result_urls <= source_urls
            or counts['source_index'] != manifest['official_source_index_records']
            or counts['environment_venue_days'] != manifest['environment_venue_days']):
        raise ValueError('Source/environment coverage mismatch')
    print(json.dumps({'status':'PASS','races':len(ids['races']),'counts':dict(stats),
                      'dates':len(splits),'files':len(manifest['files'])},ensure_ascii=False))


if __name__ == '__main__':
    verify()
