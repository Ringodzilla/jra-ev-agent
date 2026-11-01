"""Recover observed pre-target history and save target result labels separately."""
import datetime
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path.cwd()))
from jra_scraper.history_pace import parse_official_opening_600m

OUT = Path(__file__).parent
TARGET = '2026-10-25'
for directory in ('features', 'labels', 'history_results'):
    (OUT / directory).mkdir(exist_ok=True)
old = Path('report/research/20261024_batch_20261024T225718Z/repair_official_facts.py')
spec = importlib.util.spec_from_file_location('official_fact_helpers', old)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
manifest = json.loads((OUT / 'manifest.json').read_text())
sources = {m['url']: m for m in manifest}
extra_path = OUT / 'history_manifest.json'
extra = json.loads(extra_path.read_text()) if extra_path.exists() else []
sources.update({m['url']: m for m in extra})
prior_sources = {}
for prior_name in ('manifest.json', 'manifest_v2.json'):
    prior = old.parent / prior_name
    if prior.exists():
        prior_sources.update({m['url']:m for m in json.loads(prior.read_text())})
session = requests.Session()
errors = []

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def source(url):
    if url not in sources and url in prior_sources:
        prior = prior_sources[url]
        raw = Path(prior['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != prior['sha256']:
            raise ValueError('prior cache SHA mismatch')
        meta = {**prior, 'reuse_verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'reuse_reason':'identical_official_url_and_verified_raw_sha256'}
        sources[url] = meta
        extra.append(meta)
        write(extra_path, extra)
    if url in sources:
        meta = sources[url]
        raw = Path(meta['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta['sha256']:
            raise ValueError('raw SHA mismatch')
        return raw.decode(meta['encoding'], errors='replace'), meta
    parts = urlsplit(url)
    cname = parse_qs(parts.query)['CNAME'][0]
    for attempt in range(3):
        try:
            time.sleep(.25)
            response = session.post('https://jra.jp' + parts.path, data={'cname': cname}, timeout=35)
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
    response.encoding = response.apparent_encoding
    raw = response.content
    path = OUT / 'raw' / ('history_' + hashlib.sha256(url.encode()).hexdigest() + '.html')
    path.write_bytes(raw)
    meta = dict(url=url, method='POST', request_parameters={'cname': cname},
                captured_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                sha256=hashlib.sha256(raw).hexdigest(), path=str(path.resolve()), encoding=response.encoding)
    sources[url] = meta
    extra.append(meta)
    write(extra_path, extra)
    write(OUT / 'http_progress.json', {'captured_sources':len(extra),'last_url':url,'at':meta['captured_at']})
    return response.text, meta

def links(html, path):
    return sorted(set('https://jra.jp' + p + '?' + urlencode({'CNAME': c}, safe='/')
        for p, c in re.findall(r"doAction\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", html)
        if path in p))

def date(soup):
    node = soup.select_one('.cell.date')
    return helper.day(node.get_text(' ', strip=True)) if node else ''

def observations(html):
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for tr in soup.select('tr'):
        name = tr.select_one('td.horse')
        if not name:
            continue
        cells = {','.join(td.get('class', [])): td.get_text(' ', strip=True)
                 for td in tr.find_all('td', recursive=False)}
        item = {'horse_name': name.get_text(' ', strip=True), 'raw_cells': cells,
                'horse_href': name.a.get('href', '') if name.a else ''}
        for key, selector in {'horse_number':'td.num', 'position':'td.place', 'last_3f':'td.f_time',
                              'time':'td.time', 'odds':'td.odds_tan', 'weight':'td.weight',
                              'jockey':'td.jockey', 'popularity':'td.pop'}.items():
            node = tr.select_one(selector)
            item[key] = node.get_text(' ', strip=True) if node else ''
        item['passing_order'] = '-'.join(x.get_text(strip=True) for x in tr.select('td.corner li'))
        out.append(item)
    weather = soup.select_one('li.weather .txt')
    condition = soup.select_one('li.turf .txt, li.dirt .txt')
    course = soup.select_one('.cell.course')
    return {'date': date(soup), 'rows': out, 'weather':weather.get_text(strip=True) if weather else '',
            'track_condition':condition.get_text(strip=True) if condition else '',
            'course_text':course.get_text(' ',strip=True) if course else '',
            'pace': parse_official_opening_600m(html)}

cache = {}
def result(url):
    if url not in cache:
        html, meta = source(url)
        obs = observations(html)
        if not obs['date'] or obs['date'] >= TARGET:
            raise ValueError('past result date not before target: ' + obs['date'])
        obs['source'] = meta
        obs['odds_sources'] = []
        for odds_url in links(html, 'accessO.html'):
            if not re.search(r'\d{8}Z/', odds_url):
                continue
            odds_html, odds_meta = source(odds_url)
            odds_obs = observations(odds_html)
            if odds_obs['date'] != obs['date']:
                raise ValueError('past result / odds date mismatch')
            if not any(row['odds'] for row in odds_obs['rows']):
                raise ValueError('no valid past win odds table')
            obs['odds_rows'] = odds_obs['rows']
            obs['odds_sources'].append(odds_meta)
        cache[url] = obs
        write(OUT / 'history_results' / (hashlib.sha256(url.encode()).hexdigest() + '.json'), obs)
    return cache[url]

summary = {'races':0,'declared_horses':0,'history_rows':0,'career_verified':0,
           'missing_fields':{},'errors':errors,'target_date':TARGET,
           'mode':'post_event_official_reconstruction_no_fit_no_prediction',
           'code_sha256':{str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),old,Path('jra_scraper/parser.py'),Path('jra_scraper/history_pace.py'))}}
for path in sorted((OUT / 'races').glob('*.json')):
    race = json.loads(path.read_text())
    rid = race['race_id']
    label = {key:race[key] for key in ('race_id','result_url','result_tables','result_text')}
    label['purpose'] = 'OUTCOME_LABEL_ONLY_EXCLUDED_FROM_PREDICTION_FEATURES'
    html, meta = source(race['result_url'])
    label['observations'] = observations(html)
    label['source'] = meta
    label['odds'] = []
    label['odds_collection'] = 'delegated_to_final_odds_collection'
    write(OUT / 'labels' / path.name,label)
    feature = {key:value for key,value in race.items() if key not in ('result_tables','result_text')}
    feature['result_url'] = {'label_reference':race['result_url'],'excluded_from_feature_inputs':True}
    feature['collection_mode'] = 'post_event_official_reconstruction'
    label['entry_market'] = []
    for horse in feature['horses']:
        detail = ''
        label['entry_market'].append({k:horse.get(k) for k in ('horse_id','horse_name','horse_number','current_odds','current_popularity')})
        for market_field in ('current_odds','current_popularity','career_source_text','embedded_history'):
            horse.pop(market_field, None)
        summary['declared_horses'] += 1
        try:
            detail, detail_source = source(horse['horse_url'])
            career = helper.complete_career(detail,TARGET)
            post_target = [r for r in career.get('started_rows',[]) if r.get('normalized_date','') >= TARGET]
            label.setdefault('career_post_target_rows',[]).append({'horse_id':horse['horse_id'],'rows':post_target})
            career['started_rows'] = [r for r in career.get('started_rows',[]) if r.get('normalized_date','') < TARGET]
            career['nonstarter_rows'] = [r for r in career.get('nonstarter_rows',[]) if r.get('normalized_date','') < TARGET]
            career['unknown_rows'] = [r for r in career.get('unknown_rows',[]) if r.get('normalized_date','') < TARGET]
            horse['career_verification'] = career
            if career['status'] == 'VERIFIED_COMPLETE_OFFICIAL_HISTORY':
                existing = {helper.day(r.get('date')):r for r in horse['history_pre_target']}
                started = sorted(career['started_rows'], key=lambda r:r['normalized_date'], reverse=True)[:5]
                rebuilt = []
                for started_row in started:
                    merged = dict(started_row)
                    merged.update(existing.get(started_row['normalized_date'], {}))
                    merged['normalized_date'] = started_row['normalized_date']
                    rebuilt.append(merged)
                horse['history_pre_target'] = rebuilt
                horse['history_count'] = len(rebuilt)
                horse['history_selection_rule'] = 'most_recent_five_started_races_before_target_from_verified_complete_career'
            horse['career_starts'] = career['career_before_target']
            horse['career_source'] = detail_source
            summary['career_verified'] += career['status'] == 'VERIFIED_COMPLETE_OFFICIAL_HISTORY'
        except Exception as exc:
            errors.append({'scope':'career','horse':horse['horse_name'],'error':str(exc)})
        for row in horse['history_pre_target']:
            summary['history_rows'] += 1
            if row.get('last_3f_source') == 'fallback':
                row.setdefault('discarded_unobserved_parser_values', {})['last_3f'] = row.get('last_3f')
                row['last_3f'] = ''
                row['last_3f_source'] = 'UNOBSERVED_FALLBACK_REMOVED'
            url = row.get('race_result_url')
            if not url and detail:
                candidates = []
                for tr in BeautifulSoup(detail, 'html.parser').select('tr'):
                    first = tr.find(['td','th'])
                    if first and helper.day(first.get_text()) == helper.day(row.get('date')):
                        candidates.extend(urljoin('https://jra.jp',a['href']) for a in tr.select('a[href]') if 'accessS.html?CNAME=' in a['href'])
                if len(set(candidates)) == 1:
                    url = candidates[0]
                    row['race_result_url'] = url
                    row['result_link_recovery_source'] = detail_source
            try:
                if not url:
                    row['collection_gap'] = {'reason':'JRA_HORSE_HISTORY_HAS_NO_RESULT_LINK',
                        'attempted_sources':[horse.get('horse_url'),race.get('racecard_url')],
                        'observed_course':row.get('course'),'date':row.get('date'),
                        'source':detail_source if detail else None}
                    raise ValueError('official history result link missing')
                obs = result(url)
                if obs['date'] != helper.day(row.get('date')):
                    raise ValueError('history/result date mismatch')
                hits = [r for r in obs['rows'] if r['horse_name'] == horse['horse_name']]
                if len(hits) != 1:
                    raise ValueError('horse-name observation not unique')
                hit = hits[0]
                row['official_result_observation'] = hit
                row['official_result_source'] = obs['source']
                row['official_pace_observation'] = obs['pace']
                for key in ('passing_order','last_3f','time','position','weight','jockey','popularity'):
                    if hit[key]:
                        row.setdefault('previous_parser_values',{})[key] = row.get(key)
                        row[key] = hit[key]
                        row.setdefault('field_provenance',{})[key] = obs['source']
                if hit['last_3f']:
                    row['last_3f_source'] = 'official_result_observation'
                for key in ('weather','track_condition'):
                    if obs[key]:
                        row[key] = obs[key]
                        row.setdefault('field_provenance',{})[key] = obs['source']
                row['pace'] = obs['pace']['pace']
                row.update({k:v for k,v in obs['pace'].items() if k!='pace'})
                odds_hits = [r for r in obs.get('odds_rows',[]) if r['horse_name']==horse['horse_name']]
                if len(odds_hits)==1 and odds_hits[0]['odds']:
                    row['odds'] = odds_hits[0]['odds']
                    row.setdefault('field_provenance',{})['odds'] = obs['odds_sources']
                row['field_provenance']['pace'] = obs['source']
            except Exception as exc:
                errors.append({'scope':'history','race_id':rid,'horse':horse['horse_name'],
                               'date':row.get('date'),'url':url,'error':str(exc)})
            for key in ('weather','track_condition','passing_order','last_3f','pace','odds'):
                if not row.get(key):
                    summary['missing_fields'][key] = summary['missing_fields'].get(key,0)+1
    feature['declared_entry_set'] = [{'horse_id':h['horse_id'],'horse_name':h['horse_name'],
                                      'horse_number':h['horse_number']} for h in feature['horses']]
    feature['non_starters'] = []
    active = []
    for horse in feature['horses']:
        matched = [r for r in label['observations']['rows'] if r['horse_name']==horse['horse_name']]
        if len(matched)==1 and any(x in matched[0]['position'] for x in ('取消','除外')):
            horse['entry_status'] = matched[0]['position']
            horse['participation_source'] = label['source']
            feature['non_starters'].append(horse)
        else:
            horse['entry_status'] = 'STARTER' if len(matched)==1 else 'UNVERIFIED'
            active.append(horse)
    feature['horses'] = active
    write(OUT / 'labels' / path.name,label)
    write(OUT / 'features' / path.name,feature)
    summary['races'] += 1
    write(OUT / 'collection_summary.json',summary)
    print('COMPLETE',rid,'races',summary['races'],'histories',summary['history_rows'],'errors',len(errors),flush=True)
summary['unique_history_results']=len(cache)
summary['raw_sources']=len(sources)
write(OUT / 'collection_summary.json',summary)
print('DONE',json.dumps(summary,ensure_ascii=False),flush=True)
