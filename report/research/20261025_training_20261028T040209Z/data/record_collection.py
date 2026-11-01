"""Record only the data_collector collection stage after non-recording preflight."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'skills/jra-environment-research/scripts'))
from research_gate import ResearchSequence,ref,read,sha,dump_new


def main():
    study = Path(__file__).resolve().parent.parent
    data = study/'data'
    directory = study/'skill_runs/training_01'
    run = ResearchSequence(directory)
    if run.run_id!='d4bfcdb0a5114397abae0842ec389af6':
        raise ValueError('Unexpected research run identity')
    summary = study/'audit/summary.json'
    audits = sorted((study/'audit').glob('*_audit.json'))
    if len(audits)!=36:
        raise ValueError('Full 36-race audit artifact set not ready')
    payload = {'role':'data_collector','run_id':run.run_id,'stage':'collection',
               'audit_summary':ref(summary),'audits':[ref(path) for path in audits],
               'source_manifests':[ref(data/'source_manifests.json')],
               'label_adapter':ref(data/'labels.json'),'environment_adapter':ref(data/'environment.json'),
               'factual_adapter_provenance':ref(data/'adapter_source_provenance.json'),
               'imputations':[],'numeric_analysis_executed':False,'fit_executed':False}
    labels = read(payload['label_adapter'])
    environment = read(payload['environment_adapter'])
    summary_value = read(payload['audit_summary'])
    original_sources = read(payload['source_manifests'][0])
    checks = {'all_36_label_races_retained':len(labels['races'])==36 and len({r['race_id'] for r in labels['races']})==36,
              'whole_467_declared_horses_retained':sum(len(r['declared_entries']) for r in labels['races'])==467,
              'post_event_capture_status_explicit':environment['pre_race_capture'] is False,
              'all_36_current_audit_artifacts_present':summary_value['batch_complete'] is True and summary_value['race_count']==36 and len(audits)==36,
              'all_original_byte_digests_verified':bool(original_sources) and all(sha(item['path'])==item['sha256'] for item in original_sources)}
    records,prior = run.require_next('collection')
    derived = run.check('collection',payload,prior)
    preflight = {'role':'data_collector','run_id':run.run_id,'stage':'collection',
                 'kind':'non_recording_preflight','payload':payload,'checks':checks,'derived_checks':derived,
                 'recording_attempted':False}
    dump_new(data/'collection_preflight.json',preflight)
    if not derived or not all(value is True for value in checks.values()) or not all(value is True for value in derived.values()):
        raise ValueError('Collection preflight failed; no stage recorded')
    record = run.record('collection',payload,
                        evidence=[ref(data/'collection_preflight.json'),ref(data/'labels.json'),
                                  ref(data/'environment.json'),ref(data/'source_manifests.json'),ref(summary)],
                        checks=checks)
    print(json.dumps({'stage':record['stage'],'status':record['status'],'run_id':run.run_id,
                      'record':ref(directory/'02_record.json')},ensure_ascii=False))


if __name__=='__main__':
    main()
