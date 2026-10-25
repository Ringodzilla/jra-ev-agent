#!/usr/bin/env python3
"""Fit one preregistered candidate from the three authorized audited races."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from bs4 import BeautifulSoup
BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
sys.path.insert(0,str(ROOT))
from src.data_integrity import data_input_sha256
from analysis.environment_model import fit_model, evaluate_model


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {'path':str(path.resolve()),'sha256':sha(path)}


def verified_reference(reference):
    path=Path(reference['path'])
    if sha(path)!=reference['sha256']:
        raise ValueError('Referenced artifact hash mismatch: '+str(path))
    return json.loads(path.read_text())


def main():
    shared=[ROOT/'data/processed/result_labels.csv',ROOT/'report/environment_model/learning_status.json',
            ROOT/'data/models/environment_model.json']
    before={str(p):sha(p) if p.exists() else None for p in shared}
    simulator=BASE/'simulator_baseline_20261024T233746836103Z'
    summary_path=simulator/'summary.json'
    expected='4613b1cc315f50f806e126d0673782d56454cd8e1bff2761268cc940f1f0d90c'
    if sha(summary_path)!=expected:
        raise ValueError('Authorized simulator summary changed')
    summary=json.loads(summary_path.read_text())
    independent_path=simulator/'independent_verification.json'
    independent=json.loads(independent_path.read_text())
    if independent['status']!='PASS' or independent['baseline_summary_sha256']!=expected:
        raise ValueError('Independent verification missing or not bound')
    verified_reference(summary['analyzer_summary'])
    labels_path=BASE/'verified_labels_20261024T231632925424Z.json'
    labels=json.loads(labels_path.read_text())
    races=[];references=[ref(summary_path),ref(independent_path),ref(labels_path)]
    evidence=[]
    for baseline_ref in summary['artifacts']:
        baseline=verified_reference(baseline_ref)
        score,normalized,audit=[verified_reference(r) for r in baseline['input_artifacts']]
        if baseline['status']!='PASS' or audit['gate']!='PASS' or audit['model_ready'] is not True:
            raise ValueError('Non-PASS stage')
        bound=data_input_sha256(normalized,normalized['race_configs'])
        if not (bound==audit['collected_audit']['input_sha256']==score['input_sha256']==baseline['input_sha256']):
            raise ValueError('Input digest chain mismatch')
        ca=audit['collected_audit']
        if ca['gate']!='PASS' or ca['findings'] or ca['verification_issues'] or ca['unapproved_finding_count']!=0:
            raise ValueError('Input audit contains unresolved issue')
        sets=[{str(r['horse_number']) for r in rows} for rows in
              [normalized['entries'],score['feature_rows'],baseline['rows']]]
        if any(s!=sets[0] for s in sets) or any(len(rows)!=len(sets[0]) for rows in
             [normalized['entries'],score['feature_rows'],baseline['rows']]):
            raise ValueError('Horse universe mismatch')
        rid=baseline['race_id'];numeric=score['numeric_race_id']
        if rid not in {'20261024_東京_02','20261024_京都_08','20261024_京都_11'}:
            raise ValueError('Unauthorized training race')
        checks=[x for x in independent['checks'] if x['race_id']==rid]
        if len(checks)!=1 or checks[0]['independent_direct_exp_check']!='PASS' or checks[0]['horse_count']!=len(sets[0]):
            raise ValueError('Independent probability check incomplete')
        for reference in baseline['code_artifacts']+score['code_artifacts']:
            if sha(Path(reference['path']))!=reference['sha256']:
                raise ValueError('Stage code changed')
        source_path=BASE/'enriched_races'/f'{numeric}.json'
        source_refs=[s for s in labels['sources'] if Path(s['path'])==source_path]
        if len(source_refs)!=1:
            raise ValueError('Label source binding absent')
        observed=verified_reference(source_refs[0])
        metadata=audit['metadata_source'];raw_path=Path(metadata['path'])
        if sha(raw_path)!=metadata['sha256'] or source_refs[0]['official_url']!=metadata['url'] or observed['result_url']!=metadata['url']:
            raise ValueError('Official result source binding invalid')
        soup=BeautifulSoup(raw_path.read_bytes().decode(metadata['encoding']),'html.parser')
        winners={tr.select_one('td.num').get_text(strip=True) for tr in soup.select('tr')
                 if tr.select_one('td.place') and tr.select_one('td.place').get_text(strip=True)=='1'}
        payouts={}
        for dl in soup.select('dl'):
            dt=dl.find('dt');dd=dl.find('dd')
            if not dt or not dd or dt.get_text(strip=True)!='単勝':continue
            for line in dd.select('.line'):
                n=line.select_one('.num');y=line.select_one('.yen')
                if n and y:payouts[n.get_text(strip=True)]=float(re.sub(r'\D','',y.get_text()))
        selected=[x for x in labels['rows'] if x['race_id']==rid and x['式別']=='単勝']
        if not winners or winners!=set(payouts) or {x['馬番']:float(x['払戻金']) for x in selected}!=payouts or not winners.issubset(sets[0]):
            raise ValueError('Winner label/payout/raw universe mismatch')
        env_path=Path(audit['environment_source_path']);env=json.loads(env_path.read_text())
        if env['target_date']!='2026-10-24' or env['pre_race_capture'] is not False:
            raise ValueError('Retrospective environment provenance invalid')
        target=normalized['entries'][0];track=target['target_track'];post=datetime.fromisoformat(audit['post_time'])
        for raw_source in env['sources'].values():
            if sha(Path(raw_source['raw_path']))!=raw_source['sha256']:
                raise ValueError('Environmental original hash mismatch')
        cushion=audit['environment']['cushion'];moisture=audit['environment']['moisture']
        if cushion not in env['tracks'][track]['cushion_history'] or moisture not in env['tracks'][track]['moisture_history']:
            raise ValueError('Measurement does not match track history')
        for observation in (cushion,moisture):
            stamp=datetime.fromisoformat(observation['measured_at'])
            if stamp.date().isoformat()!='2026-10-24' or stamp>=post:
                raise ValueError('Measurement not same-day before race')
        environment={'cushion':cushion['cushion'],'turf_moisture_goal':moisture['turf_moisture_goal'],
                     'turf_moisture_corner4':moisture['turf_moisture_corner4'],
                     'weather':target['target_weather'],'track_condition':target['target_track_condition']}
        races.append({'race_id':rid,'date':'2026-10-24','rows':baseline['rows'],
                      'environment':environment,'winners':sorted(winners),'win_payouts':payouts})
        references.extend([baseline_ref,ref(source_path),ref(raw_path),ref(env_path)])
        evidence.append({'race_id':rid,'input_sha256':bound,'horse_count':len(sets[0]),
                         'measurement_before_post':'PASS','official_label_binding':'PASS',
                         'track':track,'post_time':audit['post_time'],'retrieved_at':env['retrieved_at']})
    if len(races)!=3 or sum(len(r['rows']) for r in races)!=31:
        raise ValueError('Authorized batch size mismatch')
    candidate=fit_model(races,family='linear',ridge=100.0)
    baseline_metrics=evaluate_model(races)
    candidate_metrics=evaluate_model(races,candidate)
    after={str(p):sha(p) if p.exists() else None for p in shared}
    if before!=after:
        raise ValueError('Shared artifact unexpectedly changed')
    output=BASE/('research_fit_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(exist_ok=False)
    for name,obj in [('dataset.json',races),('candidate_model.json',candidate)]:
        (output/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    report={'record_type':'isolated_interim_environment_candidate','role':'analyzer',
            'status':'INSUFFICIENT_DATA_RESEARCH_ONLY','training_races':3,'training_horses':31,
            'family':'linear','ridge':100.0,'training_date_max':'2026-10-24',
            'validation_dates_reserved':['2026-10-25'],'validation_status':'NOT_EXECUTED',
            'test_status':'NOT_EXECUTED_LATER_UNUSED_RACE_DAY_REQUIRED',
            'train_metrics_in_sample_only':{'baseline':baseline_metrics,'candidate':candidate_metrics},
            'baseline_variant':'exact_structural_softmax_temperature1_no_mc_no_market',
            'minimum_required':{'train':100,'validation':20,'test':20},
            'source_artifacts':references,'checks':evidence,
            'code_artifacts':[ref(Path(__file__).resolve()),ref(ROOT/'analysis/environment_model.py')],
            'candidate_artifact':ref(output/'candidate_model.json'),'dataset_artifact':ref(output/'dataset.json'),
            'pre_race_capture':False,'production_learning_eligible':False,
            'production_model_changed':False,'shared_before':before,'shared_after':after,
            'defaults_applied':[],'hyperparameter_search_executed':False,
            'limitations':['3 races are an interim subset; all Saturday turf races total at most 18, below minimum training size 100.',
                           'Training log loss/Brier/ROI are in-sample and do not establish generalization.',
                           'Posthoc environment reconstruction and result-header weather do not prove pre-race availability.',
                           'Sunday validation and later independent test have not been executed.',
                           'Candidate cannot be promoted or used for production predictions.',
                           'Exact Softmax baseline differs from legacy Monte Carlo baseline; comparisons across variants are invalid.']}
    (output/'training_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(output),'status':report['status'],'train_metrics_in_sample_only':report['train_metrics_in_sample_only'],
                      'unsupported_environment_terms':candidate['unsupported_environment_terms']}))


if __name__=='__main__':main()
