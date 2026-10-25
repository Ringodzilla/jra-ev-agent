#!/usr/bin/env python3
"""Reconstruct factual race/event/career distinctions from preserved official HTML."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from jra_scraper.parser import JRAParser


def day(value):
    p = re.findall(r'\d+', str(value))
    return f'{int(p[0]):04d}-{int(p[1]):02d}-{int(p[2]):02d}' if len(p) >= 3 else ''


def reg(value):
    match = re.search(r'dud\d{2}(\d{10})', str(value))
    return match[1] if match else None


def complete_career(html, before_date):
    """Require all enumerated started runs to equal both official summary counts."""
    parser = JRAParser(base_url='https://jra.jp')
    soup = BeautifulSoup(html, 'html.parser')
    table = next((t for t in soup.select('table') if t.caption is not None
                  and t.caption.get_text(' ', strip=True) == '出走レース'), None)
    if table is None:
        table = parser._select_last5_table(soup)
    if table is None:
        return {'status': 'UNVERIFIED', 'reason': 'history_table_missing'}
    matches = parser._build_header_matches(parser._extract_headers(table), parser.HISTORY_HEADER_ALIASES)
    all_rows, events, nonstarters, unknown = [], [], [], []
    headers = parser._extract_headers(table)
    for tr in table.select('tr'):
        cells = [parser._norm(td.get_text(' ', strip=True)) for td in tr.select('td')]
        if not cells:
            continue
        if tr.select_one('.caution.no_data') is not None and '該当するデータがありません' in tr.get_text():
            continue
        raw_text = tr.get_text(' ', strip=True)
        if any(re.fullmatch(r'JRA(?:へ|より)転[入出]', cell) for cell in cells):
            events.append({'raw_text': raw_text, 'date': day(raw_text)})
            continue
        repaired = parser._repair_cells(cells, len(headers), matches, None, context={}, aggressive=False)
        row = parser._map_row(matches, repaired)
        row['raw_text'] = raw_text
        row['normalized_date'] = day(row.get('date'))
        status = row.get('position', '')
        if any(x in status for x in ('取消', '除外')):
            nonstarters.append(row)
        elif row['normalized_date'] and (re.fullmatch(r'\d+', status) or any(x in status for x in ('中止', '失格'))):
            all_rows.append(row)
        else:
            unknown.append(row)
    totals = {}
    for summary in soup.select('table'):
        caption = summary.caption.get_text(' ', strip=True) if summary.caption else ''
        kind = next((name for name in ('平地レース合計', '障害レース合計') if name in caption), None)
        if not kind:
            continue
        data_rows = [tr for tr in summary.select('tr') if tr.select('td')]
        if len(data_rows) != 1:
            continue
        values = [c.get_text(' ', strip=True).replace(',', '') for c in data_rows[0].select('td')]
        if len(values) >= 5 and all(re.fullmatch(r'\d+', x) for x in values[:5]) and sum(map(int, values[:4])) == int(values[4]):
            totals[kind] = int(values[4])
    keys = [(r['normalized_date'], r.get('course'), r.get('race_name')) for r in all_rows]
    complete = (len(totals) == 2 and not unknown and len(keys) == len(set(keys))
                and sum(totals.values()) == len(all_rows))
    before = [r for r in all_rows if r['normalized_date'] < before_date]
    return dict(status='VERIFIED_COMPLETE_OFFICIAL_HISTORY' if complete else 'UNVERIFIED',
                career_before_target=len(before) if complete else None,
                official_totals=totals, listed_started_count=len(all_rows),
                before_target_count=len(before), started_rows=all_rows,
                nonstarter_rows=nonstarters, nonrace_events=events, unknown_rows=unknown,
                reason='All official started rows equal official aggregate career totals' if complete
                else 'Full-history/aggregate equality not proven')


def matched_result_row(html, horse):
    soup = BeautifulSoup(html, 'html.parser')
    wanted = re.sub(r'\s+', '', horse['horse_name'])
    candidates = []
    for tr in soup.select('tr'):
        cell = tr.select_one('td.horse')
        if cell is None or re.sub(r'\s+', '', cell.get_text(' ', strip=True)) != wanted:
            continue
        hrefs = [a.get('href', '') for a in cell.select('a[href]')]
        if hrefs and not any(reg(href) == reg(horse['horse_id']) and reg(href) for href in hrefs):
            continue
        candidates.append(tr)
    return candidates[0] if len(candidates) == 1 else None


def jockey_identity_proof(detail_html, result_html, horse, history_date):
    detail_soup = BeautifulSoup(detail_html, 'html.parser')
    detail_rows = [tr for tr in detail_soup.select('tr') if tr.select_one('td.date') is not None
                   and day(tr.select_one('td.date').get_text(' ', strip=True)) == history_date
                   and tr.select_one('td.jockey') is not None]
    result_soup = BeautifulSoup(result_html, 'html.parser')
    result_dates = result_soup.select('.cell.date')
    actual = matched_result_row(result_html, horse)
    if (len(detail_rows) != 1 or len(result_dates) != 1 or actual is None
            or day(result_dates[0].get_text(' ', strip=True)) != history_date):
        return {'status': 'UNVERIFIED'}
    actual_jockey = actual.select_one('td.jockey')
    detail_link = detail_rows[0].select_one('td.jockey a')
    result_link = actual_jockey.select_one('a') if actual_jockey is not None else None
    if detail_link is None or result_link is None:
        return {'status': 'UNVERIFIED'}
    def identity(link):
        match = re.search(r'kmk(\d{6})', str(link))
        return match[1] if match else None
    detail_id, result_id = identity(detail_link), identity(result_link)
    if detail_id is None or detail_id != result_id:
        return {'status': 'UNVERIFIED'}
    horse_link = actual.select_one('td.horse a')
    return dict(status='VERIFIED_SAME_OFFICIAL_JOCKEY_ID', jockey_id=detail_id,
                canonical_name=detail_link.get_text(' ', strip=True),
                raw_detail_jockey=str(detail_link), raw_result_jockey=str(actual_jockey),
                match_count=1, horse_name=horse['horse_name'],
                horse_href=horse_link.get('href', '') if horse_link is not None else '',
                historical_date=history_date)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch-dir', type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument('--input-dir', default='enriched_v2')
    ap.add_argument('--manifest', default='manifest_v2.json')
    ap.add_argument('--output-dir', '--output', default='enriched_v3')
    ap.add_argument('--race-id', action='append', help='Numeric race ID; repeat for a partial immutable snapshot')
    args = ap.parse_args()
    base = args.batch_dir.resolve()
    output = (base / args.output_dir).resolve()
    if not output.is_relative_to(base):
        raise ValueError('Output must stay inside the isolated batch directory')
    output.mkdir(exist_ok=False)
    raw_manifest = (base / args.manifest).read_bytes()
    manifest = json.loads(raw_manifest)
    manifest_snapshots = [{'path': str((base / args.manifest).resolve()), 'sha256': hashlib.sha256(raw_manifest).hexdigest()}]
    if args.manifest != 'manifest.json':
        original_bytes = (base / 'manifest.json').read_bytes()
        manifest = json.loads(original_bytes) + manifest
        manifest_snapshots.insert(0, {'path': str((base / 'manifest.json').resolve()),
                                      'sha256': hashlib.sha256(original_bytes).hexdigest()})
    indexed = {item['url']: item for item in manifest}
    cache = {}

    def source(url):
        if url not in indexed:
            return None, None
        if url not in cache:
            item = indexed[url]
            data = Path(item['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Source hash mismatch: ' + url)
            cache[url] = (data.decode(item.get('encoding') or 'utf-8'), item)
        return cache[url]

    input_paths = [p for p in sorted((base / args.input_dir).glob('*.json'))
                   if re.fullmatch(r'\d{8}_\d{2}_\d{2}\.json', p.name)
                   and (not args.race_id or p.stem in args.race_id)]
    input_hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
    code_paths = [Path(__file__).resolve(), ROOT / 'jra_scraper/parser.py']
    code_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths}
    status_path = output / 'repair_snapshot_status.json'
    def write_status(value):
        status_path.write_text(json.dumps(dict(status=value, input_file_sha256=input_hashes,
            source_manifest_snapshots=manifest_snapshots, code_sha256=code_hashes), ensure_ascii=False, indent=2) + '\n')
    write_status('RUNNING')
    changes, summary = [], []
    for path in input_paths:
        if not re.fullmatch(r'\d{8}_\d{2}_\d{2}\.json', path.name):
            continue
        if args.race_id and path.stem not in args.race_id:
            continue
        input_bytes = path.read_bytes()
        before_sha = hashlib.sha256(input_bytes).hexdigest()
        if before_sha != input_hashes[str(path.resolve())]:
            write_status('BLOCKED_INPUT_CHANGED')
            raise ValueError('Input changed before reconstruction: ' + str(path))
        race = json.loads(input_bytes)
        declared_entry_set = [dict(horse_id=h['horse_id'], horse_name=h['horse_name'],
                                   declared_horse_number=h.get('horse_number')) for h in race['horses']]
        nonstarters = []
        result_html, result_source = source(race['result_url'])
        active = []
        for horse in race['horses']:
            target_date = horse.get('target_race_date', '2026-10-24')
            actual = matched_result_row(result_html or '', horse)
            place = actual.select_one('td.place') if actual is not None else None
            status = place.get_text(' ', strip=True) if place else ''
            if status in {'取消', '除外'}:
                original = copy.deepcopy(horse)
                num = actual.select_one('td.num')
                official_number = num.get_text(' ', strip=True) if num else ''
                horse.update(horse_number=official_number, entry_status=status, participated=False,
                             participation_provenance=result_source)
                nonstarters.append(horse)
                changes.append(dict(race_id=race['race_id'], horse_name=horse['horse_name'],
                    action='OFFICIAL_NONSTARTER_SEPARATED', before=original, after=copy.deepcopy(horse),
                    source=result_source, raw_text=actual.get_text(' ', strip=True)))
                continue
            active.append(horse)
            detail_html, detail_source = source(horse.get('horse_detail_url') or horse.get('horse_url'))
            if detail_html is None:
                continue
            career = complete_career(detail_html, target_date)
            career['source'] = detail_source
            horse['career_verification'] = career
            if career['status'] == 'VERIFIED_COMPLETE_OFFICIAL_HISTORY':
                horse['career_starts'] = career['career_before_target']
            old = horse.get('history_pre_target', [])
            for history in old:
                historical_date = day(history.get('date'))
                conflicts = [c for c in race.get('history_conflicts', []) if c.get('field') == 'jockey'
                             and c.get('horse') == horse['horse_name'] and day(c.get('date')) == historical_date]
                if not conflicts:
                    continue
                history_url = (history.get('race_result_url') or history.get('odds_recovery_result_url')
                               or history.get('recovery_target_url'))
                historical_html, historical_source = source(history_url)
                if historical_html is None:
                    continue
                proof = jockey_identity_proof(detail_html, historical_html, horse, historical_date)
                if proof['status'] != 'VERIFIED_SAME_OFFICIAL_JOCKEY_ID':
                    continue
                old_jockey = history.get('jockey')
                history['jockey'] = proof['canonical_name']
                proof.update(detail_source=detail_source, result_source=historical_source,
                             previous_value=old_jockey, selected_value=history['jockey'])
                history['jockey_identity_verification'] = proof
                for conflict in conflicts:
                    conflict['fact_resolution'] = proof
                changes.append(dict(race_id=race['race_id'], horse_name=horse['horse_name'],
                    date=historical_date, action='JOCKEY_IDENTITY_RECONCILED_FROM_OFFICIAL_IDS',
                    before=old_jockey, after=history['jockey'], provenance=proof))
            old_by_date = {day(row.get('date')): row for row in old}
            event_dates = {event['date'] for event in career['nonrace_events']}
            nonstarter_dates = {row['normalized_date'] for row in career['nonstarter_rows']}
            removed = [row for row in old if day(row.get('date')) in event_dates | nonstarter_dates]
            if not removed:
                continue
            issue_sink = []
            parsed = JRAParser(base_url='https://jra.jp').parse_horse_last5(
                detail_html, horse['race_id'], horse['horse_id'], horse['horse_name'],
                horse.get('horse_url', ''), before_date=target_date, issue_sink=issue_sink)
            rebuilt = []
            for index, row in enumerate(parsed, 1):
                date = day(row.get('date'))
                if date in event_dates | nonstarter_dates:
                    continue
                previous = old_by_date.get(date)
                selected = copy.deepcopy(previous) if previous else row
                selected['run_index'] = str(index)
                selected['normalized_date'] = date
                if not previous:
                    selected['fact_reconstruction_provenance'] = detail_source
                rebuilt.append(selected)
            horse['history_pre_target'] = rebuilt
            horse['history_count'] = len(rebuilt)
            horse['nonrace_history_events'] = career['nonrace_events']
            horse['nonstarter_history_rows'] = career['nonstarter_rows']
            changes.append(dict(race_id=race['race_id'], horse_name=horse['horse_name'],
                action='NONRACE_OR_NONSTARTER_REMOVED_FROM_STARTED_HISTORY',
                before=old, after=copy.deepcopy(rebuilt), removed=removed,
                source=detail_source, parser_issues=[i.to_dict() for i in issue_sink]))
        race['horses'] = active
        race['non_starters'] = nonstarters
        race['declared_entry_set_original'] = declared_entry_set
        race['participating_entry_set'] = [dict(horse_id=h['horse_id'], horse_name=h['horse_name'],
                                               horse_number=h.get('horse_number')) for h in active]
        race['official_nonstarter_set'] = [dict(horse_id=h['horse_id'], horse_name=h['horse_name'],
            horse_number=h.get('horse_number'), status=h['entry_status'],
            source=h['participation_provenance']) for h in nonstarters]
        original_ids = [h['horse_id'] for h in declared_entry_set]
        reconstructed_ids = [h['horse_id'] for h in active + nonstarters]
        if len(set(original_ids)) != len(original_ids) or sorted(original_ids) != sorted(reconstructed_ids):
            write_status('BLOCKED_ENTRY_UNIVERSE_MISMATCH')
            raise ValueError('Declared entry universe was not preserved')
        race['fact_reconstruction'] = dict(input_path=str(path.resolve()), input_sha256=before_sha,
            source_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
            source_manifest_snapshots=manifest_snapshots,
            generated_at=datetime.now(timezone.utc).isoformat(),
            note='Original inputs and HTML preserved; factual distinctions only; no imputation approval.')
        (output / path.name).write_text(json.dumps(race, ensure_ascii=False, indent=2) + '\n')
        summary.append(dict(race_id=race['race_id'], starters=len(active), nonstarters=len(nonstarters),
            career_verified=sum(h.get('career_verification', {}).get('status') == 'VERIFIED_COMPLETE_OFFICIAL_HISTORY' for h in active)))
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in input_hashes.items()):
        write_status('BLOCKED_INPUT_CHANGED')
        raise ValueError('Input changed during fact reconstruction')
    if any(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256'] for item in manifest_snapshots):
        write_status('BLOCKED_MANIFEST_CHANGED')
        raise ValueError('Source manifest changed during fact reconstruction')
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in code_hashes.items()):
        write_status('BLOCKED_CODE_CHANGED')
        raise ValueError('Code changed during fact reconstruction')
    (output / 'reconstruction_changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2) + '\n')
    (output / 'reconstruction_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    write_status('COMPLETE')
    print(json.dumps({'output': str(output), 'races': len(summary), 'changes': len(changes)}))


if __name__ == '__main__':
    main()
