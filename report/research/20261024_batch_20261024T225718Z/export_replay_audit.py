#!/usr/bin/env python3
"""Export exact collected inputs by transparent audit instrumentation, then replay."""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BASE))
from src.data_integrity import audit_collected_data, data_input_sha256


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def hydrate_collected(record):
    if record.get('schema_version') != 1 or record.get('record_type') != 'collected_raw_references':
        raise ValueError('Unsupported collected reference schema')
    collected = copy.deepcopy(record['collected'])
    for artifact in collected.get('source_audit', {}).get('artifacts', []):
        reference = artifact.pop('raw_reference')
        raw = Path(reference['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference['bytes_sha256']:
            raise ValueError('Raw original byte hash mismatch')
        html = raw.decode(reference['encoding'])
        value = hashlib.sha256(html.encode()).hexdigest()
        if value != reference['html_utf8_sha256'] or value != artifact['sha256']:
            raise ValueError('Decoded source hash mismatch')
        artifact['html'] = html
    return collected


def export_collected(collected):
    result = copy.deepcopy(collected)
    for artifact in result.get('source_audit', {}).get('artifacts', []):
        source = artifact['original_source']
        html = artifact.pop('html')
        raw = Path(source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('Original source changed before export')
        if raw.decode(source['encoding']) != html:
            raise ValueError('Raw reference does not reconstruct original HTML')
        artifact['raw_reference'] = dict(path=source['path'], encoding=source['encoding'],
            bytes_sha256=source['sha256'], html_utf8_sha256=hashlib.sha256(html.encode()).hexdigest())
    return dict(schema_version=1, record_type='collected_raw_references', collected=result)


def child_run(source, output, group):
    summary = json.loads((source / 'summary.json').read_text())
    records = summary['races'][::2] if group == 'odd' else summary['races'][1::2]
    index = {r['race_id']: r for r in records}
    snapshot = json.loads((source / 'input_snapshot.json').read_text())
    paths = [Path(p) for p in snapshot['input_files'] if Path(p).name.startswith('20261024_')]
    input_directory = paths[0].parent
    frozen_manifest = source / 'manifest_snapshot.json'
    environment_path = next(Path(p) for p in snapshot['input_files'] if Path(p).name.startswith('observations'))
    spec = importlib.util.spec_from_file_location('replay_audit_builder', BASE / 'audit_batch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []

    def checked_audit(collected, race_configs, approvals=None, purpose='prediction'):
        race_ids = {str(e['race_id']) for e in collected['entries']}
        if len(race_ids) != 1:
            raise ValueError('One race required for exact replay export')
        race_id = next(iter(race_ids))
        record = index[race_id]
        numeric = record['numeric_race_id']
        expected_path = source / (numeric + '_audit.json')
        expected = json.loads(expected_path.read_text())
        export = export_collected(collected)
        restored = hydrate_collected(export)
        if canonical(restored) != canonical(collected):
            raise ValueError('Hydration is not an exact reconstruction')
        replay = audit_collected_data(restored, race_configs, approvals, purpose)
        if canonical(replay) != canonical(expected['collected_audit']):
            raise ValueError('Current core replay differs from the saved audit: ' + race_id)
        normalized_source = source / (numeric + '_normalized.json')
        normalized = json.loads(normalized_source.read_text())
        if data_input_sha256(restored, race_configs) != replay['input_sha256']:
            raise ValueError('Collected input hash mismatch')
        if data_input_sha256(normalized, normalized['race_configs']) != replay['input_sha256']:
            raise ValueError('Normalized input hash mismatch')
        collected_path = output / (numeric + '_collected.json')
        save(collected_path, export)
        normalized_path = output / normalized_source.name
        shutil.copyfile(normalized_source, normalized_path)
        if digest(normalized_path) != digest(normalized_source):
            raise ValueError('Normalized byte copy differs')
        payload = dict(expected, collected_artifact={'path': str(collected_path), 'sha256': digest(collected_path)},
            normalized_artifact={'path': str(normalized_path), 'sha256': digest(normalized_path)},
            source_audit_artifact={'path': str(expected_path), 'sha256': digest(expected_path)},
            audit_code_snapshot={'path': str(source / 'input_snapshot.json'), 'sha256': digest(source / 'input_snapshot.json')},
            replay_checks={'hydration_exact': True, 'current_core_exact': True, 'input_hash_exact': True})
        save(output / expected_path.name, payload)
        captured.append(numeric)
        return replay

    # Transparent instrumentation: every intercepted call returns the actual current audit result.
    module.audit_collected_data = checked_audit
    sys.argv = [str(BASE / 'audit_batch.py'), '--input-dir', str(input_directory),
                '--manifest', str(frozen_manifest), '--environment', str(environment_path)]
    for record in records:
        sys.argv += ['--race-id', record['numeric_race_id']]
    stream = io.StringIO()
    with redirect_stdout(stream):
        module.main()
    (output / ('builder_' + group + '_stdout.txt')).write_text(stream.getvalue())
    save(output / ('group_' + group + '_replay.json'), dict(status='PASS', race_ids=captured))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-audit-dir', type=Path, required=True)
    parser.add_argument('--export-dir', type=Path)
    parser.add_argument('--group', choices=['odd', 'even'])
    args = parser.parse_args()
    source = args.source_audit_dir.resolve()
    if args.group:
        child_run(source, args.export_dir.resolve(), args.group)
        return
    summary = json.loads((source / 'summary.json').read_text())
    if summary.get('race_count') != 36 or not summary.get('batch_complete'):
        raise ValueError('Current complete 36-race audit is required')
    frozen = json.loads((source / 'input_snapshot.json').read_text())
    bound = dict(frozen['input_files'], **frozen['code_files'])
    bound[str(Path(__file__).resolve())] = digest(Path(__file__).resolve())
    if any(digest(Path(p)) != value for p, value in bound.items()):
        raise ValueError('Source audit input or core/spec code changed; cannot reuse audit')
    output = BASE / ('replay_audit_export_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(exist_ok=False)
    save(output / 'replay_input_snapshot.json', dict(bound_files=bound, source_summary_sha256=digest(source / 'summary.json')))
    children = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--source-audit-dir', str(source),
                '--export-dir', str(output), '--group', group], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for group in ['odd', 'even']]
    for group, child in zip(['odd', 'even'], children):
        stdout, stderr = child.communicate()
        (output / ('worker_' + group + '_stdout.txt')).write_text(stdout)
        (output / ('worker_' + group + '_stderr.txt')).write_text(stderr)
        if child.returncode:
            raise RuntimeError('Replay export failed; retained logs: ' + str(output))
    if any(digest(Path(p)) != value for p, value in bound.items()):
        raise ValueError('Input or code changed during independent replay')
    records = []
    for original in summary['races']:
        numeric = original['numeric_race_id']
        audit_path = output / (numeric + '_audit.json')
        payload = json.loads(audit_path.read_text())
        records.append(dict(original, audit_path=str(audit_path), audit_sha256=digest(audit_path),
            collected_artifact=payload['collected_artifact'], normalized_artifact=payload['normalized_artifact']))
    save(output / 'summary.json', dict(summary, races=records, replay_export_status='PASS',
        source_audit_summary={'path': str(source / 'summary.json'), 'sha256': digest(source / 'summary.json')},
        audit_code_snapshot={'path': str(source / 'input_snapshot.json'), 'sha256': digest(source / 'input_snapshot.json')}))
    print(json.dumps({'export_dir': str(output), 'races': len(records), 'replay_exact': True}))


if __name__ == '__main__':
    main()
