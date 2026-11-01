"""Read-only replay of saved diagnostics and source-bound pace observations.

No probability/fit/production model is executed. Saved run validity is checked
as historical hash-bound evidence, not re-certified against newly edited code.
"""
import datetime,hashlib,importlib.util,json,math,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
from analysis.history_pace_features import observed_history_pace_context
from src.feature_engineering import summarize_history_rows,build_feature_row
BASE=Path(__file__).resolve().parent
TRAIN=ROOT/'report/research/20261025_training_20261028T040209Z'
RUN=TRAIN/'skill_runs/training_01'
CANON=ROOT/'report/research/20261025_batch_20261027T125128Z'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}
def read(r):
 p=Path(r['path'])
 if sha(p)!=r['sha256']:raise ValueError('historical artifact SHA changed: '+str(p))
 return json.loads(p.read_text())
def dump(name,d):(BASE/name).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
manifest=json.loads((RUN/'manifest.json').read_text());previous=None;prior={};record_refs=[]
for fp in sorted(RUN.glob('[0-9][0-9]_record.json')):
 rec=json.loads(fp.read_text())
 if rec['status']!='PASS' or rec['run_id']!=manifest['run_id'] or rec['previous_record']!=previous or rec['manifest']!=ref(RUN/'manifest.json'):raise ValueError('historical record chain changed')
 prior[rec['stage']]=read(rec['output']);previous=ref(fp);record_refs.append(previous)
if len(prior)!=10:raise ValueError('historical complete run required')
for r in manifest['inputs']:read(r)
review=read(ref(TRAIN/'review/independent_diagnostics.json'))
if review['input_fit']!=ref(RUN/'08_output.json'):raise ValueError('diagnostics fit binding changed')
byrace=defaultdict(list)
for row in prior['probability']['rows']:byrace[row['race_id']].append(row)
loss=[];uniform=[];entropies=[];ratios=[];top1_hits=0;race_diagnostics=[]
for race in prior['fit']['dataset']:
 rows=byrace[race['race_id']];numbers=[str(r['horse_number']) for r in rows];p=[float(r['win_prob']) for r in rows]
 if numbers!=[str(r['horse_number']) for r in race['rows']] or abs(sum(p)-1)>1e-12:raise ValueError('saved probabilities/field differ')
 winners=set(race['winners']);observed=-sum(math.log(p[numbers.index(n)]) for n in winners)/len(winners)
 entropy=-sum(q*math.log(q) for q in p)/math.log(len(p));ratio=max(p)/min(p)
 first=min(range(len(p)),key=lambda i:(-p[i],int(numbers[i])));hit=numbers[first] in winners
 loss.append(observed);uniform.append(math.log(len(p)));entropies.append(entropy);ratios.append(ratio);top1_hits+=hit
 race_diagnostics.append({'race_id':race['race_id'],'field_size':len(p),'normalized_entropy':entropy,'probability_max_min_ratio':ratio,'top1_hit':hit})
mean=lambda x:sum(x)/len(x)
if abs(mean(loss)-prior['fit']['metrics']['baseline']['log_loss'])>1e-12 or abs(mean(uniform)-review['uniform_reference_log_loss'])>1e-12:raise ValueError('saved loss does not reproduce')
flatness={'role':'analyzer','scope':'saved-output diagnostic replay only','historical_run_id':manifest['run_id'],'historical_records':record_refs,'input_probabilities':ref(RUN/'07_output.json'),'input_fit':ref(RUN/'08_output.json'),'current_code_recertifies_old_run':False,'new_predictions_or_fit_executed':False,'baseline_variant':prior['probability']['variant'],'production_MC_evaluated':False,'baseline_log_loss':mean(loss),'uniform_log_loss':mean(uniform),'mean_normalized_entropy':mean(entropies),'probability_ratio_range':[min(ratios),max(ratios)],'top1_hits':top1_hits,'races':race_diagnostics,'interpretation':'This saved strict research baseline is nearly uniform on these ten training races. No conclusion about the separate production MC or other dates follows.','next_experiment':'Compare feature contribution and probability calibration on chronologically separate audited race days; do not tune score multipliers to these winners.'}
dump('baseline_diagnostics.json',flatness)
# Baseline reconstruction covers only files observed clean before this task.
snapshot=json.loads((BASE/'baseline_snapshot.json').read_text())
for item in snapshot['files']:
 if sha(item['snapshot'])!=item['sha256']:raise ValueError('baseline snapshot changed')
 if item['path']!='src/feature_engineering.py' and sha(ROOT/item['path'])!=item['sha256']:raise ValueError('non-target baseline dependency changed')
spec=importlib.util.spec_from_file_location('artificial_prechange_feature',BASE/'baseline/src/feature_engineering.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
checks=[]
for surface in ['芝','ダート']:
 for distance in [1000,1200,1600,2000]:
  for count in [1,2,3,4,5]:
   rows=[{'race_id':'artificial','horse_id':'h','run_index':str(i),'date':f'2026-09-{20-i:02}','position':str(i),'last_3f':str(33+i/10),'popularity':str(i+1),'weight':str(53+i),'distance':str(distance),'time':str(distance/16+i),'passing_order':f'{i}-{i+1}','odds':str(i*2),'course':'東京','history_surface':surface,'target_surface':surface,'track_condition':'良','jockey':'人工騎手','last_3f_status':'OBSERVED','passing_order_status':'OBSERVED'} for i in range(1,count+1)]
   target={'race_id':'artificial','horse_id':'h','horse_name':'人工馬','horse_number':'1','frame_number':'1','target_race_date':'2026-10-25','target_track':'東京','target_surface':surface,'target_distance':str(distance),'target_track_condition':'良','assigned_weight':'55','current_jockey':'人工騎手','current_odds':'4','current_popularity':'2'}
   a=old.summarize_history_rows(rows,group_keys=('race_id','horse_id'));b=summarize_history_rows(rows,group_keys=('race_id','horse_id'))
   same=a==b and old.build_feature_row(target,a[('artificial','h')])==build_feature_row(target,b[('artificial','h')])
   if not same:raise ValueError('non-target feature changed')
   checks.append({'surface':surface,'distance':distance,'history_count':count,'exact_summary_and_feature_match':same})
dump('unaffected_baseline_replay.json',{'scope':'artificial inputs only; pre-change clean worktree snapshot vs new status guards','source_snapshot':ref(BASE/'baseline_snapshot.json'),'checks':checks,'all_exact':all(c['exact_summary_and_feature_match'] for c in checks),'new_real_data_predictions_executed':False})
# Reuse only immutable canonical facts corresponding to the old selected set.
catalog=json.loads((CANON/'final_artifact_catalog_v2.json').read_text());catalog_index={str(Path(x['path']).resolve()):x for x in catalog['artifacts']}
connected=[];source_refs=[]
for selected in prior['integrity_audit']['selected']:
 n=read(selected['normalized']);rid=selected['race_id'];file=CANON/'features_final_v2'/f'{rid}.json';f=read(catalog_index[str(file.resolve())]);source_refs.append(ref(file))
 wanted={e['horse_id'] for e in n['entries']};horses={h['horse_id']:h for h in f['horses']}
 if set(horses)!=wanted:raise ValueError('whole selected starter field mismatch')
 for entry in n['entries']:
  h=horses[entry['horse_id']]
  if len(h['history_pre_target'])!=len([r for r in n['rows'] if r['horse_id']==entry['horse_id']]):raise ValueError('history count mismatch')
  for row in h['history_pre_target']:connected.append(dict(row,race_id=rid))
result=observed_history_pace_context(connected,target_race_date='2026-10-25')
if len(connected)!=371 or result['complete_context_count']!=363:raise ValueError('declared connection coverage changed')
result.update(role='analyzer',canonical_sources=source_refs,code_sources=[ref(ROOT/'analysis/history_pace_features.py'),ref(ROOT/'jra_scraper/history_pace.py'),ref(ROOT/'jra_scraper/history_observations.py')],baseline_score_changed=False,fit_executed=False,validation_executed=False,test_executed=False,condition_routing='Facts retain original surface/course/distance; no learned condition-specific score is routed.',promotion_status='NOT_EVALUATED_NO_PRODUCTION_SCORE_OR_VALIDATED_COEFFICIENT',next_consumer='Research analyzer/simulator candidate input; compare to unchanged common baseline on later independently audited dates.')
dump('history_pace_connection.json',result)
dump('summary.json',{'status':'FACT_CONNECTION_AND_SAVED_DIAGNOSTICS_COMPLETE','role':'analyzer','created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'artifacts':{name:ref(BASE/name) for name in ['baseline_diagnostics.json','unaffected_baseline_replay.json','history_pace_connection.json','baseline_snapshot.json']},'baseline_new_probability_or_fit_executed':False,'production_score_changed':False,'pace_histories_retained':result['retained_history_count'],'pace_position_complete_contexts':result['complete_context_count'],'non_target_artificial_cases_unchanged':len(checks),'model_promotion':'NOT_EVALUATED','limitations':['Saved in-sample research diagnostics are not production MC evaluation.','Raw pace-position products have seconds*ordinal units; no cross-distance comparability or predictive contribution has been established.','No incomplete history is dropped or reweighted; no aggregate model score is defined.']})
print(json.dumps({'output':str(BASE),'pace_histories':result['retained_history_count'],'connected':result['complete_context_count'],'non_target_cases':len(checks),'log_loss':mean(loss),'uniform':mean(uniform),'mean_entropy':mean(entropies)}))
