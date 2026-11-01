"""Independent reviewer diagnostics and formal09; never fit a new estimator."""
from __future__ import annotations
from collections import Counter
import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np
import scipy
ROOT=Path.cwd()
sys.path.insert(0,str(ROOT/'skills/jra-environment-research/scripts'))
from research_gate import ResearchSequence,read,ref
from research_numeric import produce
from analysis.environment_model import design_matrix,environment_vector,probabilities,HORSE_FEATURES
STUDY=ROOT/'report/research/20261025_training_20261028T040209Z'
OUT=Path(__file__).resolve().parent
RUN=STUDY/'skill_runs/training_01'
def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def main():
    run=ResearchSequence(RUN)
    if (RUN/'09_record.json').exists():raise ValueError('Review already recorded; no overwrite')
    stages=['intake','collection','integrity_audit','approval_gate','analysis','simulation','probability','fit']
    prior={}
    for index,stage in enumerate(stages,1):
        record=json.loads((RUN/f'{index:02d}_record.json').read_text())
        if record['status']!='PASS' or record['stage']!=stage or record['run_id']!=run.run_id:raise ValueError('Prerequisite PASS identity missing')
        prior[stage]=read(record['output'])
    # No additional run.verify/require_next: formal record below performs all mandatory verification.
    payload=produce('review',prior,ROOT)
    payload.update(role='reviewer',run_id=run.run_id,stage='review')
    fit=prior['fit'];races=fit['dataset'];model=fit['model']
    vectors=np.array([environment_vector(r['environment'],'linear')[0] for r in races])
    varying=np.ptp(vectors,axis=0)>1e-10;active=np.tile(varying,len(HORSE_FEATURES))
    matrices=[design_matrix(r['rows'],r['environment'],'linear')[0]*active for r in races]
    matrix=np.vstack(matrices);scaled=matrix/np.asarray(model['scales'])
    singular=np.linalg.svd(scaled,compute_uv=False)
    base_flat=[];candidate_flat=[];uniform_losses=[]
    for race in races:
        n=len(race['rows']);uniform_losses.append(math.log(n))
        for target,prob in [(base_flat,probabilities(race)),(candidate_flat,probabilities(race,model))]:
            target.append({'race_id':race['race_id'],'field_size':n,'min_probability':float(min(prob)),'max_probability':float(max(prob)),'max_min_ratio':float(max(prob)/min(prob)),'normalized_entropy':float(-np.sum(prob*np.log(prob))/math.log(n)),'top1_horse_number':str(race['rows'][int(np.argmax(prob))]['horse_number'])})
    audit=json.loads((STUDY/'audit/summary.json').read_text())
    excluded=[{'race_id':r['race_id'],'gate':r['gate'],'horses_retained_in_source':r['horses'],'surface_gate':r['surface_gate'],'findings':r['findings'],'source_mismatches':r['source_mismatches']} for r in audit['races'] if not r['model_ready']]
    configurations=[ref(ROOT/'config/track_biases.json'),ref(ROOT/'config/course_learning.json')]
    diagnostics={'role':'reviewer','run_id':run.run_id,'reviewed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'input_fit':ref(RUN/'08_output.json'),'independent_review_checks':payload['review_checks'],'independent_metrics':payload['independent_metrics'],'data':{'races':len(races),'horses':sum(len(r['rows']) for r in races),'training_dates':sorted({r['date'] for r in races}),'effective_winner_events':len(races),'venue_counts':dict(Counter(next(x for x in prior['integrity_audit']['selected'] if x['race_id']==r['race_id'])['race_id'].split('_')[1] for r in races)),'weather_counts':dict(Counter(r['environment']['weather'] for r in races)),'environment_unique_vectors':len(np.unique(vectors,axis=0))},'identifiability':{'nominal_coefficients':len(model['coefficients']),'active_coefficients':int(active.sum()),'inactive_constant_environment_terms':model['unsupported_environment_terms'],'environment_vector_rank':int(np.linalg.matrix_rank(vectors)),'race_centered_design_shape':list(matrix.shape),'race_centered_raw_design_rank':int(np.linalg.matrix_rank(matrix)),'scaled_design_rank':int(np.linalg.matrix_rank(scaled)),'singular_values':singular.tolist(),'regularization':'Positive ridge gives a numerical minimizer; it does not establish empirical identification of collinear environmental effects.'},'baseline_flatness':base_flat,'candidate_flatness':candidate_flat,'uniform_reference_log_loss':float(np.mean(uniform_losses)),'whole_race_exclusions':excluded,'whole_race_exclusion_gate_counts':dict(Counter(r['gate'] for r in excluded)),'baseline_configurations':configurations,'runtime_packages':{'numpy':np.__version__,'scipy':scipy.__version__},'claim_scope':'in_sample_research_candidate_only'}
    diagnostic_path=OUT/'independent_diagnostics.json';save(diagnostic_path,diagnostics)
    payload['independent_diagnostics']=ref(diagnostic_path)
    payload_path=OUT/'review_stage_payload.json';save(payload_path,payload)
    record=run.record('review',payload,evidence=[ref(RUN/'08_record.json'),ref(diagnostic_path),ref(STUDY/'research_plan.json'),ref(STUDY/'audit/summary.json'),*configurations],checks={**payload['review_checks'],'independent_reviewer_role':True})
    print(json.dumps({'status':record['status'],'run_id':run.run_id,'record':ref(RUN/'09_record.json'),'diagnostics':ref(diagnostic_path)}))
if __name__=='__main__':main()
