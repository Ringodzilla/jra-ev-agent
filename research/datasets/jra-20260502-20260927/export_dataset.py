"""Export factual JRA observations; raw web documents stay in the collection store."""
import argparse
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

OMIT = {'raw_text', 'raw_result_text', 'raw_course_cell_html', 'raw_html', 'html',
        'parser_issues', 'notes', 'payout_tables', 'career_post_target_rows', 'entry_market',
        'path', 'saved_path', 'reused_from', 'archive_index_source', 'original_path'}
LOCAL_PATH = re.compile(r'/(?:Users|home|private/tmp)/[^"\s,;]+')
HTML = re.compile(r'<(?:html|body|script|div|table|td|span|a)\b', re.I)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def read(reference):
    data = Path(reference['path']).read_bytes()
    if sha(data) != reference['sha256']:
        raise ValueError(f"Changed input: {reference['path']}")
    return json.loads(data)


def public(value):
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()
                if key not in OMIT and not LOCAL_PATH.search(key)}
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, str):
        if HTML.search(value):
            return None
        return LOCAL_PATH.sub('[local path omitted]', value)
    return value


def payouts(tables):
    result = []
    for table in tables:
        for row in table:
            if not row or '円' not in ''.join(row):
                continue
            numeric = [' '.join(re.findall(r'\d[\d,]*', cell)) for cell in row]
            values = [{'cell': i, 'yen': [int(s.replace(',', '')) for s in re.findall(r'(\d[\d,]*)\s*円', cell)]}
                      for i, cell in enumerate(row) if '円' in cell]
            result.append({'bet_type': next((b for b in ('単勝','複勝','枠連','馬連','馬単','ワイド','3連複','3連単','三連複','三連単')
                                             if b in row[0]), None),
                           'table_numeric_cells': numeric, 'payout_cells': values})
    return result


def write_jsonl(output, name, rows):
    path = output / name
    with path.open('xb') as fh, gzip.GzipFile(filename='', fileobj=fh, mode='wb', mtime=0, compresslevel=6) as archive:
        for row in rows:
            archive.write((json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode())
    return {'file': name, 'sha256': sha(path.read_bytes()), 'records': len(rows), 'bytes': path.stat().st_size}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('study', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = args.study / 'data'
    collection = load(data / 'collection.json')
    summary = load(args.study / 'audit/summary.json')
    if collection['errors'] or not summary['batch_complete'] or summary['errors']:
        raise ValueError('Completed collection and audit preparation required')
    inventory = collection['inventory']
    race_ids = {r['race_id'] for r in inventory}
    if len(inventory) != len(race_ids) or len(race_ids) != 1464:
        raise ValueError('Incomplete or duplicate race universe')
    audits = {Path(a['path']).name.removesuffix('_audit.json'): a for a in summary['audits']}
    if set(audits) != race_ids:
        raise ValueError('Audit preparation differs from race universe')
    plan = read(collection['plan'])
    splits = {date: tag for tag, key in [('train','training_dates'),('validation','validation_dates'),('test','test_dates')]
              for date in plan[key]}
    counters, gates, surface = Counter(), Counter(), Counter()
    parts, monthly, races = [], defaultdict(list), []
    expected_sources = load(data / 'source_manifest.json')
    if len({r['url'] for r in expected_sources}) != 19659 or len(expected_sources) != 19659:
        raise ValueError('Unexpected official source universe')
    args.output.mkdir(parents=True, exist_ok=True)
    for item in sorted(inventory, key=lambda r:r['race_id']):
        rid = item['race_id']
        feature, label, audit = (read(r) for r in (item['feature_artifact'], item['label_artifact'], audits[rid]))
        if {feature['race_id'],label['race_id'],audit['race_id']} != {rid}:
            raise ValueError(f'Mismatched source identity: {rid}')
        day = feature['race_info']['race_date']
        month = day[:7]
        race_surface = feature['race_info']['target_surface']
        horse_rows = feature['horses']
        starters = sum(h['target_start_status']=='STARTER' for h in horse_rows)
        histories = sum(len(h['history_pre_target']) for h in horse_rows)
        if starters!=audit['starting_field_count'] or len(horse_rows)!=audit['declared_field_count']:
            raise ValueError(f'Mismatched field size: {rid}')
        counters.update(races=1, starters=starters, declared=len(horse_rows), histories=histories)
        gates[audit['gate']] += 1
        surface[race_surface] += 1
        core = audit['collected_audit']
        result = public(label)
        result['payouts_factual'] = payouts(label.get('payout_tables', []))
        feature_record = public(feature)
        audit_record = {'race_id':rid, 'state':audit['gate'], 'model_ready_in_preparation':audit['model_ready'],
                        'history_counts':public(core['history_counts']),
                        'field_missingness':public(core['missing_field_counts']),
                        'unapproved_finding_count':core['unapproved_finding_count'],
                        'acquisition_status':core['acquisition_coverage']['status'],
                        'declared_field_count':len(horse_rows),'starting_field_count':starters,
                        'official_nonstarters':public(audit['official_nonstarters']),
                        'raw_feature_file_sha256':item['feature_artifact']['sha256'],
                        'raw_label_file_sha256':item['label_artifact']['sha256'],
                        'raw_audit_file_sha256':audits[rid]['sha256']}
        races.append({'race_id':rid,'date':day,'track':feature['race_info']['track'],'surface':race_surface,
                      'distance_m':feature['race_info']['target_distance'],'split':splits[day],
                      'declared':len(horse_rows),'starters':starters,'histories':histories,
                      'audit_preparation_gate':audit['gate'],'source_url':item['result_url']})
        monthly[month].append((feature_record,result,audit_record))
        if len(races)%100==0:
            print(f'Projected {len(races)} races',flush=True)
        if len(races)==len(inventory) or sorted(race_ids)[len(races)][:6]!=rid[:6]:
            batch = monthly.pop(month)
            for i,kind in enumerate(('features','outcomes','audit_preparation')):
                parts.append(write_jsonl(args.output,f'{kind}_{month}.jsonl.gz',[row[i] for row in batch]))
    if (counters != {'races':1464,'starters':19617,'declared':19692,'histories':72604}
            or dict(gates)!=summary['gate_counts'] or len({r['date'] for r in races})!=44):
        raise ValueError(f'Export coverage changed: {counters}')
    env = load(data / 'environment.json')['records']
    if len(env)!=122:
        raise ValueError('Unexpected venue-day environment count')
    parts.append(write_jsonl(args.output,'races.jsonl.gz',races))
    parts.append(write_jsonl(args.output,'environment_venue_days.jsonl.gz',public(env)))
    source_keys = ('url','method','request_parameters','requested_at','captured_at','sha256','encoding')
    parts.append(write_jsonl(args.output,'source_index.jsonl.gz',[{k:r[k] for k in source_keys} for r in expected_sources]))
    manifest = {'schema_version':1,'status':'RESEARCH_ARCHIVE_RETROSPECTIVE',
                'days':44,'totals':dict(counters),'by_surface':dict(surface),'audit_preparation':dict(gates),
                'nonstarters':75,'environment_venue_days':122,'official_source_index_records':19659,
                'source_document_bodies_included':False,'formal_fit_status_at_snapshot':'NOT_STARTED',
                'collection_sha256':sha((data/'collection.json').read_bytes()),
                'source_manifest_sha256':sha((data/'source_manifest.json').read_bytes()),
                'audit_summary_sha256':sha((args.study/'audit/summary.json').read_bytes()),
                'files':parts}
    with (args.output/'manifest.json').open('x') as fh:
        json.dump(manifest,fh,ensure_ascii=False,indent=2);fh.write('\n')


if __name__=='__main__':
    main()
