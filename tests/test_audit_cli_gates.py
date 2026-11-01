"""Saved-input audits report machine gate failures without discarding raw proofs."""
import json
import runpy
import sys

import pytest
from pathlib import Path

from test_skill_stage_contracts import stage_payloads


@pytest.mark.parametrize("invalid_intake", [False, True])
@pytest.mark.parametrize("preserve_source_set", [False, True])
def test_saved_audit_cli_preserves_sources_and_reports_gate(tmp_path, monkeypatch, capsys, invalid_intake, preserve_source_set):
    payloads = stage_payloads(mode='prediction')
    collected = payloads['collection']
    collected['source_audit']['attempts'] = [{'request_id': 'preserved'}]
    source = tmp_path / 'collected.json'
    config = tmp_path / 'config.json'
    source.write_text(json.dumps(collected))
    configs = payloads['intake']['race_configs']
    if invalid_intake:
        configs[0]['source_url'] = 'https://example.com/unverified'
    config.write_text(json.dumps(configs))
    argv = ['audit_race_inputs.py', '--collected-path', str(source),
        '--config-path', str(config), '--output-dir', str(tmp_path / 'output'),
        '--raw-dir', str(tmp_path / 'missing_raw')]
    if preserve_source_set:
        argv.append('--preserve-source-set')
        def reject_cache_append(*args):
            raise AssertionError('Frozen evidence must not read unrelated cached HTML')
        monkeypatch.setattr('src.skill_workflow.attach_history_sources', reject_cache_append)
    monkeypatch.setattr(sys, 'argv', argv)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/audit_race_inputs.py'), run_name='__main__')
    result = json.loads(capsys.readouterr().out)
    assert result['gate'] == ('BLOCKED_SKILL_EVIDENCE' if invalid_intake else 'PASS')
    audit = json.loads(Path(result['audit_path']).read_text())
    assert audit['gate'] == 'PASS'
    if invalid_intake:
        assert not list(Path(result['audit_path']).parent.glob('*collection*output.json'))
        return
    collection = json.loads(next(Path(result['audit_path']).parent.glob('*collection*output.json')).read_text())
    assert collection['source_audit']['attempts'] == [{'request_id': 'preserved'}]
    assert collection['source_audit']['artifacts']
