"""Serialize the independent reviewer's source/code/experimental assessment."""
from pathlib import Path
import datetime,hashlib,json
import numpy as np
ROOT=Path.cwd();STUDY=ROOT/'report/research/20261025_training_20261028T040209Z';OUT=STUDY/'review';RUN=STUDY/'skill_runs/training_01'
def ref(p):return {'path':str(Path(p).resolve()),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
def main():
    record=json.loads((RUN/'09_record.json').read_text())
    if record['status']!='PASS':raise ValueError('Formal numerical review PASS required')
    d=json.loads((OUT/'independent_diagnostics.json').read_text());fit=json.loads((RUN/'08_output.json').read_text());plan=json.loads((STUDY/'research_plan.json').read_text());integrity=json.loads((RUN/'03_output.json').read_text());history=[]
    for item in integrity['selected']:history+=json.loads(Path(item['normalized']['path']).read_text())['rows']
    old=plan['old_experiment']['contract'];old_matches=ref(old['path'])['sha256']==old['sha256']
    checks=[
      {'id':'experimental_scope','status':'PASS','finding':'Sunday-only posthoc exploratory fit, labels_previously_inspected=true, not preregistered before labels. Original Saturday-train/Sunday-validation experiment is separate and unchanged.'},
      {'id':'whole_race_universe','status':'PASS','finding':'All 10 current-code eligible turf races and all 120 starters/371 past histories retained. No missing horse was individually excluded.'},
      {'id':'market_and_results_separation','status':'PASS','finding':'Strict structural projection never reads or generates target odds/popularity. Target outcomes/payouts are used only as fit labels and retrospective policy metrics. Every historical race predates 2026-10-25.'},
      {'id':'baseline_configs_bound','status':'PASS','finding':'Both track_biases.json and course_learning.json are SHA-bound in analysis05 and subsequent numerical-stage evidence; no frozen code/config was edited during calculation.'},
      {'id':'no_imputation','status':'PASS','finding':'Selected whole-race audits have zero findings and required missingness, no imputation approval/adoption. Unsupported or blocked races remain in artifacts with reasons.'},
      {'id':'independent_numerics','status':'PASS','finding':'Independent dataset, metric, gradient-stationarity and shared-state checks all true; formal09 verifies full current-code source replay and stage lineage.'},
      {'id':'baseline_identity','status':'PASS_WITH_LIMITATIONS','finding':'This is exact_structural_softmax_temperature1_no_mc_no_market. It reuses common features and pace functions but differs from the existing production Monte Carlo probability baseline; candidate gains cannot be reported as production MC improvement.'},
      {'id':'environment_application','status':'PASS_WITH_LIMITATIONS','finding':'Baseline environment_score=0; learned race-centered horse/environment interaction is added once to log baseline probability. Existing course condition matching and the residual condition interaction can be correlated; no manually added duplicate environment coefficient is present.'},
      {'id':'sample_and_identifiability','status':'RESEARCH_ONLY','finding':'One date, ten race winner events, three venues. 28 nominal/24 active coefficients but design rank16. Ridge selects a numerical minimizer without proving identification of all physical effects.'},
      {'id':'decision_quality','status':'LIMITED','finding':'Baseline and candidate top-one policies both miss all10 races and ROI=0. Baseline loss is almost uniform-reference loss; small training loss improvement does not demonstrate horse discrimination, calibration, profitability or generalization.'},
      {'id':'heldout_and_promotion','status':'PASS','finding':'No validation/test, no model promotion, no real tickets. Production and shared-state hashes remain unchanged. Original model and experiment assets are retained.'}
    ]
    result={
      'role':'reviewer','run_id':record['run_id'],'reviewed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'OK','approval_scope':'isolated_posthoc_exploratory_training_artifact_only','production_approval':False,
      'reason':'The fixed exploratory candidate and its limited in-sample claims match the frozen plan; independent numerical/source lineage checks pass. Sample size, confounding, rank deficiency and weak baseline discrimination prevent claims of validated predictive improvement or production readiness.',
      'fix':'No fix required to retain this research-only artifact. The future research requirements below are not relaxed gates or newly invented rejection thresholds.','evaluated_checks':checks,
      'evidence':[ref(RUN/'09_record.json'),ref(RUN/'08_output.json'),ref(OUT/'independent_diagnostics.json'),ref(STUDY/'research_plan.json'),ref(STUDY/'audit/summary.json'),ref(OUT/'configuration_dependency_review.json'),ref(OUT/'analysis_configuration_binding.json'),ref(OUT/'simulation_configuration_binding.json')],
      'training_metrics':d['independent_metrics'],'uniform_reference_log_loss':d['uniform_reference_log_loss'],
      'baseline_distribution':{'mean_normalized_entropy':float(np.mean([r['normalized_entropy'] for r in d['baseline_flatness']])),'normalized_entropy_range':[min(r['normalized_entropy'] for r in d['baseline_flatness']),max(r['normalized_entropy'] for r in d['baseline_flatness'])],'max_min_probability_ratio_range':[min(r['max_min_ratio'] for r in d['baseline_flatness']),max(r['max_min_ratio'] for r in d['baseline_flatness'])],'interpretation':'Near-uniform baseline. Correct numerical execution and training improvement do not prove useful discrimination.'},
      'coefficient_identifiability':d['identifiability'],'environment_coverage':d['data'],
      'independent_validation':{'executed':False,'test_executed':False,'old_contract_sha_unchanged':old_matches,'old_validation_cannot_be_reused_as_independent_evaluation_of_this_candidate':True,'minimum_policy':{'training_races':100,'validation_races':20,'test_races':20},'minimum_policy_met':False},
      'data_use_trace':[
        {'data':'Past finish/time/distance','use':'Common recency-weighted ability and speed features'},
        {'data':'Past last3f and final corner order','use':'Closing strength/front rate, common pace scenarios; source missingness is not replaced'},
        {'data':'Past course/surface/condition/jockey/weight; target assigned weight/frame/jockey','use':'Common course, weight, jockey and pace priors, with frozen config dependencies'},
        {'data':'Date, horse identity, full career and result lineup','use':'Cutoff, exact latest5 history and whole-starter-set integrity checks'},
        {'data':'Target observed weather, cushion and turf moisture, measured time/history','use':'Source-bound post-event environment vectors interacting with four race-centered horse features'},
        {'data':'Target winners and win payouts','use':'Conditional fit targets and one100-yen top-one retrospective policy metrics only'},
        {'data':'Historical leader opening600m observations','use':'Retained and source-audited but not connected to this fixed common structural/pace function. Future validated pace feature experiment required.','observed_in_selected_histories':sum(str(r.get('pace','')).strip() not in ['', 'None','null','-','―'] for r in history),'selected_history_rows':len(history)},
        {'data':'Historical odds/popularity','use':'Retained and audited under strict history contract; computed historical market summaries are not used by the strict structural score'},
        {'data':'All eight target final odds tables, body weight and other retained raw metadata','use':'Preserved for future price/EV/body-weight or other separately validated experiments; not silently presented as learned ability features or inputs to this candidate'}
      ],
      'remaining_races':{'count':len(d['whole_race_exclusions']),'gate_counts':d['whole_race_exclusion_gate_counts'],'race_reasons':d['whole_race_exclusions'],'policy':'All remain in canonical source data. Collection completeness is distinct from eligibility for this strict turf research input contract.'},
      'allowed_claims':['A fixed linear/ridge100 research candidate was fitted on10 whole Sunday turf races/120 horses with no unapproved imputation.','In-sample log loss and Brier decreased slightly against the frozen research baseline.','Both top-one policies remain0/10 and no predictive or financial improvement has been validated.'],
      'forbidden_claims':['Validated environmental causal effects','Independent validation or test improvement','Improvement of production Monte Carlo probabilities','All36 races were fitted','High-EV horse discovery or profitable betting','Promotion or production use'],
      'future_research_requirements':['Collect independent dates and conditions, preserving chronology and existing held-out experiments; meet existing minimum100/20/20 policy before considering promotion.','Compare basic-score scale/calibration and discrimination with uniform probabilities before attributing residual environment fit to useful horse-strength separation.','Connect retained official historical pace to an explicitly tested feature/pace experiment; do not hide weakness behind extra longshot or NO_GO filters.','Address source-supported NAR/straight-course/jump/debut inputs with separate tested contracts rather than missing-horse exclusion or unapproved numeric defaults.','If eventual production adoption is considered, use a production-compatible baseline or validate the declared baseline change before transferring environment coefficients.']
    }
    if not old_matches:result.update(status='NG',reason='Original experiment contract changed',fix='Restore/resolve the original contract and rerun the affected chain in a new run.')
    p=OUT/'semantic_review.json';p.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n');print(json.dumps({'status':result['status'],'scope':result['approval_scope'],'path':str(p),'sha256':ref(p)['sha256']}))
if __name__=='__main__':main()
