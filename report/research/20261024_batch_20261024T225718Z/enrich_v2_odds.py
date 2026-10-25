import json,re,hashlib,datetime,time,requests
from pathlib import Path
from urllib.parse import urljoin,urlsplit,parse_qs
from bs4 import BeautifulSoup
OUT=Path(__file__).parent; (OUT/'enriched_v2').mkdir(exist_ok=True)
manifest=json.loads((OUT/'manifest.json').read_text()); sources={m['url']:m for m in manifest}; session=requests.Session();summary={'target_rows':0,'linked_rows':0,'missing_links':0,'urls':0,'recovered_passing_order':0,'recovered_last_3f':0,'recovered_weather':0,'unmatched':[],'errors':[]}; planned=set()
def soup_source(m):return BeautifulSoup(Path(m['path']).read_bytes().decode(m['encoding'],errors='replace'),'html.parser')
def datekey(t):
 nums=re.findall(r'\d+',t)
 return '-'.join([nums[0],nums[1].zfill(2),nums[2].zfill(2)]) if len(nums)>=3 else ''
def discover(h,row):
 u=row.get('race_result_url','')
 if 'accessS.html' in u and 'CNAME=' in u:return u
 m=sources.get(h.get('horse_url',''))
 if not m:return None
 candidates=[]
 for tr in soup_source(m).select('tr'):
  cells=tr.find_all(['td','th'],recursive=False)
  if not cells:continue
  if row.get('normalized_date')!=datekey(cells[0].get_text(' ',strip=True)):continue
  for a in tr.select('a[href]'):
   href=a['href']
   if 'accessS.html' in href and 'CNAME=' in href:candidates.append(urljoin('https://jra.jp',href))
 return candidates[0] if len(set(candidates))==1 else None

ds=[]
for p in sorted((OUT/'enriched_races').glob('*.json')):
 d=json.loads(p.read_text())
 for h in d['horses']:
  for row in h['history_pre_target']:
   if not row.get('odds'):
    summary['target_rows']+=1;u=discover(h,row);row['odds_recovery_result_url']=u
    if u:planned.add(u);summary['linked_rows']+=1
    else:summary['missing_links']+=1
 ds.append((p.name,d))
summary['urls']=len(planned);(OUT/'odds_recovery_plan.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print('PLAN',summary,flush=True)
# New manifest only; original manifest and enriched_races are immutable.
v2path=OUT/'manifest_v2.json';v2=json.loads(v2path.read_text()) if v2path.exists() else []
for m in v2:sources[m['url']]=m
cache_soup={}
def fetch(u):
 if u in sources:return sources[u]
 time.sleep(.35);parts=urlsplit(u);qs=parse_qs(parts.query);r=session.post('https://jra.jp'+parts.path,data={'cname':qs['CNAME'][0]},timeout=25);r.raise_for_status();r.encoding=r.apparent_encoding
 path=OUT/'raw'/('v2_'+hashlib.sha256(u.encode()).hexdigest()+'.html');path.write_bytes(r.content);m={'url':u,'method':'POST','captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sha256':hashlib.sha256(r.content).hexdigest(),'path':str(path.resolve()),'encoding':r.encoding};v2.append(m);sources[u]=m;v2path.write_text(json.dumps(v2,ensure_ascii=False,indent=2));return m
def getsoup(u):
 m=fetch(u);return soup_source(m),m
def official_odds_links(s):
 vals=[]
 for a in s.select('a'):
  if a.get_text(' ',strip=True)!='オッズ':continue
  for path,cname in re.findall(r"doAction\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",str(a)):
   if 'accessO.html' in path and re.search(r'\d{8}Z/',cname):vals.append('https://jra.jp'+path+'?CNAME='+cname)
 return list(set(vals))
observations={};progress={'total_urls':len(planned),'processed':0,'odds_pages':0,'recovered_rows':0,'unmatched':[],'errors':[],'date_conflicts':[]}
def checkpoint():
 progress['recovered_rows']=0;progress['unmatched']=[];progress['date_conflicts']=[]
 for filename,original in ds:
  d=json.loads(json.dumps(original))
  for h in d['horses']:
   for row in h['history_pre_target']:
    obs=observations.get(row.get('odds_recovery_result_url'))
    if not obs:continue
    date=row.get('normalized_date') or datekey(str(row.get('date','')))
    if date!=obs['date']:progress['date_conflicts'].append({'horse':h['horse_name'],'rowdate':date,'sourcedate':obs['date']});continue
    matches=[x for x in obs['rows'] if x['horse_name']==h['horse_name']]
    if len(matches)!=1:progress['unmatched'].append({'horse':h['horse_name'],'date':date,'matches':len(matches)});continue
    hit=matches[0]
    if not row.get('odds'):
     row['odds']=hit['odds'];row.setdefault('field_provenance',{})['odds']={'url':obs['odds_url'],'sha256':obs['source']['sha256'],'captured_at':obs['source']['captured_at'],'match':{'horse_name':hit['horse_name'],'horse_number':hit['horse_number'],'date':date,'match_count':1},'source_kind':'official_historical_final_odds','previous_value':'','page_status':obs['page_status']};row['official_odds_row_observations']=hit['all_cells'];progress['recovered_rows']+=1
  (OUT/'enriched_v2'/filename).write_text(json.dumps(d,ensure_ascii=False,indent=2))
priority={r.get('odds_recovery_result_url') for fn,d in ds if d['race_id'] in {'20261024_08_08','20261024_08_11','20261024_05_02'} for h in d['horses'] for r in h['history_pre_target']}
for i,u in enumerate(sorted(planned,key=lambda u:(u not in priority,u))):
 try:
  rs,rm=getsoup(u);ls=official_odds_links(rs)
  if len(ls)!=1:raise ValueError('official_unique_odds_link_unavailable')
  os,om=getsoup(ls[0]);date_node=os.select_one('.cell.date');date=datekey(date_node.get_text(' ',strip=True)) if date_node else ''
  if not date or date>='2026-10-24':raise ValueError('odds_date_not_before_target')
  obs=[]
  for tr in os.select('tr'):
   name=tr.select_one('td.horse');odds=tr.select_one('td.odds_tan');num=tr.select_one('td.num')
   if name and odds:
    text=odds.get_text(' ',strip=True)
    if re.fullmatch(r'\d+\.\d+',text):obs.append({'horse_name':name.get_text(' ',strip=True),'horse_number':num.get_text(strip=True) if num else '', 'odds':text,'all_cells':{','.join(td.get('class',[])):td.get_text(' ',strip=True) for td in tr.find_all('td',recursive=False)}})
  observations[u]={'date':date,'odds_url':ls[0],'source':om,'result_source':rm,'rows':obs,'page_status':'final_odds' if '最終オッズ' in os.get_text() else 'UNVERIFIED'};progress['odds_pages']+=1
 except Exception as e:progress['errors'].append({'url':u,'error':str(e)})
 progress['processed']=i+1
 if (i+1)%20==0:
  checkpoint()
  (OUT/'odds_recovery_progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2));print('FETCH',i+1,len(planned),'pages',progress['odds_pages'],'errors',len(progress['errors']),flush=True)
checkpoint()
(OUT/'odds_observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2));(OUT/'odds_recovery_summary.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2));print('DONE',progress,flush=True)
