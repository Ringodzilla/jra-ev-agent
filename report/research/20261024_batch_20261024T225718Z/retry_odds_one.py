from pathlib import Path
src=(Path(__file__).parent/'enrich_v2_odds.py').read_text()
exec(src[:src.index('ds=[]')])
a=src.index('v2path=');b=src.index('observations={};progress=');exec(src[a:b])
progress=json.loads((OUT/'odds_recovery_summary.json').read_text());observations=json.loads((OUT/'odds_observations.json').read_text())
remaining=[]
for error in progress['errors']:
 u=error['url']
 try:
  rs,rm=getsoup(u);ls=official_odds_links(rs)
  if len(ls)!=1:raise ValueError('official_unique_odds_link_unavailable')
  os,om=getsoup(ls[0]);dn=os.select_one('.cell.date');date=datekey(dn.get_text(' ',strip=True)) if dn else ''
  if not date or date>='2026-10-24':raise ValueError('invalid historical date')
  obs=[]
  for tr in os.select('tr'):
   name=tr.select_one('td.horse');odds=tr.select_one('td.odds_tan');num=tr.select_one('td.num')
   if name and odds and re.fullmatch(r'\d+\.\d+',odds.get_text(' ',strip=True)):obs.append({'horse_name':name.get_text(' ',strip=True),'horse_number':num.get_text(strip=True) if num else '', 'odds':odds.get_text(' ',strip=True),'all_cells':{','.join(td.get('class',[])):td.get_text(' ',strip=True) for td in tr.find_all('td',recursive=False)}})
  page={'date':date,'odds_url':ls[0],'source':om,'result_source':rm,'rows':obs,'page_status':'final_odds' if '最終オッズ' in os.get_text() else 'UNVERIFIED'};observations[u]=page
  recovered=0
  for p in (OUT/'enriched_v2').glob('*.json'):
   d=json.loads(p.read_text());changed=False
   for h in d['horses']:
    for row in h['history_pre_target']:
     if row.get('odds') or row.get('odds_recovery_result_url')!=u:continue
     matches=[x for x in obs if x['horse_name']==h['horse_name']]
     if len(matches)!=1 or (row.get('normalized_date') or datekey(str(row.get('date',''))))!=date:continue
     hit=matches[0];row['odds']=hit['odds'];row.setdefault('field_provenance',{})['odds']={'url':page['odds_url'],'sha256':om['sha256'],'captured_at':om['captured_at'],'match':{'horse_name':hit['horse_name'],'horse_number':hit['horse_number'],'date':date,'match_count':1},'source_kind':'official_historical_final_odds','previous_value':'','page_status':page['page_status']};row['official_odds_row_observations']=hit['all_cells'];recovered+=1;changed=True
   if changed:p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
  progress['odds_pages']+=1;progress['recovered_rows']+=recovered;print('RETRY_SUCCESS',u,recovered)
 except Exception as exc:remaining.append({'url':u,'error':str(exc)})
progress['initial_errors']=progress['errors'];progress['errors']=remaining
(OUT/'odds_recovery_summary.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2));(OUT/'odds_observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2));print(progress)
