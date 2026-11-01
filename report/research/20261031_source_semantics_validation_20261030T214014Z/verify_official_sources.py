"""Read-only reproduction of38 average1F and26 straight-course official observations."""
import datetime
import hashlib
import json
import sys
from pathlib import Path
from bs4 import BeautifulSoup
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from jra_scraper.history_observations import parse_result_history_semantics
base=ROOT/'report/research/20261025_batch_20261027T125128Z'
summary=json.loads((base/'final_collection_summary_v2.json').read_text())
checks=[];cache={}
def soup_for(source):
 raw=Path(source['path']).read_bytes()
 assert hashlib.sha256(raw).hexdigest()==source['sha256']
 if source['sha256'] not in cache:
  cache[source['sha256']]=BeautifulSoup(raw.decode(source['encoding']),'html.parser')
 return cache[source['sha256']]
def row_for(soup,name):
 rows=[r for r in soup.select('tr') if r.select_one('td.horse a') and r.select_one('td.horse a').get_text(strip=True)==name]
 assert len(rows)==1
 return rows[0]
for item in summary['jra_average_1f_semantic_repairs']:
 s=soup_for(item['source']);obs=parse_result_history_semantics(s,row_for(s,item['horse_name']))
 ok=obs['average_1f']==item['average_1f'] and obs['last_3f']=='' and obs['last_3f_status']=='NOT_APPLICABLE'
 assert ok
 checks.append({'kind':'average_1f','race_id':item['race_id'],'horse_name':item['horse_name'],'date':item['date'],'source':item['source'],'observation':obs,'pass':ok})
for p in sorted((base/'features_final_v2').glob('*.json')):
 d=json.loads(p.read_text())
 for h in d['horses']+d.get('non_starters',[]):
  for hist in h['history_pre_target']:
   if hist.get('passing_order_missing_reason')!='NOT_APPLICABLE_STRAIGHT_COURSE_NO_CORNER_OBSERVATION':continue
   source=hist['passing_order_collection_evidence']['source'];s=soup_for(source)
   obs=parse_result_history_semantics(s,row_for(s,h['horse_name']))
   ok=obs['passing_order']=='' and obs['passing_order_status']=='NOT_APPLICABLE' and obs['passing_order_missing_reason']=='STRAIGHT_COURSE_NO_CORNERS'
   assert ok
   checks.append({'kind':'straight_course','race_id':d['race_id'],'horse_name':h['horse_name'],'date':hist['date'],'source':source,'observation':obs,'pass':ok})
assert sum(c['kind']=='average_1f' for c in checks)==38
assert sum(c['kind']=='straight_course' for c in checks)==26
result={'role':'data_collector','scope':'read_only_reparse_of_immutable_official_sources','status':'PASS','checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'average_1f_checks':38,'straight_course_checks':26,'failures':0,'source_sha_failures':0,'source_summary':{'path':str((base/'final_collection_summary_v2.json').resolve()),'sha256':hashlib.sha256((base/'final_collection_summary_v2.json').read_bytes()).hexdigest()},'code':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'jra_scraper/history_observations.py',ROOT/'jra_scraper/parser.py',Path(__file__)]},'checks':checks,'prediction_or_model_fit_executed':False}
out=Path(__file__).parent/'validation.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(out)
