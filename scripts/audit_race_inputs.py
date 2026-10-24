#!/usr/bin/env python3
"""Audit saved race inputs without running an analyzer or rewriting predictions."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.artifacts import atomic_write_json
from src.data_integrity import audit_collected_data
from src.skill_workflow import SkillSequence, SkillGateBlocked, attach_history_sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collected-path', required=True, type=Path)
    parser.add_argument('--config-path', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--raw-dir', type=Path, default=ROOT / 'data/raw')
    parser.add_argument('--approvals-path', type=Path)
    args = parser.parse_args()
    data = json.loads(args.collected_path.read_text())
    collected = data.get('data_collector', data)
    configs = json.loads(args.config_path.read_text())
    attach_history_sources(collected, args.raw_dir)
    approvals = json.loads(args.approvals_path.read_text()) if args.approvals_path else []
    audit = audit_collected_data(collected, configs, approvals)
    sequence = SkillSequence(args.output_dir)
    sequence.record('intake', {'configs': configs}, checks={'configs_present': bool(configs)})
    sequence.record('collection', {'source_path': str(args.collected_path.resolve())}, checks={'saved_collector_present': bool(collected)})
    sequence.record('integrity_audit', audit, checks={'audit_completed': True})
    try:
        sequence.record('approval_gate', audit, checks={'audit_passed': audit['gate'] == 'PASS'}, failure_code=audit['gate'])
    except SkillGateBlocked:
        pass
    # Each invocation has its own directory; previous audit evidence stays intact.
    atomic_write_json(sequence.directory / 'audit.json', audit)
    print(json.dumps({'gate': audit['gate'], 'input_sha256': audit['input_sha256'],
                      'missing_field_counts': audit['missing_field_counts'],
                      'unapproved_finding_count': audit['unapproved_finding_count'],
                      'audit_path': str((sequence.directory / 'audit.json').resolve()),
                      'next_action': 'analyze_after_all_gates_pass' if audit['gate'] == 'PASS' else 'resolve_findings_and_obtain_explicit_user_approval'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
