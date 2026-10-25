import json,re,hashlib,datetime,time,requests
from pathlib import Path
from urllib.parse import urljoin,urlsplit,parse_qs
from bs4 import BeautifulSoup
OUT=Path(__file__).parent; (OUT/'enriched_races').mkdir(exist_ok=True)
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
for p in sorted((OUT/'races').glob('*.json')):
 d=json.loads(p.read_text())
 for h in d['horses']:
  for row in h['history_pre_target']:
   if not row.get('passing_order') or row.get('last_3f_source')=='fallback':
    summary['target_rows']+=1;u=discover(h,row);row['recovery_target_url']=u
    if u:planned.add(u);summary['linked_rows']+=1
    else:summary['missing_links']+=1
 ds.append((p.name,d))
summary['urls']=len(planned);(OUT/'enrichment_plan.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print('PLAN',summary,flush=True)
def fetch(u):
 if u in sources:return sources[u]
 time.sleep(.45);parts=urlsplit(u);qs=parse_qs(parts.query);r=session.post('https://jra.jp'+parts.path,data={'cname':qs['CNAME'][0]},timeout=35);r.raise_for_status();r.encoding=r.apparent_encoding
 path=OUT/'raw'/('past_result_'+hashlib.sha256(u.encode()).hexdigest()+'.html');path.write_bytes(r.content);m={'url':u,'method':'POST','captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sha256':hashlib.sha256(r.content).hexdigest(),'path':str(path.resolve()),'encoding':r.encoding};manifest.append(m);sources[u]=m;(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));return m
parsed={}
for i,u in enumerate(sorted(planned)):
 try:
  m=fetch(u);s=soup_source(m);entries=[]
  for tr in s.select('tr'):
   horse=tr.select_one('td.horse')
   if not horse:continue
   name=horse.select_one('a') or horse
   corner=tr.select_one('td.corner');f=tr.select_one('td.f_time')
   passing='-'.join(x.get_text(strip=True) for x in corner.select('li')) if corner and corner.select('li') else ('-'.join(re.findall(r'\d+',corner.get_text(' ',strip=True))) if corner else '')
   entries.append({'name':name.get_text(' ',strip=True),'href':name.get('href',''),'passing_order':passing,'last_3f':f.get_text(' ',strip=True) if f else ''})
  weather='';text=s.get_text(' ',strip=True);match=re.search(r'天候\s*[:：]?\s*(晴|曇|小雨|雨|小雪|雪)',text)
  if match:weather=match.group(1)
  parsed[u]={'entries':entries,'weather':weather,'source':m}
 except Exception as e:summary['errors'].append({'url':u,'error':str(e)})
 if (i+1)%20==0:print('FETCHED',i+1,len(planned),flush=True)
for filename,d in ds:
 for h in d['horses']:
  for row in h['history_pre_target']:
   u=row.get('recovery_target_url');page=parsed.get(u)
   if not page:continue
   matches=[e for e in page['entries'] if e['name']==h['horse_name'] or (h.get('horse_id') and h['horse_id'] in e['href'])]
   if len(matches)!=1:summary['unmatched'].append({'race_id':d['race_id'],'horse_name':h['horse_name'],'date':row.get('date'),'url':u,'matches':len(matches)});continue
   hit=matches[0];prov={'url':u,'sha256':page['source']['sha256'],'captured_at':page['source']['captured_at'],'match':{'horse_name':hit['name'],'horse_href':hit['href'],'match_count':1}}
   for field in ('passing_order','last_3f','weather'):
    eligible=not row.get(field) or (field=='last_3f' and row.get('last_3f_source')=='fallback')
    value=page['weather'] if field=='weather' else hit[field]
    if eligible and value and (field!='last_3f' or re.fullmatch(r'\d+\.\d+',value)):
     row.setdefault('field_provenance',{})[field]={**prov,'previous_value':row.get(field),'previous_source':row.get('last_3f_source') if field=='last_3f' else 'missing','source_kind':'official_past_result_observation'};row[field]=value;summary['recovered_'+field]+=1
     if field=='last_3f':row['last_3f_source']='official_result_recovered'
  h['history_missing_fields']=[{'index':i,'fields':[k for k in ['passing_order','pace','weather','odds','last_3f'] if not row.get(k)],'fallback':row.get('last_3f_source')} for i,row in enumerate(h['history_pre_target'])]
 (OUT/'enriched_races'/filename).write_text(json.dumps(d,ensure_ascii=False,indent=2))
(OUT/'enrichment_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print('DONE',summary,flush=True)
