"""Role-separated pure research producers and independent numerical checks."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
import math
from pathlib import Path
import re
import sys
import numpy as np
from bs4 import BeautifulSoup
from research_gate import Blocked,read,ref,identities,sha,canonical
from structural_features import structural_features,COMPONENT_WEIGHTS
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def close(a,b,tolerance=1e-11):
    if isinstance(a,dict) and isinstance(b,dict):
        return set(a)==set(b) and all(close(a[k],b[k],tolerance) for k in a)
    if isinstance(a,list) and isinstance(b,list):
        return len(a)==len(b) and all(close(x,y,tolerance) for x,y in zip(a,b))
    if type(a) in (int,float) and type(b) in (int,float):
        return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a)-float(b))<=tolerance
    return type(a) is type(b) and a==b


def structural(prior):
    rows=[]
    for selected in prior['integrity_audit']['selected']:
        data=read(selected['normalized']);groups=defaultdict(list)
        for row in data['rows']:groups[str(row['horse_number'])].append(row)
        if set(groups)!={str(e['horse_number']) for e in data['entries']}:
            raise Blocked('Full horse set missing')
        for entry in data['entries']:
            f=structural_features(groups[str(entry['horse_number'])],entry)
            f['S_base']=math.fsum(COMPONENT_WEIGHTS[k]*f[k] for k in COMPONENT_WEIGHTS)
            f.update(environment_score=0.0,environment_adjustment_applied=False,
                     environment_model_status='not_applied_research_baseline',
                     environment_not_applied_reason='NO_PROMOTED_ENVIRONMENT_MODEL',
                     condition_model_difference_applied=False,
                     condition_model_unapplied_reason='NO_VALIDATED_CONDITION_DIFFERENCE',
                     historical_pace_usage='OBSERVED_RETAINED_NOT_USED_BY_COMMON_BASE')
            rows.append(f)
    identities(rows)
    return rows


def scenarios(prior):
    from src.agents.simulator import SimulatorAgent
    return SimulatorAgent().run(prior['analysis']['rows'])['scenario_rows']


def softmax(values):
    maximum=max(values);weights=[math.exp(float(v)-maximum) for v in values];total=math.fsum(weights)
    return [w/total for w in weights]


def baseline(prior):
    grouped=defaultdict(list)
    for row in prior['simulation']['rows']:grouped[row['race_id']].append(dict(row))
    result=[]
    for rows in grouped.values():
        scores=[math.fsum(COMPONENT_WEIGHTS[k]*float(r[k]) for k in COMPONENT_WEIGHTS) for r in rows]
        base=softmax(scores);final=softmax([math.log(p) for p in base])
        for r,s,p,q in zip(rows,scores,base,final):
            r.update(S_base=s,p_base=p,p_final=q,win_prob=q)
            result.append(r)
    return result


def dataset(prior):
    labels=read(prior['intake']['labels']);environment=read(prior['intake']['environment'])
    if environment['target_date']!='2026-10-24' or environment['pre_race_capture'] is not False:
        raise Blocked('Posthoc environment date/provenance invalid')
    for source in environment['sources'].values():
        if sha(source['raw_path'])!=source['sha256']:raise Blocked('Environment original changed')
    by_race=defaultdict(list)
    for row in prior['probability']['rows']:by_race[row['race_id']].append(row)
    races=[]
    for selected in prior['integrity_audit']['selected']:
        a=read(selected['audit']);n=read(selected['normalized']);rid=a['race_id'];target=n['entries'][0]
        metadata=a['metadata_source']
        if sha(metadata['path'])!=metadata['sha256'] or not metadata['url'].startswith(('https://jra.jp/','https://www.jra.go.jp/')):
            raise Blocked('Official result source mismatch')
        sources=[s for s in labels['sources'] if s['official_url']==metadata['url']]
        if len(sources)!=1:raise Blocked('Label source missing')
        source=read({'path':sources[0]['path'],'sha256':sources[0]['sha256']})
        if source['result_url']!=metadata['url']:raise Blocked('Label result URL mismatch')
        soup=BeautifulSoup(Path(metadata['path']).read_bytes().decode(metadata['encoding']),'html.parser')
        winners={tr.select_one('td.num').get_text(strip=True) for tr in soup.select('tr')
                 if tr.select_one('td.place') and tr.select_one('td.place').get_text(strip=True)=='1'}
        payouts={}
        for dl in soup.select('dl'):
            dt=dl.find('dt');dd=dl.find('dd')
            if not dt or not dd or dt.get_text(strip=True)!='単勝':continue
            for line in dd.select('.line'):
                num=line.select_one('.num');yen=line.select_one('.yen')
                if num and yen:payouts[num.get_text(strip=True)]=float(re.sub(r'\D','',yen.get_text()))
        selected_labels={str(r['馬番']):float(r['払戻金']) for r in labels['rows'] if r['race_id']==rid and r['式別']=='単勝'}
        if not winners or winners!=set(payouts) or payouts!=selected_labels or not winners.issubset({str(r['horse_number']) for r in by_race[rid]}):
            raise Blocked('Official winner/payout/label mismatch')
        track=target['target_track'];post=datetime.fromisoformat(a['post_time']);obs=a['environment']
        if obs['cushion'] not in environment['tracks'][track]['cushion_history'] or obs['moisture'] not in environment['tracks'][track]['moisture_history']:
            raise Blocked('Track/measurement history mismatch')
        for key in ('cushion','moisture'):
            stamp=datetime.fromisoformat(obs[key]['measured_at'])
            if stamp.tzinfo is None or stamp.date().isoformat()!='2026-10-24' or stamp>=post:
                raise Blocked('Measurement date/time not before race')
        env={'cushion':obs['cushion']['cushion'],'turf_moisture_goal':obs['moisture']['turf_moisture_goal'],
             'turf_moisture_corner4':obs['moisture']['turf_moisture_corner4'],
             'weather':target['target_weather'],'track_condition':target['target_track_condition']}
        if identities(by_race[rid])!=identities(n['entries']):raise Blocked('Fit horse set mismatch')
        races.append({'race_id':rid,'date':'2026-10-24','rows':by_race[rid],'environment':env,
                      'winners':sorted(winners),'win_payouts':payouts})
    if len(races)!=len(by_race) or len(races)!=len({r['race_id'] for r in races}):raise Blocked('Fit race duplicate/missing')
    return races


def shared(root):
    paths=[root/'data/processed/result_labels.csv',root/'report/environment_model/learning_status.json',root/'data/models/environment_model.json']
    return {str(p):sha(p) if p.exists() else None for p in paths}


def independent_metrics(races,model=None):
    losses=[];briers=[];returns=[];hits=[]
    from analysis.environment_model import design_matrix
    for race in races:
        offset=np.log(np.array([float(r['win_prob']) for r in race['rows']]))
        if model is not None:
            matrix,names=design_matrix(race['rows'],race['environment'],'linear')
            if names!=model['feature_names']:raise Blocked('Model names differ')
            offset=offset+(matrix/np.asarray(model['scales']))@np.asarray(model['coefficients'])
        p=np.asarray(softmax(offset.tolist()));numbers=[str(r['horse_number']) for r in race['rows']]
        target=np.array([1/len(race['winners']) if n in race['winners'] else 0 for n in numbers])
        losses.append(-float(target@np.log(p)));briers.append(float(np.sum((p-target)**2)))
        leader=min(range(len(numbers)),key=lambda i:(-p[i],int(numbers[i])))
        hits.append(numbers[leader] in race['winners']);returns.append(race['win_payouts'].get(numbers[leader],0))
    return {'race_count':len(races),'log_loss':float(np.mean(losses)),'brier':float(np.mean(briers)),
            'top1_hit_rate':float(np.mean(hits)),'win_top1_policy_roi':sum(returns)/(100*len(races))}


def verify_candidate(races,model):
    from analysis.environment_model import design_matrix,environment_vector,HORSE_FEATURES
    if model['family']!='linear' or model['ridge']!=100 or model['status']!='candidate_not_promoted' or model['training_races']!=len(races) or model['training_end_date']!='2026-10-24':return False
    ev=np.array([environment_vector(r['environment'],'linear')[0] for r in races]);vary=np.ptp(ev,axis=0)>1e-10
    env_names=environment_vector(races[0]['environment'],'linear')[1]
    unsupported=[name for name,flag in zip(env_names,vary) if not flag]
    ranges={name:[float(min(values)),float(max(values))] for name,values in zip(env_names,ev.T)}
    if model['unsupported_environment_terms']!=unsupported or not close(model['training_environment_ranges'],ranges):return False
    active=np.tile(vary,len(HORSE_FEATURES));matrices=[design_matrix(r['rows'],r['environment'],'linear')[0]*active for r in races]
    names=design_matrix(races[0]['rows'],races[0]['environment'],'linear')[1]
    scales=np.sqrt(np.mean(np.vstack(matrices)**2,axis=0));scales[scales<1e-10]=1.0
    if names!=model['feature_names'] or not np.allclose(scales,model['scales'],rtol=0,atol=1e-12):return False
    beta=np.array(model['coefficients']);gradient=100*beta
    if not np.isfinite(beta).all() or np.any(np.abs(beta[~active])>1e-12):return False
    for race,matrix in zip(races,matrices):
        matrix=matrix/scales;offset=np.log([float(r['win_prob']) for r in race['rows']]);p=np.array(softmax((offset+matrix@beta).tolist()))
        target=np.array([1/len(race['winners']) if str(r['horse_number']) in race['winners'] else 0 for r in race['rows']])
        gradient+=matrix.T@(p-target)/len(races)
    return bool(np.max(np.abs(gradient))<1e-5)


def produce(stage,prior,root):
    if stage=='analysis':return {'rows':structural(prior),'weights':COMPONENT_WEIGHTS}
    if stage=='simulation':return {'rows':scenarios(prior)}
    if stage=='probability':return {'rows':baseline(prior),'variant':'exact_structural_softmax_temperature1_no_mc_no_market'}
    if stage=='fit':
        from analysis.environment_model import fit_model,evaluate_model
        before=shared(root)
        if before!=prior['intake']['shared_initial']:raise Blocked('Shared state changed since intake')
        races=dataset(prior);model=fit_model(races,'linear',100.0)
        metrics={'baseline':evaluate_model(races),'candidate':evaluate_model(races,model)};after=shared(root)
        if before!=after:raise Blocked('Shared state changed')
        return {'dataset':races,'model':model,'metrics':metrics,'metrics_scope':'in_sample_only',
                'shared_before':before,'shared_after':after,'validation_status':'NOT_EXECUTED',
                'test_status':'NOT_EXECUTED','production_eligible':False,'pre_race_capture':False}
    if stage=='review':
        fit=prior['fit'];races=dataset(prior)
        metrics={'baseline':independent_metrics(races),'candidate':independent_metrics(races,fit['model'])}
        checks={'dataset_matches':close(races,fit['dataset']),'candidate_stationary':verify_candidate(races,fit['model']),
                'metrics_match':close(metrics,fit['metrics']),'shared_unchanged':shared(root)==fit['shared_before']==fit['shared_after']}
        return {'review_checks':checks,'independent_metrics':metrics,'status':'OK' if all(checks.values()) else 'NG',
                'fit_sha256':canonical(fit),'review_scope':'numerical_recalculation_not_proof_of_LLM_review'}
    if stage=='report':
        return {'claim_scope':'in_sample_research_candidate_only','status':'INSUFFICIENT_DATA_RESEARCH_ONLY',
                'validation_status':'NOT_EXECUTED','test_status':'NOT_EXECUTED','production_eligible':False,
                'production_model_changed':False,'tickets':[],'research_fit_sha256':canonical(prior['fit']),
                'review_sha256':canonical(prior['review'])}
    raise Blocked('No numerical producer for stage')


def check(stage,p,prior,root):
    if stage in {'analysis','simulation','probability'}:
        expected=produce(stage,prior,root)
        return {'independent_recalculation':all(k in p and close(p[k],v) for k,v in expected.items()),
                'complete_lineup':identities(p['rows'])==identities(expected['rows'])}
    if stage=='fit':
        races=dataset(prior)
        return {'dataset_bound':close(races,p['dataset']),'fixed_fit_optimum':verify_candidate(races,p['model']),
                'metrics_recomputed':close(p['metrics'],{'baseline':independent_metrics(races),'candidate':independent_metrics(races,p['model'])}),
                'no_shared_change':p['shared_before']==p['shared_after']==shared(root)==prior['intake']['shared_initial'],
                'research_only':p['metrics_scope']=='in_sample_only' and p['validation_status']=='NOT_EXECUTED' and p['test_status']=='NOT_EXECUTED' and p['production_eligible'] is False and p['pre_race_capture'] is False}
    if stage in {'review','report'}:
        expected=produce(stage,prior,root)
        return {'independent_evidence':all(k in p and close(p[k],v) for k,v in expected.items()),
                'review_ok':expected.get('status')!='NG'}
    raise Blocked('Unknown numerical stage')
