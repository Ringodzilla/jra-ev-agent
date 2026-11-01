"""Artificial fixtures only; no model or real race calculations."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from research_gate import ResearchSequence,Blocked,SKILLS,dump_new,ref


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.base=Path(self.tmp.name)
        self.root=self.base/'root';self.root.mkdir()
        for directory in ('src','analysis','jra_scraper'):
            path=self.root/directory;path.mkdir();(path/'fake.py').write_text('x=1\n')
        for skill in set(SKILLS):
            path=self.root/'skills'/skill;path.mkdir(parents=True);(path/'SKILL.md').write_text('Artificial fixture\n')
        (self.root/'config').mkdir()
        real=Path(__file__).resolve().parents[3]
        (self.root/'config/skill_workflow.json').write_bytes((real/'config/skill_workflow.json').read_bytes())
        (self.root/'docs').mkdir()
        for name in ('skill_workflow.md','condition_models.md','environment_model.md'):
            (self.root/'docs'/name).write_text('Artificial contract fixture')
        self.input=self.base/'input.json';dump_new(self.input,{'synthetic':True})
        self.plan=self.base/'plan.json';dump_new(self.plan,{'training_date_max':'2026-10-24','validation_dates':['2026-10-25'],'family':'linear','ridge':100})
        self.inputs=[ref(self.input),ref(self.plan)]
        self.run=ResearchSequence.create(self.root,self.base/'run',self.inputs)
    def tearDown(self):self.tmp.cleanup()
    def intake(self,**changes):
        p={'run_id':self.run.run_id,'stage':'intake','role':'orchestrator','plan':ref(self.plan),'labels':ref(self.input),'environment':ref(self.input),'training_date':'2026-10-24',
           'validation_dates':['2026-10-25'],'family':'linear','ridge':100,
           'variant':'exact_structural_softmax_temperature1_no_mc_no_market','production_model_changed':False,'shared_initial':self.run.manifest['shared_initial']}
        p.update(changes);return p
    def record_intake(self):
        return self.run.record('intake',self.intake(),evidence=[ref(self.input)],checks={'fixture':True})
    def test_valid_intake(self):
        self.record_intake();self.assertEqual(len(self.run.verify()[0]),1)
    def test_out_of_order(self):
        with self.assertRaises(Blocked):self.run.record('analysis',self.intake(),evidence=[],checks={'fake':True})
    def test_empty_and_non_bool_checks_terminal(self):
        for i,checks in enumerate(({}, {'fake':1}, {'fake':'true'})):
            run=ResearchSequence.create(self.root,self.base/f'bad{i}',self.inputs)
            payload=self.intake(run_id=run.run_id)
            with self.assertRaises(Blocked):run.record('intake',payload,evidence=[],checks=checks)
            with self.assertRaises(Blocked):run.require_next('intake')
    def test_output_tamper(self):
        self.record_intake();(self.run.directory/'01_output.json').write_text('{}')
        with self.assertRaises(Blocked):self.run.verify()
    def test_record_tamper(self):
        self.record_intake();p=self.run.directory/'01_record.json';d=json.loads(p.read_text());d['run_id']='different';p.write_text(json.dumps(d))
        with self.assertRaises(Blocked):self.run.verify()
    def test_code_or_skill_tamper(self):
        self.record_intake();(self.root/'src/fake.py').write_text('x=2')
        with self.assertRaises(Blocked):self.run.verify()
    def test_environment_contract_tamper(self):
        self.record_intake();(self.root/'docs/environment_model.md').write_text('Changed contract')
        with self.assertRaises(Blocked):self.run.verify()
    def test_other_run_output(self):
        with self.assertRaises(Blocked):self.run.record('intake',self.intake(run_id='another'),evidence=[],checks={'fake':True})
    def test_fake_gate_no_semantic_evidence(self):
        self.record_intake()
        with self.assertRaises(Blocked):self.run.record('collection',{'run_id':self.run.run_id,'stage':'collection','role':'data_collector','gate':'PASS'},evidence=[ref(self.input)],checks={'fake':True})
        with self.assertRaises(Blocked):self.run.require_next('collection')
    def test_invalid_fixed_plan(self):
        with self.assertRaises(Blocked):self.run.record('intake',self.intake(ridge=1),evidence=[],checks={'fake':True})
    def exploratory(self, **changes):
        plan={'experiment_id':'separate-sunday-fit','experiment_kind':'posthoc_exploratory_training_only',
              'training_date_max':'2026-10-25','training_dates':['2026-10-25'],
              'validation_dates':[],'test_dates':[],'allowed_target_surfaces':['芝'],
              'imputations_permitted':False,'labels_previously_inspected':True,
              'preregistered_before_label_inspection':False,'production_eligible':False,
              'family':'linear','ridge':100}
        plan.update(changes)
        path=self.base/'exploratory.json';dump_new(path,plan)
        run=ResearchSequence.create(self.root,self.base/'exploratory_run',[ref(path),ref(self.input)])
        payload=self.intake(run_id=run.run_id,plan=ref(path),training_date=plan['training_date_max'],
                            validation_dates=plan['validation_dates'],audit_purpose='environment_structural_research')
        return run,payload
    def test_separate_sunday_fit_only_contract(self):
        run,payload=self.exploratory()
        run.record('intake',payload,evidence=[ref(self.input)],checks={'fixture':True})
        self.assertEqual(len(run.verify()[0]),1)
    def test_cannot_relabel_old_holdout_as_training(self):
        with self.assertRaises(Blocked):
            self.run.record('intake',self.intake(training_date='2026-10-25',validation_dates=[]),
                            evidence=[ref(self.input)],checks={'fixture':True})
    def test_exploratory_overlap_forbidden(self):
        run,payload=self.exploratory(validation_dates=['2026-10-25'])
        with self.assertRaises(Blocked):run.record('intake',payload,evidence=[ref(self.input)],checks={'fixture':True})
    def test_exploratory_cannot_enable_production(self):
        run,payload=self.exploratory(production_eligible=True)
        with self.assertRaises(Blocked):run.record('intake',payload,evidence=[ref(self.input)],checks={'fixture':True})
    def test_exploratory_market_purpose_mismatch(self):
        run,payload=self.exploratory();payload['audit_purpose']='environment_training'
        with self.assertRaises(Blocked):run.record('intake',payload,evidence=[ref(self.input)],checks={'fixture':True})
    def test_invalid_canonical_training_date(self):
        run,payload=self.exploratory(training_date_max='2026-02-30',training_dates=['2026-02-30'])
        with self.assertRaises(Blocked):run.record('intake',payload,evidence=[ref(self.input)],checks={'fixture':True})
    def test_input_tamper(self):
        self.record_intake();self.input.write_text('{}')
        with self.assertRaises(Blocked):self.run.verify()
    def test_branch_tamper(self):
        self.record_intake();p=self.root/'config/skill_workflow.json'
        d=json.loads(p.read_text());d['research_branch']['steps'][4]['role']='simulator';p.write_text(json.dumps(d))
        with self.assertRaises(Blocked):self.run.verify()
    def test_fake36_without_sources_cannot_pass(self):
        summary=self.base/'summary.json';dump_new(summary,{'batch_complete':True,'race_count':36,'races':[{'race_id':str(i),'gate':'PASS','model_ready':True} for i in range(36)]})
        audits=[]
        for i in range(36):
            p=self.base/f'a{i}.json';dump_new(p,{'race_id':str(i),'gate':'PASS','model_ready':True});audits.append(ref(p))
        checks=self.run.check('collection',{'audit_summary':ref(summary),'audits':audits,'source_manifests':[]},{})
        self.assertFalse(all(checks.values()))
    def test_existing_directory_refused(self):
        with self.assertRaises(Blocked):ResearchSequence.create(self.root,self.run.directory,[ref(self.input)])


if __name__=='__main__':unittest.main()
