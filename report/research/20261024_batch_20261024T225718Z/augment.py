import json,re
from pathlib import Path
from bs4 import BeautifulSoup
OUT=Path(__file__).parent
manifest=json.loads((OUT/'manifest.json').read_text());sources={m['url']:m for m in manifest};summary=[];labels=[]
for p in sorted((OUT/'races').glob('*.json')):
 d=json.loads(p.read_text());m=sources[d['result_url']];s=BeautifulSoup(Path(m['path']).read_bytes().decode(m['encoding'],errors='replace'),'html.parser')
 result=[]
 for tr in s.select('tr'):
  if tr.select_one('td.place'):
   result.append({td.get('class',['unknown'])[0]:td.get_text(' ',strip=True) for td in tr.select('td')})
 d['official_result_rows']=result;d['result_labels_candidates']=[];d['payouts']=[]
 for dl in s.select('dl'):
  dt=dl.find('dt');dd=dl.find('dd')
  if not dt or not dd:continue
  bt=dt.get_text(' ',strip=True)
  if bt not in ['単勝','複勝','枠連','馬連','ワイド','馬単','3連複','3連単','三連複','三連単']:continue
  for line in dd.select('.line'):
   num=line.select_one('.num');yen=line.select_one('.yen')
   if not num or not yen:continue
   if not re.sub(r'\D','',yen.get_text()):
    d.setdefault('payout_not_numeric',[]).append({'bet_type':bt,'text':line.get_text(' ',strip=True)});continue
   val={'bet_type':bt,'combination':num.get_text('-',strip=True),'payout_yen_per_100':int(re.sub(r'\D','',yen.get_text()))}
   d['payouts'].append(val)
   if bt=='単勝':d['result_labels_candidates'].append({'race_id':d['race_id'],'式別':'単勝','馬番':num.get_text('-',strip=True),'払戻金':str(val['payout_yen_per_100'])})
 cm=sources.get(d['racecard_url']); career={}
 if cm:
  cs=BeautifulSoup(Path(cm['path']).read_bytes().decode(cm['encoding'],errors='replace'),'html.parser')
  for tr in cs.select('tr'):
   n=tr.select_one('td.num');text=tr.get_text(' ',strip=True);mt=re.search(r'\((\d+)\.(\d+)\.(\d+)\.(\d+)\)',text)
   if n and mt:career[n.get_text(strip=True)]=sum(map(int,mt.groups()))
 for h in d['horses']:
  rows={}
  for row in h['history_pre_target']:
   nums=re.findall(r'\d+',str(row.get('date','')))
   date='-'.join([nums[0],nums[1].zfill(2),nums[2].zfill(2)]) if len(nums)>=3 else ''
   if date and date<'2026-10-24':rows.setdefault(date,dict(row,normalized_date=date))
  h['history_pre_target']=sorted(rows.values(),key=lambda x:x['normalized_date'],reverse=True)[:5];h['history_count']=len(h['history_pre_target']);h['career_starts']=career.get(str(h['horse_number']),'UNVERIFIED')
  h['required_history_count']=min(5,h['career_starts']) if isinstance(h['career_starts'],int) else None
  h['history_count_state']='UNVERIFIED' if h['required_history_count'] is None else ('COMPLETE' if h['history_count']>=h['required_history_count'] else 'MISSING')
 d['entry_result_count_check']={'entries':len(d['horses']),'result_rows':len(result),'match':len(d['horses'])==len(result)}
 p.write_text(json.dumps(d,ensure_ascii=False,indent=2));labels.extend(d['result_labels_candidates']);summary.append({'race_id':d['race_id'],'horses':len(d['horses']),'results':len(result),'win_payouts':len(d['result_labels_candidates']),'errors':d['errors']})
(OUT/'collection_summary.json').write_text(json.dumps({'races':len(summary),'horses':sum(x['horses'] for x in summary),'details':summary},ensure_ascii=False,indent=2));(OUT/'result_labels_candidates.json').write_text(json.dumps(labels,ensure_ascii=False,indent=2));print(len(summary),sum(x['horses'] for x in summary))
