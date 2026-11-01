"""Merge independent read-only audit evidence without changing collected inputs."""
import json,hashlib,datetime
from pathlib import Path
from collections import Counter
OUT=Path(__file__).parent;BASE=Path('report/research/20261025_batch_20261027T125128Z')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
parts=[OUT/f'summary_part{i}.json' for i in range(4)]
summaries=[json.loads(p.read_text()) for p in parts];races=[r for s in summaries for r in s['races']]
corrected_path=OUT/'corrected_race_summary.json';corrected=json.loads(corrected_path.read_text())['races'][0]
races=[corrected if r['race_id']==corrected['race_id'] else r for r in races];races.sort(key=lambda r:r['race_id'])
assert len(races)==36 and len({r['race_id'] for r in races})==36
aliases=[]
for r in races:
 audit=json.loads((OUT/(r['race_id']+'.json')).read_text());prior=audit['input_artifacts'][0];canonical=BASE/'features_final_v2'/(r['race_id']+'.json')
 aliases.append({'race_id':r['race_id'],'audited_path':prior['path'],'audited_sha256':prior['sha256'],'canonical_path':str(canonical),'canonical_sha256':sha(canonical),'sha256_equal':prior['sha256']==sha(canonical)})
assert all(a['sha256_equal'] for a in aliases)
nonstarter_path=OUT/'nonstarter_history_audit.json';non=json.loads(nonstarter_path.read_text())
collection_path=BASE/'final_collection_summary_v2.json';collection=json.loads(collection_path.read_text())
raw_checks=json.loads((OUT/'raw_source_checks.json').read_text());counts=Counter()
for r in races:counts.update(r['field_missing_counts'])
source_class=Counter()
for r in races:source_class.update(r['source_issue_classification_counts'])
result={'role':'data_integrity_auditor','evaluated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'status':'COLLECTION_AUDITED_WITH_MODEL_INPUT_LIMITATIONS','gate':'BLOCKED_FOR_MODEL_CALCULATION',
 'audit_scope':'read_only_post_event_observation_collection_no_fit_no_prediction',
 'expected_races':36,'audited_races':36,'declared_horses':sum(r['horse_count'] for r in races)+1,'starters':sum(r['horse_count'] for r in races),'non_starters':1,
 'starter_history_rows':sum(r['history_rows'] for r in races),'nonstarter_history_rows':sum(h['collected'] for h in non['history_counts']),
 'all_history_rows':sum(r['history_rows'] for r in races)+sum(h['collected'] for h in non['history_counts']),
 'history_shortfalls':sum(r['history_shortfalls'] for r in races),'history_career_unverified_horses':sum(r['history_career_unverified_horses'] for r in races),
 'history_latest5_date_set_mismatches':sum(r['history_date_set_mismatches'] for r in races),
 'all_race_declared_sets_match':all(r['horse_set_same'] for r in races),'all_race_starter_sets_match':all(r['starter_set_same'] for r in races),
 'unresolved_source_mismatches':sum(r['unresolved_source_mismatch_count'] for r in races),
 'existing_parser_source_issue_classifications':dict(source_class),
 'raw_source_checks':len(raw_checks),'raw_sha_failures':sum(c['status']!='PASS' for c in raw_checks),
 'nar_observations_independently_checked':sum(r['nar_source_checks_count'] for r in races),'nar_source_check_failures':sum(r['nar_source_check_failures'] for r in races),
 'parser_substitution_rows':sum(r['parser_substitution_rows'] for r in races),'actual_learning_substitutions_adopted':0,
 'feature_container_leakage_risks':sum(r['leakage_container_risks'] for r in races),
 'starter_normalized_field_missingness':dict(counts),'all_declared_history_missingness':collection['missing_fields'],'all_declared_missing_field_reasons':collection.get('missing_field_reasons'),
 'approval_checks':{'imputation_authorized':False,'new_calculations_executed':False,'approved_imputations_used':[]},
 'environment_and_final_odds_evidence':summaries[0]['external_collection_evidence'],
 'canonical_input_equivalence':aliases,
 'input_artifacts':[{'path':str(p),'sha256':sha(p)} for p in parts+[corrected_path,nonstarter_path,collection_path,OUT/'environment_sources.json',OUT/'nar_sources_independent.json']],
 'races':races,
 'limitations':['Existing prediction audit requires current market fields deliberately excluded from post-event feature inputs; its gates remain blocked.',
 'Existing source parser does not consume NAR observations or recognize the jump average-1F semantic correction. Separate source checks preserve these distinctions without overriding machine gates.',
 'Original-source reparse shares production parsing logic and does not independently prove all parser completeness.',
 'Post-event reconstruction does not prove pre-race capture. No fit, probabilities, EV, tickets, model promotion, or shared learning-state mutation performed.'],
 'next_action':'Use canonical v2 observations with explicit missingness for future research preparation; do not execute new model calculations with unapproved substitution.'}
assert result['all_history_rows']==collection['history_rows']
(OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['audited_races','declared_horses','starters','non_starters','all_history_rows','history_shortfalls','history_career_unverified_horses','history_latest5_date_set_mismatches','all_race_declared_sets_match','all_race_starter_sets_match','raw_sha_failures','unresolved_source_mismatches','nar_observations_independently_checked','nar_source_check_failures','parser_substitution_rows','feature_container_leakage_risks','all_declared_history_missingness']},ensure_ascii=False))
