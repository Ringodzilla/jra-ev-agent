import requests,re,time,json,hashlib,datetime,sys,os
from pathlib import Path
from urllib.parse import urlencode,urlsplit,parse_qs,urljoin
from bs4 import BeautifulSoup
from dataclasses import asdict
sys.path.insert(0,str(Path.cwd()))
from jra_scraper.parser import JRAParser
OUT=Path(__file__).parent; (OUT/'raw').mkdir(exist_ok=True); (OUT/'races').mkdir(exist_ok=True)
BASE='https://jra.jp'; session=requests.Session(); manifest=json.loads((OUT/'manifest.json').read_text()) if (OUT/'manifest.json').exists() else []; cache={}; parser=JRAParser(BASE)
for item in manifest:
 if item.get('method')=='POST':cache[item['url']]=Path(item['path']).read_bytes().decode(item['encoding'],errors='replace')
def save(): (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
def fetch(url):
 if url in cache:return cache[url]
 if os.environ.get('BATCH_OFFLINE')=='1':raise ValueError('OFFLINE_SOURCE_UNAVAILABLE '+url)
 time.sleep(.4); parts=urlsplit(url); query=parse_qs(parts.query); r=(session.post(BASE+parts.path,data={'cname':query['CNAME'][0]},timeout=40) if 'CNAME' in query else session.get(url,timeout=40));r.raise_for_status();r.encoding=r.apparent_encoding
 name=hashlib.sha256(url.encode()).hexdigest();path=OUT/'raw'/f'{name}.html';path.write_bytes(r.content)
 item={'url':url,'method':'POST','captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sha256':hashlib.sha256(r.content).hexdigest(),'path':str(path.resolve()),'encoding':r.encoding};manifest.append(item);save();cache[url]=r.text;return r.text
def links(html):
 out=[]
 for a in BeautifulSoup(html,'html.parser').select('a[href]'):
  href=a.get('href','')
  if 'CNAME=' in href:out.append(urljoin(BASE,href))
 for path,cname in re.findall(r"doAction\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",html):
  u=BASE+path+'?'+urlencode({'CNAME':cname},safe='/')
  if u not in out:out.append(u)
 return out
seed=BASE+'/JRADB/accessS.html?CNAME=pw01sde0105202604081120261024/E7'
q=[seed]; seen=set(); results={}; cards={}
while q:
 u=q.pop(0)
 if u in seen:continue
 seen.add(u);h=fetch(u)
 for v in links(h):
  if 'accessS.html' in v and ('20261024' in v or 'pw01sli00' in v):
   if 'pw01sde' in v: results[v]=None
   elif v not in seen:q.append(v)
  if 'accessD.html' in v and '20261024' in v:cards[v]=None
print('DISCOVERED',len(results),'results',len(cards),'cards',flush=True)
(OUT/'discovery.json').write_text(json.dumps({'results':list(results),'cards':list(cards)},indent=2))
# result pages may link only general card index. Discover dated racecard navigation from official card index.
dq=[v for v in links(fetch(seed)) if 'accessD.html' in v];ds=set()
while dq:
 u=dq.pop(0)
 if u in ds:continue
 ds.add(u);h=fetch(u)
 for v in links(h):
  if 'accessD.html' not in v:continue
  if '20261024' in v:
   if 'pw01dde' in v:cards[v]=None
   elif v not in ds:dq.append(v)
  elif 'pw01dli00' in v and v not in ds:dq.append(v)
print('CARDS',len(cards),flush=True)
(OUT/'discovery.json').write_text(json.dumps({'results':list(results),'cards':list(cards)},indent=2))
def code(u):
 m=re.search(r'pw01[sd]de\d{2}(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})20261024',u)
 if not m:return None
 track,year,meeting,day,race=m.groups();return f'20261024_{track}_{race}'
def tables(h):
 s=BeautifulSoup(h,'html.parser');return [{'class':t.get('class',[]),'caption':t.caption.get_text(' ',strip=True) if t.caption else '', 'rows':[[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'],recursive=False)] for tr in t.select('tr')]} for t in s.select('table')]
for ix,u in enumerate(sorted(results)):
 try:
  rid=code(u);h=fetch(u);s=BeautifulSoup(h,'html.parser');cu=next((v for v in cards if code(v)==rid),None)
  record={'race_id':rid,'result_url':u,'racecard_url':cu,'result_tables':tables(h),'result_text':s.get_text(' ',strip=True),'horses':[],'errors':[]}
  if cu:
   ch=fetch(cu);issues=[]; canonical=rid.replace('_04_','_新潟_').replace('_05_','_東京_').replace('_08_','_京都_'); horses=parser.parse_race_detail(ch,canonical,'',target_race_date='2026-10-24',issue_sink=issues)
   record['parser_issues']=[asdict(x) if hasattr(x,'__dataclass_fields__') else str(x) for x in issues]
   for horse in horses:
    d=asdict(horse);hist=d.get('embedded_history',[])
    # Career count must be observed, not inferred from number of rows. Fetch detail for all linked horses.
    if horse.horse_url and len(hist)<5:
     try:
      hh=fetch(horse.horse_url);extra=parser.parse_horse_last5(hh,canonical,horse.horse_id,horse.horse_name,horse.horse_url,before_date='2026-10-24')
      d['horse_detail_url']=horse.horse_url;d['career_source_text']=BeautifulSoup(hh,'html.parser').get_text(' ',strip=True)
      merged={}
      for row in hist+extra:
       date=str(row.get('date','')); nums=re.findall(r'\d+',date)
       dt='-'.join([nums[0],nums[1].zfill(2),nums[2].zfill(2)]) if len(nums)>=3 else date
       if dt and dt<'2026-10-24': 
        key=(dt,)
        row=dict(row,normalized_date=dt)
        if key not in merged:merged[key]=row
        else:
         original=merged[key]
         for field,value in row.items():
          if not original.get(field) and value:original[field]=value
          elif value and original.get(field) and original[field]!=value:record.setdefault('history_conflicts',[]).append({'horse':horse.horse_name,'date':dt,'field':field,'embedded_or_first':original[field],'detail':value})
      hist=list(merged.values());hist.sort(key=lambda x:str(x.get('normalized_date','')),reverse=True)
     except Exception as e:record['errors'].append({'horse':horse.horse_name,'error':str(e)})
    d['history_pre_target']=hist[:5];d['history_count']=len(hist[:5]);d['career_starts']='UNVERIFIED';d['history_missing_fields']=[{'index':i,'fields':[k for k in ['passing_order','pace','weather','odds','last_3f'] if not row.get(k)],'fallback':row.get('last_3f_source')} for i,row in enumerate(hist[:5])]
    record['horses'].append(d)
  else:record['errors'].append('official_racecard_link_not_discovered')
  (OUT/'races'/f'{rid}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));print('RACE',ix+1,len(results),rid,len(record['horses']),flush=True)
 except Exception as e:print('ERROR',u,repr(e),flush=True)
print('DONE',flush=True)
