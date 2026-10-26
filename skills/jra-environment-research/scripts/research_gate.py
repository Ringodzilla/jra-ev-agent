"""Fail-closed artifact chain for isolated research, never a production gate.

A local integrity checker, not a sandbox against arbitrary code execution.
Numerical producers must be separately dispatched by the orchestrator.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

STAGES=('intake','collection','integrity_audit','approval_gate','analysis','simulation','probability','fit','review','report')
ROLES=('orchestrator','data_collector','data_integrity_auditor','approval_gate','analyzer','simulator','simulator','analyzer','reviewer','reporter')
SKILLS=('jra-01-intake','jra-02-official-collection','jra-03-integrity-audit','jra-04-approval-gate',
        'jra-05-ability-analysis','jra-06-pace-simulation','jra-07-probability-calculation',
        'jra-environment-research','jra-10-review','jra-12-report')


class Blocked(ValueError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
                                    separators=(',',':'),allow_nan=False).encode()).hexdigest()


def ref(path):
    path=Path(path).resolve()
    return {'path':str(path),'sha256':sha(path)}


def read(reference):
    if not isinstance(reference,dict) or set(reference)!={'path','sha256'}:
        raise Blocked('Invalid evidence reference')
    path=Path(reference['path'])
    if not path.is_absolute() or sha(path)!=reference['sha256']:
        raise Blocked('Evidence changed')
    return json.loads(path.read_text())


def branch(root):
    value=json.loads((Path(root)/'config/skill_workflow.json').read_text())['research_branch']
    if tuple(x['step_id'] for x in value['steps'])!=STAGES or tuple(x['skill_name'] for x in value['steps'])!=SKILLS or tuple(x['role'] for x in value['steps'])!=ROLES:
        raise Blocked('Research branch contract differs from implemented validator')
    if value['production_skillsequence_equivalent'] is not False or value['production_changes_allowed'] is not False:
        raise Blocked('Production permission forbidden')
    return value


def shared_initial(root):
    paths=[root/'data/processed/result_labels.csv',root/'report/environment_model/learning_status.json',root/'data/models/environment_model.json']
    return {str(p):sha(p) if p.exists() else None for p in paths}


def freeze(root):
    root=Path(root).resolve()
    branch(root)
    paths=[p for folder in ('src','analysis','jra_scraper') for p in (root/folder).rglob('*.py')]
    paths += [root/'skills'/name/'SKILL.md' for name in set(SKILLS)]
    paths += list(Path(__file__).resolve().parent.glob('*.py'))
    paths += [root/'config/skill_workflow.json',root/'docs/skill_workflow.md',root/'docs/condition_models.md']
    paths += list((root/'report/research').rglob('research_stage_wrapper*.py'))
    return [ref(p) for p in sorted(set(paths))]


def collected_from_reference(reference):
    wrapper=read(reference)
    if wrapper.get('record_type')!='collected_raw_references' or wrapper.get('schema_version')!=1:
        raise Blocked('Full collected source wrapper required')
    collected=wrapper['collected']
    for artifact in collected['source_audit']['artifacts']:
        source=artifact.pop('raw_reference')
        raw=Path(source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source['bytes_sha256']:
            raise Blocked('Collected raw bytes changed')
        html=raw.decode(source['encoding'])
        if hashlib.sha256(html.encode()).hexdigest()!=source['html_utf8_sha256'] or source['html_utf8_sha256']!=artifact['sha256']:
            raise Blocked('Collected decoded HTML changed')
        artifact['html']=html
    return collected


def dump_new(path,value):
    with Path(path).open('x',encoding='utf-8') as file:
        json.dump(value,file,ensure_ascii=False,indent=2,allow_nan=False)
        file.write('\n')


def identities(rows):
    keys=[(str(r['race_id']),str(r['horse_number'])) for r in rows]
    if not rows or len(keys)!=len(set(keys)):
        raise Blocked('Empty or duplicate horse universe')
    return set(keys)


class ResearchSequence:
    def __init__(self,directory):
        self.directory=Path(directory).resolve()
        self.manifest=json.loads((self.directory/'manifest.json').read_text())
        self.root=Path(self.manifest['root'])
        self.run_id=self.manifest['run_id']

    @classmethod
    def create(cls,root,directory,input_refs):
        directory=Path(directory).resolve()
        if directory.exists():
            raise Blocked('New run directory required')
        for reference in input_refs:read(reference)
        code=freeze(root)  # missing skill or code is an error, never a PASS
        directory.mkdir(parents=True,exist_ok=False)
        dump_new(directory/'manifest.json',{'schema_version':1,'mode':'isolated_environment_research',
                 'run_id':uuid4().hex,'root':str(Path(root).resolve()),'freeze':code,
                 'branch':branch(root),'shared_initial':shared_initial(Path(root)), 'inputs':input_refs,'input_digest':canonical(input_refs)})
        return cls(directory)

    def verify(self):
        if branch(self.root)!=self.manifest['branch']:
            raise Blocked('Research branch configuration changed')
        if freeze(self.root)!=self.manifest['freeze']:
            raise Blocked('Current code/skill freeze changed')
        for reference in self.manifest['inputs']:read(reference)
        records=[];previous=None;outputs={}
        for index,path in enumerate(sorted(self.directory.glob('[0-9][0-9]_record.json'))):
            record=json.loads(path.read_text())
            if path.name!=f'{index+1:02d}_record.json':
                raise Blocked('Missing/skipped record filename')
            if index>=len(STAGES) or record['stage']!=STAGES[index] or record['run_id']!=self.run_id:
                raise Blocked('Stage/run identity mismatch')
            if record['previous_record']!=previous or record['manifest']!=ref(self.directory/'manifest.json'):
                raise Blocked('Parent/manifest changed')
            if record['skill']!=ref(self.root/'skills'/SKILLS[index]/'SKILL.md'):
                raise Blocked('Stage skill mismatch')
            if record['freeze']!=self.manifest['freeze'] or record['input_digest']!=self.manifest['input_digest']:
                raise Blocked('Frozen identity changed')
            if not record['evidence']:
                raise Blocked('Empty evidence')
            for r in record['evidence']:read(r)
            payload=read(record['output'])
            if payload.get('role')!=self.manifest['branch']['steps'][index]['role']:
                raise Blocked('Role binding mismatch')
            if payload.get('run_id')!=self.run_id or payload.get('stage')!=record['stage']:
                raise Blocked('Output belongs to another run/stage')
            if record['status']=='BLOCKED':
                if index!=len(list(self.directory.glob('[0-9][0-9]_record.json')))-1:
                    raise Blocked('Continued terminal blocked run')
                raise Blocked('Terminal blocked run')
            if record['status']!='PASS' or not record['checks'] or any(type(v) is not bool or not v for v in record['checks'].values()):
                raise Blocked('Invalid stage checks')
            derived=self.check(record['stage'],payload,outputs)
            if not derived or any(type(v) is not bool or not v for v in derived.values()):
                raise Blocked('Stage semantic verification failed')
            outputs[record['stage']]=payload
            records.append(record);previous=ref(path)
        return records,outputs

    def require_next(self,stage):
        records,outputs=self.verify()
        if len(records)>=len(STAGES) or STAGES[len(records)]!=stage:
            raise Blocked('Out-of-order stage')
        return records,outputs

    def record(self,stage,payload,*,evidence,checks):
        records,outputs=self.require_next(stage)
        status='PASS';reason=None
        try:
            if payload.get('role')!=self.manifest['branch']['steps'][len(records)]['role']:
                raise Blocked('Explicit role binding required')
            if payload.get('run_id')!=self.run_id or payload.get('stage')!=stage:
                raise Blocked('Explicit run/stage binding required')
            if not checks or any(type(v) is not bool or not v for v in checks.values()):
                raise Blocked('Empty/non-boolean/failed caller checks')
            if not evidence:
                raise Blocked('Empty evidence')
            for r in evidence:read(r)
            derived=self.check(stage,payload,outputs)
            if not derived or any(type(v) is not bool or not v for v in derived.values()):
                raise Blocked('Derived checks failed')
        except (ValueError,KeyError,TypeError,OSError,OverflowError) as exc:
            status='BLOCKED';reason=str(exc);derived={}
        index=len(records)+1
        output_path=self.directory/f'{index:02d}_output.json'
        dump_new(output_path,payload)
        record={'schema_version':1,'run_id':self.run_id,'stage':stage,'status':status,'reason':reason,
                'manifest':ref(self.directory/'manifest.json'),'freeze':self.manifest['freeze'],
                'input_digest':self.manifest['input_digest'],
                'previous_record':ref(self.directory/f'{index-1:02d}_record.json') if records else None,
                'skill':ref(self.root/'skills'/SKILLS[index-1]/'SKILL.md'),
                'evidence':evidence,'output':ref(output_path),'checks':{**checks,**derived},
                'execution_kind':'deterministic_research_artifact_verification'}
        dump_new(self.directory/f'{index:02d}_record.json',record)
        if status!='PASS':raise Blocked(reason)
        return record

    def check(self,stage,p,prior):
        if stage=='intake':
            plan=read(p['plan'])
            for key in ('plan','labels','environment'):
                if p[key] not in self.manifest['inputs']:
                    raise Blocked('Intake source not frozen in manifest')
            if plan['training_date_max']!=p['training_date'] or plan['validation_dates']!=p['validation_dates'] or plan['family']!=p['family'] or plan['ridge']!=p['ridge']:
                raise Blocked('Preregistered plan differs')
            return {'fixed_plan':p['training_date']=='2026-10-24' and p['validation_dates']==['2026-10-25']
                    and p['family']=='linear' and p['ridge']==100 and p['variant']=='exact_structural_softmax_temperature1_no_mc_no_market',
                    'no_production':p['production_model_changed'] is False,
                    'shared_state_bound':p['shared_initial']==self.manifest['shared_initial']}
        if stage=='collection':
            summary=read(p['audit_summary'])
            items=[read(r) for r in p['audits']]
            if not (summary['batch_complete'] is True and summary['race_count']==36 and len(items)==36
                    and len({a['race_id'] for a in items})==36):
                return {'all36':False}
            reported={r['race_id']:r for r in summary['races']}
            if set(reported)!={a['race_id'] for a in items} or any(
                    reported[a['race_id']]['gate']!=a['gate'] or reported[a['race_id']]['model_ready']!=a['model_ready'] for a in items):
                return {'summary_matches_raw_audits':False}
            manifests=[read(r) for r in p['source_manifests']]
            originals=[m for manifest in manifests for m in manifest]
            if not originals:return {'source_manifests_nonempty':False}
            for m in originals:
                if sha(m['path'])!=m['sha256']:raise Blocked('Original source bytes changed')
            for a in items:
                metadata=a['metadata_source']
                if not any(m['path']==metadata['path'] and m['sha256']==metadata['sha256'] and m['url']==metadata['url'] for m in originals):
                    raise Blocked('Result source not bound to original manifests')
                collected=collected_from_reference(a['collected_artifact'])
                artifacts=collected['source_audit']['artifacts']
                if not artifacts:raise Blocked('Audit has no bound source artifacts')
                for artifact in artifacts:
                    original=artifact['original_source']
                    matching=[m for m in originals if m['path']==original['path'] and m['sha256']==original['sha256'] and m['url']==original['url']]
                    if not matching:raise Blocked('Required source missing from original manifests')
                    raw=Path(original['path']).read_bytes()
                    if sha(original['path'])!=original['sha256'] or raw.decode(original.get('encoding') or 'utf-8')!=artifact['html']:
                        raise Blocked('Decoded source artifact differs from original bytes')
                    if hashlib.sha256(artifact['html'].encode()).hexdigest()!=artifact['sha256']:
                        raise Blocked('Source HTML digest mismatch')
            return {'all36':True,'summary_matches_raw_audits':True,'complete_original_binding':True}
        if stage=='integrity_audit':
            import sys
            sys.path.insert(0,str(self.root))
            from src.data_integrity import data_input_sha256,audit_collected_data
            snapshot=read(p['audit_code_snapshot'])
            code_files=snapshot['code_files']
            mandatory=[self.root/'src/data_integrity.py',self.root/'src/acquisition_coverage.py']
            if any(code_files.get(str(path))!=sha(path) for path in mandatory) or any(sha(path)!=value for path,value in code_files.items()):
                return {'audit_code_snapshot_current':False}
            collection=prior['collection'];items=[read(r) for r in collection['audits']]
            eligible={a['race_id'] for a in items if a['model_ready'] is True and a['gate']=='PASS'}
            selected=p['selected']
            if not selected or {x['race_id'] for x in selected}!=eligible or len(selected)!=len(eligible):
                return {'full_eligible_set':False}
            for a in items:
                collected=collected_from_reference(a['collected_artifact'])
                normalized=read(a['normalized_artifact'])
                if data_input_sha256(collected,normalized['race_configs'])!=data_input_sha256(normalized,normalized['race_configs']):
                    raise Blocked('Collected and normalized inputs differ')
                expected=audit_collected_data(collected,normalized['race_configs'],purpose='environment_training')
                if canonical(expected)!=canonical(a['collected_audit']):
                    raise Blocked('Saved audit differs from full current source reparse')
            for item in selected:
                a=read(item['audit']);n=read(item['normalized']);ca=a['collected_audit']
                if a not in items or item['race_id']!=a['race_id']:
                    return {'audit_from_collection':False}
                if (ca['gate']!='PASS' or ca['acquisition_coverage']['status']!='PASS'
                    or ca['verification_issues'] or ca['unapproved_finding_count'] or ca['findings']
                    or a['source_issues'] or a['blocking_history_conflicts']
                    or ca['history_counts_verified'] is not True
                    or ca['source_audit_status']!='VERIFIED_REPARSE'
                    or ca['input_sha256']!=data_input_sha256(n,n['race_configs'])
                    or any(r['target_race_date']!='2026-10-24' or r['target_surface']!='芝' for r in n['entries'])):
                    return {'current_audit_pass':False}
            return {'current_audit_pass':True,'all36_source_reparsed':len(items)==36}
        if stage=='approval_gate':
            return {'same_audit':p['integrity_output']==ref(self.directory/'03_output.json'),
                    'zero_substitutions':p['approved_imputations']==[] and p['blocked_imputations']==[] and p['approval_evidence']==[],
                    'no_unknowns':p['unknowns']==[]}
        from research_numeric import check
        return check(stage,p,prior,self.root)
