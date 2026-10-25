#!/usr/bin/env python3
"""Prepare audited inputs for the analyzer's isolated, fixed research fit.

No baseline probabilities, EV, environmental coefficients or predictions are
calculated by this preparation script. Simulator/EV stages must supply the
baseline artifact after the approval gate. Research fitting remains a separate
explicitly dispatched analyzer task.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.data_integrity import data_input_sha256


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference(path):
    return {'path': str(path.resolve()), 'sha256': digest(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    output = args.output.resolve()
    if not output.is_relative_to(base) or output.exists():
        raise ValueError('Output must be a new file inside this isolated batch')
    plan = json.loads(args.plan.read_text())
    if (plan['family'], plan['ridge'], plan['training_date_max'], plan['validation_dates']) != (
            'linear', 100.0, '2026-10-24', ['2026-10-25']):
        raise ValueError('Preregistered model or whole-date partition changed')
    accepted, excluded = [], []
    for audit_path in sorted(args.audit_dir.glob('*_audit.json')):
        audit = json.loads(audit_path.read_text())
        normalized_path = audit_path.with_name(audit_path.name.replace('_audit.json', '_normalized.json'))
        normalized = json.loads(normalized_path.read_text())
        configs = normalized['race_configs']
        if len(configs) != 1:
            raise ValueError('Single-race audit config required')
        bound = data_input_sha256(normalized, configs)
        if bound != audit['collected_audit']['input_sha256']:
            raise ValueError('Audit input hash mismatch: ' + str(audit_path))
        reasons = []
        if audit.get('gate') != 'PASS' or audit.get('model_ready') is not True:
            reasons.append('AUDIT_NOT_MODEL_READY')
        if audit['collected_audit']['gate'] != 'PASS':
            reasons.append('COLLECTED_AUDIT_NOT_PASS')
        if audit['collected_audit']['unapproved_finding_count'] != 0:
            reasons.append('UNAPPROVED_IMPUTATION')
        entries = normalized['entries']
        dates = {str(row['target_race_date']) for row in entries}
        if len(dates) != 1 or not dates or max(dates) > plan['training_date_max']:
            reasons.append('OUTSIDE_TRAIN_DATE_RANGE')
        if {row['target_surface'] for row in entries} != {'芝'}:
            reasons.append('OUTSIDE_TURF_MODEL')
        item = {'race_id': audit['race_id'], 'input_sha256': bound,
                'normalized': reference(normalized_path), 'audit': reference(audit_path)}
        if reasons:
            excluded.append(dict(item, reason_codes=reasons))
        else:
            accepted.append(item)
    result = {
        'record_type': 'research_training_preparation',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'AWAITING_BASELINE_STAGES' if accepted else 'BLOCKED_NO_AUDIT_PASS_INPUTS',
        'role': 'analyzer', 'plan': reference(args.plan),
        'accepted_training_inputs': accepted, 'excluded_inputs': excluded,
        'fixed_model': {'family': 'linear', 'ridge': 100.0},
        'split': {'train_through': '2026-10-24', 'validation_dates': ['2026-10-25'],
                  'test': 'later separate unused race day; not yet observed'},
        'required_next_artifacts': [
            'Explicit root dispatch after input audit PASS; no imputation approval inferred from dispatch.',
            'Simulator/EV baseline race dataset bound to each input_sha256 with same complete horse universe.',
            'Independently verified win labels bound to official result source hashes.',
            'Retrospective environment reconstruction with actual retrieval and pre_race_capture=false.',
        ],
        'fit_api': 'analysis.environment_model.fit_model(train_races, family="linear", ridge=100.0)',
        'evaluation_api': 'analysis.environment_model.evaluate_model(validation_races, frozen_candidate)',
        'forbidden_apis': ['train_and_compare: auto split and hyperparameter selection conflicts with frozen plan',
                           'legacy learning CLIs: missing approval gate'],
        'limitations': [
            'Post-race reconstruction is not evidence the same inputs were available before post time.',
            'Weather and condition from result headers are retrospective observations.',
            'Saturday contains at most 18 turf races; minimum production training count is 100.',
            'Sunday remains validation only; no tuning from Sunday labels.',
            'No later independent test day has been supplied; production eligibility cannot be established.',
            'Existing seed races require fresh audits before inclusion; old fixed data is not automatically approved.',
        ],
        'calculations_performed': [], 'production_learning_eligible': False,
        'production_model_changed': False, 'shared_learning_state_changed': False,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'path': str(output), 'status': result['status'],
                      'accepted': len(accepted), 'excluded': len(excluded)}))


if __name__ == '__main__':
    main()
