"""Source-bound Sunday research input preparation and current audit, never scores."""
from __future__ import annotations
import argparse
import copy
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from bs4 import BeautifulSoup
ROOT=Path.cwd()
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'skills/jra-environment-research/scripts'))
from jra_scraper.validation import _normalize_row
from src.data_integrity import audit_collected_data,data_input_sha256
from structural_features import HISTORY_FIELDS,TARGET_FIELDS
BASE=ROOT/'report/research/20261025_batch_20261027T125128Z'
OLD=ROOT/'report/research/20261025_audit_20261027T125128Z'
ENV=ROOT/'report/research/20261025_environment_20261027T125148Z/environment_collection.json'
OUT=Path(__file__).resolve().parent
PURPOSE='environment_structural_research'
TARGET='2026-10-25'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path):return {'path':str(Path(path).resolve()),'sha256':sha(path)}
def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False)
def missing(x):return x is None or str(x).strip() in {'','None','null','-','―','UNVERIFIED'}
def structural_contract(rows,entries):
    problems=[]
    for entry in entries:
        own=[r for r in rows if r['horse_id']==entry['horse_id']]
        if not 1<=len(own)<=5:problems.append({'horse_number':entry['horse_number'],'field':'history_count','value':len(own)})
        for key in TARGET_FIELDS:
            if missing(entry.get(key)):problems.append({'horse_number':entry['horse_number'],'field':key,'scope':'target'})
        for key in ('target_distance','assigned_weight','frame_number'):
            try:good=math.isfinite(float(entry[key])) and float(entry[key])>0
            except (ValueError,TypeError,KeyError):good=False
            if not good:problems.append({'horse_number':entry['horse_number'],'field':key,'scope':'positive_target'})
        for r in own:
            identity={'horse_number':entry['horse_number'],'run_index':r['run_index']}
            for key in HISTORY_FIELDS:
                if missing(r.get(key)):problems.append({**identity,'field':key,'scope':'history'})
            for key in ('position','time','distance','last_3f','weight','odds','popularity'):
                try:good=math.isfinite(float(r[key])) and float(r[key])>0
                except (ValueError,TypeError,KeyError):good=False
                if not good:problems.append({**identity,'field':key,'scope':'positive_history'})
            if not re.fullmatch(r'\d+(?:-\d+)*',str(r.get('passing_order',''))):problems.append({**identity,'field':'passing_order','scope':'syntax'})
            if r.get('history_surface') not in {'芝','ダート'}:problems.append({**identity,'field':'history_surface','scope':'comparability'})
            if r.get('track_condition') not in {'良','稍重','重','不良'}:problems.append({**identity,'field':'track_condition','scope':'condition'})
            if str(r.get('date',''))>=TARGET:problems.append({**identity,'field':'date','scope':'nonpast'})
            if r.get('race_id')!=entry['race_id'] or r.get('horse_id')!=entry['horse_id']:problems.append({**identity,'field':'identity','scope':'identity'})
    return problems

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    manifest_paths=[BASE/'manifest.json',BASE/'history_manifest.json',ROOT/'report/research/20261025_nar_recovery_20261027T130621Z/manifest.json']
    manifests=[json.loads(p.read_text()) for p in manifest_paths]
    source_by_url={m['url']:m for data in manifests for m in data}
    frozen_sources={m['path']:m for m in source_by_url.values()}
    raw_checks=[]
    for meta in frozen_sources.values():
        actual=sha(meta['path']);raw_checks.append({'path':meta['path'],'sha256':actual,'matches':actual==meta['sha256']})
    if not all(x['matches'] for x in raw_checks):raise ValueError('Original source changed')
    save(OUT/'source_byte_checks.json',raw_checks)
    environment=json.loads(ENV.read_text())
    catalog=json.loads((BASE/'final_artifact_catalog_v2.json').read_text())
    if any(sha(x['path'])!=x['sha256'] for x in catalog['artifacts']):raise ValueError('Frozen canonical catalog changed')
    def artifact(url,kind,**kw):
        meta=source_by_url[url];raw=Path(meta['path']).read_bytes();html=raw.decode(meta['encoding'])
        return dict(kind=kind,path=meta['path'],sha256=hashlib.sha256(html.encode()).hexdigest(),original_source=meta,
                    raw_reference={'path':meta['path'],'encoding':meta['encoding'],'bytes_sha256':meta['sha256'],'html_utf8_sha256':hashlib.sha256(html.encode()).hexdigest()},**kw)
    summaries=[]
    for fp in sorted((BASE/'features_final_v2').glob('*.json')):
        feature=json.loads(fp.read_text());rid=feature['race_id'];prior=json.loads((OLD/fp.name).read_text())
        label_path=BASE/'labels_final_v2'/fp.name;label=json.loads(label_path.read_text());metadata=label['source']
        if sha(metadata['path'])!=metadata['sha256']:raise ValueError('Result source changed')
        soup=BeautifulSoup(Path(metadata['path']).read_bytes().decode(metadata['encoding']),'html.parser')
        weather=soup.select_one('li.weather .txt');weather=weather.get_text(strip=True) if weather else ''
        conditions=[(x.get('class',[]),x.get_text(' ',strip=True)) for x in soup.select('li.turf,li.dirt,li.durt')]
        condition_node=soup.select_one('li.turf .txt,li.dirt .txt,li.durt .txt')
        condition=condition_node.get_text(strip=True) if condition_node else ''
        if feature['horses'][0]['target_surface']=='障害':condition=label['observations']['track_condition']
        if weather!=label['observations']['weather'] or condition!=label['observations']['track_condition']:raise ValueError('Official result observation mismatch')
        time_node=soup.select_one('.cell.time');m=re.search(r'(\d+)時(\d+)分',time_node.get_text(' ',strip=True) if time_node else '')
        if not m:raise ValueError('Post time unverified')
        post=f'{TARGET}T{int(m[1]):02d}:{int(m[2]):02d}:00+09:00'
        rows=[];entries=[];artifacts=[];careers={}
        independent={str(c['horse_number']):c for c in prior['career_source_checks']}
        for horse in feature['horses']:
            entry={k:v for k,v in horse.items() if k not in ['history_pre_target','embedded_history','career_verification','career_source_text']}
            entry.update(race_id=rid,target_weather=weather,target_track_condition=condition,target_conditions_captured_at=metadata['captured_at'])
            entry['target_condition_provenance']={'type':'post_event_official_reconstruction','source':metadata,'pre_race_capture_proven':False}
            entries.append(entry)
            proof=independent[str(horse['horse_number'])]
            if proof['status']!='PASS':raise ValueError('Career verification unconfirmed')
            careers[str(horse['horse_number'])]=proof['before_target_count']
            common=dict(race_id=rid,horse_id=horse['horse_id'],horse_name=horse['horse_name'],horse_url=horse['horse_url'])
            if horse['horse_url'] in source_by_url:artifacts.append(artifact(horse['horse_url'],'horse_history',**common))
            for index,h in enumerate(horse['history_pre_target'],1):
                r=_normalize_row(dict(h,**common,horse_number=horse['horse_number'],run_index=str(index),target_race_date=TARGET))
                raw_day=str(r['date']);match=re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日',raw_day)
                if match:r['date']=datetime.date(*map(int,match.groups())).isoformat()
                else:datetime.date.fromisoformat(raw_day)
                if h.get('normalized_date') and r['date']!=h['normalized_date']:raise ValueError('Deterministic history date conversion mismatch')
                rows.append(r)
                meta=h.get('official_result_source',{})
                if meta.get('url') in source_by_url:artifacts.append(artifact(meta['url'],'history_result',**common))
                odds=h.get('field_provenance',{}).get('odds',[]);odds=[odds] if isinstance(odds,dict) else odds
                for o in odds:
                    if o.get('url') in source_by_url:artifacts.append(artifact(o['url'],'history_odds',**common))
        if feature['racecard_url'] in source_by_url:artifacts.append(artifact(feature['racecard_url'],'race_detail',race_id=rid))
        collected={'rows':rows,'entries':entries,'source_audit':{'artifacts':artifacts}}
        config={'race_id':rid,'race_date':TARGET,'track':entries[0]['target_track'],'surface':entries[0]['target_surface'],'distance':entries[0]['target_distance'],'post_time':post[11:16],'source_url':feature['racecard_url'],'career_starts_by_horse_number':careers}
        normalized={'rows':copy.deepcopy(rows),'entries':copy.deepcopy(entries),'race_configs':[config]}
        wrapper={'schema_version':1,'record_type':'collected_raw_references','collected':collected}
        collected_path=OUT/(rid+'_collected.json');normalized_path=OUT/(rid+'_normalized.json')
        save(collected_path,wrapper);save(normalized_path,normalized)
        if args.prepare_only:continue
        hydrated=copy.deepcopy(collected)
        for a in hydrated['source_audit']['artifacts']:
            r=a.pop('raw_reference');raw=Path(r['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=r['bytes_sha256']:raise ValueError('Hydration byte mismatch')
            a['html']=raw.decode(r['encoding'])
            if hashlib.sha256(a['html'].encode()).hexdigest()!=r['html_utf8_sha256']:raise ValueError('Hydration text mismatch')
        ca=audit_collected_data(hydrated,[config],approvals=[],purpose=PURPOSE)
        if ca['input_sha256']!=data_input_sha256(normalized,[config]):raise ValueError('Normalized/collected input mismatch')
        contract_errors=structural_contract(rows,entries)
        env=next(t for t in environment['tracks'] if t['track']==entries[0]['target_track'])
        selected={kind:max([o for o in env[kind+'_history'] if o['measured_at'].startswith(TARGET)],key=lambda x:x['measured_at']) for kind in ['cushion','moisture']}
        env_ok=all(datetime.datetime.fromisoformat(o['measured_at'])<datetime.datetime.fromisoformat(post) for o in selected.values())
        issues=[]
        if not prior['horse_set_check']['same_set'] or not prior['horse_set_check']['starter_set_same']:issues.append('UNVERIFIED_HORSE_SET')
        if any(x['status']!='PASS' for x in prior['career_source_checks']+prior['history_date_set_checks']):issues.append('UNVERIFIED_CAREER_OR_HISTORY_SET')
        after_or_on_target=[{k:r.get(k) for k in ['horse_number','date']} for r in rows if datetime.date.fromisoformat(r['date'])>=datetime.date.fromisoformat(TARGET)]
        container_risks=[{'horse_number':e['horse_number'],'field':k} for e in entries for k in ['current_odds','current_popularity','win_odds','career_source_text','embedded_history'] if not missing(e.get(k))]
        if after_or_on_target or container_risks:issues.append('RESULT_LEAKAGE_RISK')
        gate=ca['gate']
        if gate=='PASS' and contract_errors:gate='BLOCKED_STRUCTURAL_CONTRACT'
        if gate=='PASS' and entries[0]['target_surface']!='芝':gate='NOT_SUPPORTED_SURFACE'
        if gate=='PASS' and not env_ok:gate='UNVERIFIED_ENVIRONMENT'
        if gate=='PASS' and issues:gate='UNVERIFIED'
        model_ready=gate=='PASS'
        payload={'race_id':rid,'numeric_race_id':rid,'gate':gate,'model_ready':model_ready,'record_type':'retrospective_reconstructed','production_learning_eligible':False,'metadata_source':metadata,'post_time':post,'environment':selected,'environment_source_path':str(ENV),'collected_artifact':ref(collected_path),'normalized_artifact':ref(normalized_path),'source_issues':issues,'blocking_history_conflicts':[],'history_conflicts':feature.get('history_conflicts',[]),'history_conflict_resolution':'Canonical selected observations reverified by current core source audit; old parser alternatives retained in immutable feature artifact, not adopted','collected_audit':ca,'structural_contract_errors':contract_errors,'input_artifacts':[ref(fp),ref(label_path),ref(OLD/fp.name)],'source_audit_current_purpose':PURPOSE,'environment_gate':'PASS' if env_ok else 'UNVERIFIED_ENVIRONMENT','surface_gate':'PASS' if entries[0]['target_surface']=='芝' else 'NOT_SUPPORTED_SURFACE','all_horses_retained':True,'current_leakage_checks':{'history_after_or_on_target':after_or_on_target,'container_risks':container_risks,'date_comparison':'datetime.date on deterministically converted ISO dates; prior legacy Japanese/ISO lexical comparison not reused'},'official_nonstarters':feature['non_starters'],'model_calculations_executed':False}
        ap=OUT/(rid+'_audit.json');save(ap,payload);summaries.append({'race_id':rid,'numeric_race_id':rid,'gate':gate,'model_ready':model_ready,'horses':len(entries),'histories':len(rows),'findings':len(ca['findings']),'source_mismatches':len(ca['verification_issues']),'surface_gate':payload['surface_gate'],'environment_gate':payload['environment_gate'],'audit_artifact':ref(ap),'collected_artifact':ref(collected_path),'normalized_artifact':ref(normalized_path)})
        print(rid,gate,len(ca['findings']),flush=True)
    if not args.prepare_only:
        snapshot={'code_files':{str(ROOT/p):sha(ROOT/p) for p in ['src/data_integrity.py','src/acquisition_coverage.py','jra_scraper/parser.py','jra_scraper/validation.py','skills/jra-environment-research/scripts/structural_features.py']},'adapter':ref(Path(__file__))}
        save(OUT/'audit_code_snapshot.json',snapshot)
        result={'role':'data_integrity_auditor','purpose':PURPOSE,'race_count':len(summaries),'batch_complete':len(summaries)==36,'races':summaries,'source_manifests':[ref(p) for p in manifest_paths],'audit_code_snapshot':ref(OUT/'audit_code_snapshot.json'),'model_ready_race_ids':[r['race_id'] for r in summaries if r['model_ready']],'approvals_used':[],'model_calculations_executed':False,'raw_checks':ref(OUT/'source_byte_checks.json')}
        save(OUT/'summary.json',result);print('DONE',len(result['model_ready_race_ids']),flush=True)
if __name__=='__main__':main()
