import json,re,hashlib,datetime,sys
from pathlib import Path
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path.cwd()))
from jra_scraper.history_pace import parse_official_opening_600m
OUT=Path(__file__).parent;stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');DEST=OUT/('enriched_v4_'+stamp);DEST.mkdir(exist_ok=False)
manifest=json.loads((OUT/'manifest.json').read_text())+json.loads((OUT/'manifest_v2.json').read_text());sources={m['url']:m for m in manifest};cache={};summary={'input':'enriched_v2','output':str(DEST.resolve()),'races':0,'history_rows':0,'recoveries':{},'missing_reasons':{},'contradictions':[],'errors':[],'source_urls':set()}
def day(text):
 m=re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日',text)
 if m:return datetime.date(*map(int,m.groups())).isoformat()
 return text if re.fullmatch(r'\d{4}-\d{2}-\d{2}',text) else ''
def load(u):
 if u in cache:return cache[u]
 m=sources[u];data=Path(m['path']).read_bytes()
 if hashlib.sha256(data).hexdigest()!=m['sha256']:raise ValueError('source bytes SHA mismatch')
 html=data.decode(m['encoding'],errors='replace');s=BeautifulSoup(html,'html.parser');date_nodes=s.select('.cell.date')
 if len(date_nodes)!=1:raise ValueError('source date not unique')
 date=day(date_nodes[0].get_text(' ',strip=True))
 if not date or date>='2026-10-24':raise ValueError('source not before cutoff')
 weather=s.select_one('li.weather .txt');course=s.select_one('.cell.course');course_text=course.get_text(' ',strip=True) if course else ''
 condition=s.select_one('li.turf .txt,li.dirt .txt');pace=parse_official_opening_600m(html)
 obs=[]
 for tr in s.select('tr'):
  name=tr.select_one('td.horse a')
  if not name:continue
  def cell(sel):
   x=tr.select_one(sel);return x.get_text(' ',strip=True) if x else ''
  surface='ダート' if 'ダート' in course_text else ('芝' if '芝' in course_text else ('障害' if '障害' in course_text else ''))
  distance=re.search(r'([0-9,]+)\s*メートル',course_text)
  obs.append({'horse_name':name.get_text(strip=True),'horse_href':name.get('href',''),'date':date,'weather':weather.get_text(strip=True) if weather else '', 'pace':pace['pace'],'passing_order':'-'.join(x.get_text(strip=True) for x in tr.select('td.corner li')),'last_3f':cell('td.f_time'),'position':cell('td.place'),'time':cell('td.time'),'weight':cell('td.weight'),'jockey':cell('td.jockey'),'popularity':cell('td.pop'),'track_condition':condition.get_text(strip=True) if condition else '', 'history_surface':surface, 'official_distance_m':distance.group(1).replace(',','') if distance else '', 'raw_result_cells':{','.join(td.get('class',[])):td.get_text(' ',strip=True) for td in tr.find_all('td',recursive=False)}})
 val={'source':m,'rows':obs,'pace':pace,'date':date};cache[u]=val;return val
for i,p in enumerate(sorted((OUT/'enriched_v2').glob('*.json'))):
 d=json.loads(p.read_text());d['v4_collection']={'mode':'existing_raw_only','created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'input_path':str(p.resolve()),'input_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 for h in d['horses']:
  for row in h['history_pre_target']:
   summary['history_rows']+=1;u=row.get('odds_recovery_result_url') or row.get('recovery_target_url') or row.get('race_result_url');attempts=row.setdefault('field_collection_attempts',[])
   if not u or u not in sources:
    reason='NO_VERIFIED_OFFICIAL_RESULT_LINK' if not u else 'OFFICIAL_RESULT_NOT_IN_EXISTING_RAW';summary['missing_reasons'][reason]=summary['missing_reasons'].get(reason,0)+1
    attempts.append({'fields':['pace','weather','passing_order','last_3f'],'state':'SOURCE_UNVERIFIED','reason':reason,'url':u});continue
   try:
    page=load(u);date=day(str(row.get('date','')))
    if page['date']!=date:raise ValueError('history/source day mismatch')
    matches=[x for x in page['rows'] if x['horse_name']==h['horse_name']]
    if len(matches)!=1:raise ValueError('horse-name match not unique')
    hit=matches[0];src=page['source'];hid=str(h.get('horse_id',''));hn=hit['horse_href'];idmatch=bool(hid and hid in hn)
    prov={'url':u,'path':src['path'],'sha256':src['sha256'],'encoding':src['encoding'],'captured_at':src['captured_at'],'source_kind':'official_past_result_observation','match':{'horse_name':hit['horse_name'],'horse_href':hn,'horse_id':hid,'horse_id_matches':idmatch,'date':date,'match_count':1,'match_method':'registration_id_and_unique_name' if idmatch else 'unique_exact_name_and_date'}}
    summary['source_urls'].add(u);row['official_result_observations_v4']=hit
    for field in ['weather','pace','passing_order','last_3f','position','time','weight','jockey','popularity','track_condition','history_surface']:
     value=hit[field];old=row.get(field);fallback=field=='last_3f' and row.get('last_3f_source')=='fallback';blank=old is None or str(old).strip() in {'','-','―','None','null'}
     evidence={**prov,'previous_value':old,'previous_source':row.get('last_3f_source') if field=='last_3f' else ('missing' if blank else 'existing')}
     if field=='pace':evidence['pace_metadata']=page['pace']
     state='OBSERVED' if value else 'SOURCE_FIELD_MISSING';reason=''
     if field=='pace' and not value:
      reason=page['pace']['pace_missing_reason'];state='NOT_MEASURED' if reason=='NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED' else 'SOURCE_FIELD_MISSING';row.update({k:v for k,v in page['pace'].items() if k!='pace'})
     if value and (blank or fallback):
      row[field]=value;row.setdefault('field_provenance',{})[field]=evidence;summary['recoveries'][field]=summary['recoveries'].get(field,0)+1
      if field=='last_3f':row['last_3f_source']='official_result_recovered_v4'
      if field=='pace':row.update({k:v for k,v in page['pace'].items() if k!='pace'})
     elif value and str(old).strip()!=str(value).strip():
      conflict={'race_id':d['race_id'],'horse_name':h['horse_name'],'date':date,'field':field,'existing_value':old,'observed_value':value,'provenance':evidence};summary['contradictions'].append(conflict);row.setdefault('observation_conflicts_v4',[]).append(conflict)
     attempts.append({'fields':[field],'state':state,'reason':reason,**prov})
     if not value:summary['missing_reasons'][reason or field+'_SOURCE_FIELD_MISSING']=summary['missing_reasons'].get(reason or field+'_SOURCE_FIELD_MISSING',0)+1
   except Exception as exc:
    summary['errors'].append({'race_id':d['race_id'],'horse_name':h['horse_name'],'date':row.get('date'),'url':u,'error':str(exc)});attempts.append({'fields':['pace','weather','passing_order','last_3f'],'state':'UNVERIFIED','reason':str(exc),'url':u})
 (DEST/p.name).write_text(json.dumps(d,ensure_ascii=False,indent=2));summary['races']+=1;print('RACE',i+1,36,d['race_id'],'pace',summary['recoveries'].get('pace',0),'weather',summary['recoveries'].get('weather',0),flush=True)
summary['source_urls']=sorted(summary['source_urls']);(DEST/'collection_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print('DONE',str(DEST),summary['recoveries'],summary['missing_reasons'],'contradictions',len(summary['contradictions']),'errors',len(summary['errors']),flush=True)
