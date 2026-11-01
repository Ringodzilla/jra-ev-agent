"""Read-only observed-data audit; never run analyzer, simulator, EV or fit."""
import datetime, hashlib, json, re, sys
from collections import Counter
from pathlib import Path
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path.cwd()))
from src.data_integrity import audit_collected_data
from jra_scraper.validation import _normalize_row
BASE=Path('report/research/20261025_batch_20261027T125128Z'); OUT=Path(__file__).parent
TARGET='2026-10-25'
PART=None
SUMMARY=OUT/'corrected_race_summary.json'
FEATURES=BASE/'features_final_v2'
LABELS=BASE/'labels_final_v2'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
manifests=[]
for name in ['manifest.json','history_manifest.json']:
 if (BASE/name).exists():manifests+=json.loads((BASE/name).read_text())
nar_manifest=Path('report/research/20261025_nar_recovery_20261027T130621Z/manifest.json')
if nar_manifest.exists():manifests+=json.loads(nar_manifest.read_text())
source_by_url={m['url']:m for m in manifests}; raw_checks=[]
for m in manifests:
 p=Path(m['path']);actual=sha(p) if p.exists() else None
 raw_checks.append({'path':str(p),'url':m['url'],'expected_sha256':m['sha256'],'actual_sha256':actual,'status':'PASS' if actual==m['sha256'] else 'FAIL'})
write(OUT/(f'raw_source_checks_part{PART}.json' if PART is not None else 'raw_source_checks.json'),raw_checks)
def artifact(url,kind,**kw):
 m=source_by_url[url];html=Path(m['path']).read_bytes().decode(m['encoding'],errors='replace')
 return dict(path=m['path'],sha256=hashlib.sha256(html.encode()).hexdigest(),html=html,kind=kind,**kw)
def independent_career(horse):
 try:
  meta=source_by_url[horse['horse_url']]
  soup=BeautifulSoup(Path(meta['path']).read_bytes().decode(meta['encoding'],errors='replace'),'html.parser')
  totals={};started=[];unknown=[];nonstarters=[]
  for table in soup.select('table'):
   caption=table.caption.get_text(strip=True) if table.caption else ''
   if caption in ['平地レース合計','障害レース合計']:
    rr=[r for r in table.select('tr') if r.select('td')]
    if len(rr)==1:
     cells=[c.get_text(' ',strip=True).replace(',','') for c in rr[0].find_all('td',recursive=False)]
     if len(cells)>=5 and all(c.isdigit() for c in cells[:5]) and sum(map(int,cells[:4]))==int(cells[4]):totals[caption]=int(cells[4])
   if caption!='出走レース':continue
   for row in table.select('tr'):
    cells=[c.get_text(' ',strip=True) for c in row.find_all('td',recursive=False)]
    if not cells or row.select_one('.caution.no_data'):continue
    if any(re.fullmatch(r'JRA(?:へ|より)転[入出]',c) for c in cells):continue
    if len(cells)!=14:unknown.append(cells);continue
    match=re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日',cells[0])
    status=cells[7]
    if any(x in status for x in ['取消','除外']):nonstarters.append(cells);continue
    if not match or not (status.isdigit() or any(x in status for x in ['中止','失格'])):unknown.append(cells);continue
    dt='-'.join([match[1],match[2].zfill(2),match[3].zfill(2)])
    started.append({'date':dt,'course':cells[1],'race_name':cells[2],'status':status})
  complete=len(totals)==2 and not unknown and sum(totals.values())==len(started)
  before=[r for r in started if r['date']<TARGET]
  return {'status':'PASS' if complete else 'UNVERIFIED','before_target_count':len(before) if complete else None,'official_totals':totals,'listed_started_count':len(started),'unknown_row_count':len(unknown),'nonstarter_count':len(nonstarters),'latest5_dates':sorted([r['date'] for r in before],reverse=True)[:5]}
 except (KeyError,ValueError,OSError) as exc:return {'status':'UNVERIFIED','error':str(exc),'before_target_count':None}
def nar_audit(item):
 source=item.get('source',{});result={'horse_name':item.get('horse_name'),'date':item.get('date'),'status':'UNVERIFIED','source':source}
 if not source:return result
 if sha(source['path'])!=source['sha256']:result['reason']='RAW_SHA_MISMATCH';return result
 soup=BeautifulSoup(Path(source['path']).read_bytes().decode(source['encoding'],errors='replace'),'html.parser')
 rows=[tr for tr in soup.select('tr') if tr.select_one('td.horseName') and tr.select_one('td.horseName').get_text(strip=True)==item['horse_name']]
 if len(rows)!=1:result['reason']='HORSE_ROW_NOT_UNIQUE';return result
 row=rows[0];cells=[td.get_text(' ',strip=True) for td in row.find_all('td',recursive=False)]
 if len(cells) not in [16,17] or (len(cells)==17 and not row.select_one('td.q')):result['reason']='UNVERIFIED_TABLE_SCHEMA';return result
 indices={'position':0,'horse_number':2,'weight':6,'jockey':7,'time':10,'last_3f':12,'passing_order':13,'popularity':14,'odds':15}
 compared=[]
 for field,idx in indices.items():
  actual=cells[idx];expected=str(item.get('fields',{}).get(field,''));equal=re.sub(r'\s+','',actual)==re.sub(r'\s+','',expected) or (actual in ['','-','―'] and expected in ['','-','―'])
  compared.append({'field':field,'raw_value':actual,'collected_value':expected,'status':'PASS' if equal else 'FAIL'})
 text=soup.get_text(' ',strip=True);match=re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日',text)
 dt='-'.join([match[1],match[2].zfill(2),match[3].zfill(2)]) if match else None
 for field,pattern in [('weather',r'天候[：:]([^\s]+)'),('track_condition',r'馬場[：:]([^\s]+)')]:
  m=re.search(pattern,text);actual=m[1] if m else '';expected=item.get('fields',{}).get(field,'')
  compared.append({'field':field,'raw_value':actual,'collected_value':expected,'status':'PASS' if actual==expected else 'FAIL'})
 result.update(status='PASS' if all(c['status']=='PASS' for c in compared) and dt==item['date'] else 'FAIL',field_checks=compared,raw_date=dt,pace='NOT_ESTIMATED_EXACT_600M_NOT_COLLECTED')
 return result
old_summary=json.loads(SUMMARY.read_text()) if SUMMARY.exists() else {}
previous={r['race_id']:r for r in old_summary.get('races',[])}
script_sha=sha(__file__)
results=[]
for file_index,fp in enumerate(sorted(FEATURES.glob('*.json'))):
 if fp.stem!='20261025_08_03':continue
 f=json.loads(fp.read_text());rid=f['race_id']
 prior_path=OUT/(rid+'.json')
 if prior_path.exists() and rid in previous:
  prior=json.loads(prior_path.read_text())
  if prior.get('audit_script_sha256')==script_sha and prior.get('input_artifacts',[{}])[0].get('sha256')==sha(fp):
   results.append(previous[rid]);continue
 rows=[];entries=[];artifacts=[];careers={};leaks=[];expected=[];history_set_checks=[];nar_checks=[]
 for horse in f['horses']:
  entry={k:v for k,v in horse.items() if k not in ['history_pre_target','embedded_history','career_verification','career_source_text']}
  entry['race_id']=rid;entries.append(entry)
  cv=horse.get('career_verification',{});independent=independent_career(horse);n=independent['before_target_count']
  horse['independent_career_audit']=independent
  if independent['status']=='PASS' and isinstance(n,int):careers[str(horse['horse_number'])]=n
  for key in ['current_odds','current_popularity','career_source_text','embedded_history']:
   if horse.get(key):leaks.append({'horse_number':horse['horse_number'],'field':key,'state':'POST_EVENT_OR_MIXED_SOURCE_PRESENT_IN_FEATURE_CONTAINER'})
  common=dict(race_id=rid,horse_id=horse['horse_id'],horse_name=horse['horse_name'],horse_url=horse['horse_url'])
  if horse.get('horse_url') in source_by_url:artifacts.append(artifact(horse['horse_url'],'horse_history',**common))
  for i,h in enumerate(horse['history_pre_target'],1):
   if h.get('nar_official_observation'):nar_checks.append(nar_audit(h['nar_official_observation']))
   r=dict(h,**common,horse_number=horse['horse_number'],run_index=str(i),target_race_date=TARGET)
   r=_normalize_row(r);rows.append(r)
   s=h.get('official_result_source',{})
   if s.get('url') in source_by_url:artifacts.append(artifact(s['url'],'history_result',**common))
   os=h.get('field_provenance',{}).get('odds',[])
   if isinstance(os,dict):os=[os]
   for o in os:
    if o.get('url') in source_by_url:artifacts.append(artifact(o['url'],'history_odds',**common))
  if independent['status']=='PASS':
   expected_dates=independent['latest5_dates']
   actual_dates=[r.get('normalized_date') or _normalize_row(r).get('date') for r in horse['history_pre_target']]
   history_set_checks.append({'horse_number':horse['horse_number'],'expected_dates':expected_dates,'actual_dates':actual_dates,'status':'PASS' if expected_dates==actual_dates else 'FAIL'})
  if n is not None:expected.append({'horse_number':horse['horse_number'],'required':min(5,n),'collected':len(horse['history_pre_target'])})
 if f.get('racecard_url') in source_by_url:artifacts.append(artifact(f['racecard_url'],'race_detail',race_id=rid))
 collected={'rows':rows,'entries':entries,'source_audit':{'artifacts':artifacts}}
 config={'race_id':rid,'career_starts_by_horse_number':careers}
 audit=audit_collected_data(collected,config,approvals=[],purpose='research_preparation')
 classifications=[]
 for issue in audit['verification_issues']:
  ir=issue.get('row',{});horse=next((h for h in f['horses'] if str(h['horse_number'])==str(ir.get('horse_number'))),{})
  idx=int(ir.get('run_index') or 0)-1;hist=horse.get('history_pre_target',[]);original=hist[idx] if 0<=idx<len(hist) else {}
  state='UNRESOLVED_SOURCE_MISMATCH'
  if original.get('nar_official_observation'):state='NAR_OBSERVATION_REQUIRES_SEPARATE_NAR_CHECK'
  elif issue.get('field')=='last_3f' and original.get('last_3f_source')=='NOT_OBSERVED_RESULT_COLUMN_IS_AVERAGE_1F':state='AVERAGE_1F_COLUMN_REJECTED_AS_LAST3F'
  elif issue.get('field')=='last_3f' and not original.get('last_3f') and issue.get('source_value') and all(str(v) in ['36','36.0',''] for v in issue['source_value']):state='PARSER_DEFAULT_36_REMOVED_NO_SUBSTITUTION_ADOPTED'
  classifications.append({'horse_number':ir.get('horse_number'),'run_index':ir.get('run_index'),'field':issue.get('field'),'state':state})
 audit['source_issue_classification']=classifications
 # Entry market fields intentionally absent after segregation are not missing raw history.
 audit['entry_model_requirements_scope']='Current market requirements belong to prediction; this collection is post-event research preparation, no model executed.'
 label_path=LABELS/fp.name;label=json.loads(label_path.read_text()) if label_path.exists() else {}
 obs=label.get('observations',{}).get('rows',[])
 declared={str(h['horse_number']):h['horse_name'] for h in f.get('declared_entry_set',f['horses'])}
 observed={str(h['horse_number']):h['horse_name'] for h in obs}
 nonstarters=[h for h in obs if any(s in h.get('position','') for s in ['取消','除外'])]
 observed_starters={str(h['horse_number']):h['horse_name'] for h in obs if h not in nonstarters}
 feature_starters={str(h['horse_number']):h['horse_name'] for h in f['horses']}
 starter_set_verified=observed_starters==feature_starters
 after=[{k:r.get(k) for k in ['horse_number','date']} for r in rows if str(r.get('date',''))>=TARGET]
 environment_path=Path('report/research/20261025_environment_20261027T125148Z/environment_collection.json')
 environment=json.loads(environment_path.read_text());track=entries[0].get('target_track') if entries else None
 env=next((t for t in environment['tracks'] if t['track']==track and t['date']==TARGET),None)
 pairing={'track':track,'environment_artifact':str(environment_path),'environment_sha256':sha(environment_path),
 'weather':label.get('observations',{}).get('weather'),'track_condition':label.get('observations',{}).get('track_condition'),
 'race_condition_provenance':'post_event_official_result','pre_race_capture_proven':False,
 'same_day_cushion_observations':env.get('cushion_same_day_observations',[]) if env else [],
 'same_day_moisture_observations':env.get('moisture_same_day_observations',[]) if env else [],
 'environment_score':'NOT_CALCULATED','model_fit':'NOT_EXECUTED','status':'MATCHED_POST_EVENT_RECONSTRUCTION' if env else 'UNVERIFIED'}
 field_missing=Counter(audit['missing_field_counts']); audit.update(race_id=rid,audit_script_sha256=script_sha,input_artifacts=[{'path':str(fp),'sha256':sha(fp)}],
  nar_source_checks=nar_checks,environment_pairing=pairing,field_missingness=audit['missing_fields'],imputations={'adopted_for_learning':[],'potential_if_model_executed':audit['findings']},
  approval_checks=[{'status':'NOT_APPROVED','scope':'No imputation approved; no model executed'}],
  horse_set_check={'declared':declared,'result':observed,'same_set':declared==observed,'starter_set_same':starter_set_verified,'nonstarters':nonstarters},
  leakage_check={'history_after_or_on_target':after,'container_risks':leaks,'labels_separate':label_path.exists(),
   'training_execution':'NOT_EXECUTED','gate':'BLOCKED' if after or leaks else 'PASS_DATE_AND_CONTAINER_CHECK_ONLY'},
  history_count_expected=expected,history_date_set_checks=history_set_checks,career_source_checks=[{'horse_number':h['horse_number'],**h['independent_career_audit']} for h in f['horses']], next_action='Resolve concrete source/missingness findings before any calculation')
 write(OUT/(rid+'.json'),audit)
 results.append({'race_id':rid,'gate':audit['gate'],'horse_count':len(entries),'history_rows':len(rows),
 'history_counts_verified':audit['history_counts_verified'],'history_required':sum(x['required'] for x in expected),
 'history_shortfalls':sum(max(0,x['required']-x['collected']) for x in expected),
 'history_career_unverified_horses':len(entries)-len(careers),
 'history_date_set_mismatches':sum(x['status']!='PASS' for x in history_set_checks),
 'nar_source_checks_count':len(nar_checks),'nar_source_check_failures':sum(c['status']!='PASS' for c in nar_checks),
 'parser_substitution_rows':sum(r.get('last_3f_source')=='fallback' for r in rows),
 'historical_missing_fields':audit['missing_fields'],
 'acquisition_coverage_counts':audit['acquisition_coverage']['counts'],
 'field_missing_counts':dict(field_missing),'source_audit_status':audit['source_audit_status'],
 'source_mismatch_count':len(audit['verification_issues']),
 'unresolved_source_mismatch_count':sum(x['state']=='UNRESOLVED_SOURCE_MISMATCH' for x in classifications),
 'source_issue_classification_counts':dict(Counter(x['state'] for x in classifications)),'potential_imputation_count':len(audit['findings']),
 'leakage_container_risks':len(leaks),'horse_set_same':declared==observed,'starter_set_same':starter_set_verified,'nonstarters':len(nonstarters)})
summary={'role':'data_integrity_auditor','evaluated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'scope':'post_event_collection_read_only_no_fit_no_prediction','expected_races':36,'audited_races':len(results),
 'raw_source_count':len(raw_checks),'raw_sha_failures':sum(c['status']!='PASS' for c in raw_checks),'races':results,
 'horse_total':sum(r['horse_count'] for r in results),'history_total':sum(r['history_rows'] for r in results),
 'history_shortfalls_total':sum(r['history_shortfalls'] for r in results),
 'historical_field_missing_counts':dict(sum((Counter(r['field_missing_counts']) for r in results),Counter())),
 'actual_learning_substitutions_adopted':0,'model_calculations_executed':False,
 'gate':'UNVERIFIED' if len(results)!=36 else 'REQUIRES_PER_RACE_GATE_REVIEW',
 'limitations':['No original input modified. No model run. Prediction gate findings do not mean substitutions were adopted.',
 'Source reparse uses existing production parser; independent parser completeness is not proved.',
 'Eight-bet-type full combination counts and environment pairing are separate checks, not claimed complete here.'],
 'skill_artifacts':[{'path':p,'sha256':sha(p)} for p in ['skills/jra-03-integrity-audit/SKILL.md','skills/jra-data-integrity-audit/SKILL.md','src/data_integrity.py','src/acquisition_coverage.py']]}
external=[]
for path in [Path('report/research/20261025_environment_20261027T125148Z/environment_collection.json'),Path('report/research/20261025_final_odds_20261027T125420Z/coverage_audit.json')]:
 if path.exists():
  d=json.loads(path.read_text());record={'path':str(path),'sha256':sha(path)}
  if 'coverage_audit' in path.name:
   record.update({k:d.get(k) for k in ['total_races','numeric_odds_total','non_numeric_total','all_races_complete','imputation_count']})
   checks=[{'path':r['artifact_path'],'status':'PASS' if sha(r['artifact_path'])==r['sha256'] else 'FAIL'} for r in d.get('races',[])]
   record['race_artifact_sha_checks']=checks
  else:
   record['tracks']=[{'track':t['track'],'date':t['date'],'same_day_cushion_count':len(t.get('cushion_same_day_observations',[])),'same_day_moisture_count':len(t.get('moisture_same_day_observations',[])),'missing_fields':t.get('missing_fields'),'pre_race_capture_proven':t.get('pre_race_capture_proven')} for t in d.get('tracks',[])]
  external.append(record)
summary['external_collection_evidence']=external
write(SUMMARY,summary)
print(json.dumps({k:v for k,v in summary.items() if k not in ['races','skill_artifacts']},ensure_ascii=False))
