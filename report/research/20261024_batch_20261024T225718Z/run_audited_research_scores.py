#!/usr/bin/env python3
"""Analyzer-only research scores; requires immutable whole-race audit PASS."""
from collections import defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
from structural_research_features import structural_features, COMPONENT_WEIGHTS
from src.data_integrity import data_input_sha256

BASE = Path(__file__).resolve().parent



def ref(path):
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


class NoCurrentMarket(dict):
    def __getitem__(self, key):
        if key in {'current_odds','current_popularity','win_odds'}:
            raise ValueError('Forbidden current market feature access')
        return super().__getitem__(key)
    def get(self, key, default=None):
        return self[key] if key in self else default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    audit_directory = args.audit_dir.resolve()
    output = args.output_dir.resolve()
    if not audit_directory.is_relative_to(BASE) or not output.is_relative_to(BASE) or output.exists():
        raise ValueError('New output and audit directories must be inside isolated batch')
    eligible = []
    for path in sorted(audit_directory.glob('*_audit.json')):
        item = json.loads(path.read_text())
        if item['gate'] == 'PASS' and item['model_ready'] is True:
            eligible.append(path.name.removesuffix('_audit.json'))
    if not eligible:
        raise ValueError('No model-ready audited races')
    staged = []
    for numeric_id in eligible:
        audit_path = audit_directory / f'{numeric_id}_audit.json'
        input_path = audit_directory / f'{numeric_id}_normalized.json'
        audit = json.loads(audit_path.read_bytes())
        inputs = json.loads(input_path.read_bytes())
        ca = audit['collected_audit']
        digest = data_input_sha256(inputs, inputs['race_configs'])
        if (audit['gate'] != 'PASS' or audit['model_ready'] is not True or ca['gate'] != 'PASS'
                or ca['input_sha256'] != digest or ca['unapproved_finding_count'] != 0
                or ca['findings'] or ca['verification_issues'] or audit['source_issues']
                or audit['blocking_history_conflicts']):
            raise ValueError('Audit gate/hash/finding check failed: ' + numeric_id)
        if len(inputs['race_configs']) != 1:
            raise ValueError('Single-race input required')
        groups = defaultdict(list)
        for row in inputs['rows']:
            groups[str(row['horse_number'])].append(NoCurrentMarket(row))
        entries = inputs['entries']
        if {r['target_surface'] for r in entries} != {'芝'} or {r['target_race_date'] for r in entries} != {'2026-10-24'}:
            raise ValueError('Only authorized Saturday turf allowed')
        numbers = [str(entry['horse_number']) for entry in entries]
        if len(numbers) != len(set(numbers)) or set(numbers) != set(groups):
            raise ValueError('Whole horse universe mismatch')
        features = []
        for entry in entries:
            feature = structural_features(groups[str(entry['horse_number'])],NoCurrentMarket(entry))
            feature['S_base_before_simulation'] = sum(weight * feature[key] for key,weight in COMPONENT_WEIGHTS.items())
            feature['weighted_components_before_simulation'] = {
                key:weight*feature[key] for key,weight in COMPONENT_WEIGHTS.items()}
            features.append(feature)
        if {str(row['horse_number']) for row in features} != set(numbers):
            raise ValueError('Horse dropped during scoring')
        artifact = {
            'record_type':'research_analyzer_structural_scores','role':'analyzer','status':'PASS',
            'race_id':audit['race_id'],'numeric_race_id':numeric_id,
            'input_sha256':digest,'input_artifact':ref(input_path),'audit_artifact':ref(audit_path),
            'code_artifacts':[ref(Path(__file__).resolve()),ref(BASE/'structural_research_features.py'),
                              ref(BASE.parents[2]/'src/feature_engineering.py'),
                              ref(BASE.parents[2]/'src/track_bias.py')],
            'feature_rows':features,'horse_numbers':numbers,'component_weights':COMPONENT_WEIGHTS,
            'simulator_contract':{
                'S_base':'sum(component_weights[key] * final_feature_row[key])',
                'pace_component':'Simulator may replace pace_score only using explicit saved high/mid/slow contract.',
                'probability':'No probabilities calculated in this analyzer artifact. Next role applies exact Softmax(S_base).',
                'temperature':1,'monte_carlo':False,'market_blending':False},
            'environment':audit['environment'],'environment_provenance':audit['environment_source_path'],
            'provenance_type':'posthoc_retrospective_reconstruction','pre_race_capture':False,
            'production_learning_eligible':False,'current_market_access_trap':'PASS',
            'defaults_applied':[],'results_labels_read':False,'probability_ev_fit_calls':0,
            'production_model_changed':False,'generated_at':datetime.now(timezone.utc).isoformat()}
        staged.append((numeric_id,artifact))
    output.mkdir(exist_ok=False)
    for numeric_id,artifact in staged:
        (output/f'{numeric_id}_scores.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
    summary={'status':'PASS','races':len(staged),'horses':sum(len(a['feature_rows']) for _,a in staged),
             'audit_directory':str(audit_directory), 'eligible_race_ids':eligible,
             'artifacts':[ref(output/f'{numeric_id}_scores.json') for numeric_id,_ in staged],
             'probability_ev_fit_calls':0,'defaults_applied':[],'current_market_model_reads':0}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(output),'races':summary['races'],'horses':summary['horses']}))


if __name__=='__main__':
    main()
