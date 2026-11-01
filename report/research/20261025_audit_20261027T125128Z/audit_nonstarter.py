"""Audit preserved nonstarter histories without adding them to model inputs."""
import ast,hashlib,json,re,sys
from pathlib import Path
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path.cwd()))
from src.data_integrity import audit_collected_data
from jra_scraper.validation import _normalize_row
BASE=Path('report/research/20261025_batch_20261027T125128Z');OUT=Path(__file__).parent;TARGET='2026-10-25'
source_by_url={}
for name in ['manifest.json','history_manifest.json']:
 source_by_url.update({m['url']:m for m in json.loads((BASE/name).read_text())})
code=OUT/'audit_collection.py'
for node in ast.parse(code.read_text()).body:
 if isinstance(node,ast.FunctionDef) and node.name in ['sha','write','artifact','independent_career']:
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(code),'exec'))
path=BASE/'features_final_v2/20261025_08_03.json';f=json.loads(path.read_text());h=f['non_starters'][0];rid=f['race_id'];common=dict(race_id=rid,horse_id=h['horse_id'],horse_name=h['horse_name'],horse_url=h['horse_url'])
rows=[_normalize_row(dict(r,**common,horse_number=h['horse_number'],run_index=str(i),target_race_date=TARGET)) for i,r in enumerate(h['history_pre_target'],1)]
artifacts=[artifact(h['horse_url'],'horse_history',**common),artifact(f['racecard_url'],'race_detail',race_id=rid)]
for r in h['history_pre_target']:
 if r.get('official_result_source'):artifacts.append(artifact(r['official_result_source']['url'],'history_result',**common))
 for o in r.get('field_provenance',{}).get('odds',[]):artifacts.append(artifact(o['url'],'history_odds',**common))
career=independent_career(h);c={'rows':rows,'entries':[dict(h,race_id=rid)],'source_audit':{'artifacts':artifacts}}
audit=audit_collected_data(c,{'race_id':rid,'career_starts_by_horse_number':{'5':career['before_target_count']}},purpose='nonstarter_history_collection_only')
audit.update(role='data_integrity_auditor',horse_name=h['horse_name'],horse_number=h['horse_number'],entry_status='取消',excluded_from_training_entries=True,independent_career=career,input_artifact={'path':str(path),'sha256':sha(path)},script_artifact={'path':str(Path(__file__)),'sha256':sha(__file__)})
write(OUT/'nonstarter_history_audit.json',audit)
print({'horse':h['horse_name'],'number':h['horse_number'],'histories':len(rows),'source':audit['source_audit_status'],'mismatches':len(audit['verification_issues']),'career':career['status']})
