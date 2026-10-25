#!/usr/bin/env python3
"""Audit independent races in exactly two processes, then verify and merge references."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.data_integrity import data_input_sha256


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input-dir', required=True)
    ap.add_argument('--manifest', default='manifest_v2.json')
    ap.add_argument('--environment', type=Path)
    args = ap.parse_args()
    base = Path(__file__).resolve().parent
    source = (base / args.input_dir).resolve()
    repair_status = source / 'repair_snapshot_status.json'
    if repair_status.exists() and json.loads(repair_status.read_text()).get('status') != 'COMPLETE':
        raise ValueError('Fact reconstruction snapshot is not COMPLETE')
    paths = sorted(p for p in source.glob('*.json') if re.fullmatch(r'\d{8}_\d{2}_\d{2}\.json', p.name))
    if len(paths) != 36:
        raise ValueError('Full parallel batch requires exactly 36 immutable race inputs')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = base / ('parallel_audit_' + stamp)
    output.mkdir(exist_ok=False)
    input_hashes = {str(p): sha(p) for p in paths}
    environment_path = args.environment.resolve() if args.environment else sorted(
        (ROOT / 'report/research/20261024_environment_reconstruction').glob('observations*.json'))[-1].resolve()
    input_hashes[str(environment_path)] = sha(environment_path)
    manifest_records, manifest_sources = [], []
    names = ['manifest.json'] if args.manifest == 'manifest.json' else ['manifest.json', args.manifest]
    for name in names:
        path = base / name
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        manifest_records.extend(json.loads(data))
        manifest_sources.append({'path': str(path), 'sha256': digest})
    frozen_manifest = output / 'manifest_snapshot.json'
    write(frozen_manifest, manifest_records)
    audit_script = base / 'audit_batch.py'
    code_paths = [Path(__file__).resolve(), audit_script, base / 'repair_official_facts.py',
                  ROOT / 'src/data_integrity.py', ROOT / 'jra_scraper/parser.py',
                  ROOT / 'jra_scraper/validation.py', ROOT / 'src/feature_engineering.py']
    code_hashes = {str(p): sha(p) for p in code_paths}
    write(output / 'input_snapshot.json', dict(input_files=input_hashes, code_files=code_hashes,
                                              source_manifests=manifest_sources, frozen_manifest_sha256=sha(frozen_manifest)))
    children = []
    for group in (paths[::2], paths[1::2]):
        command = [sys.executable, str(audit_script), '--input-dir', str(source),
                   '--manifest', str(frozen_manifest.relative_to(base))]
        command += ['--environment', str(environment_path)]
        for path in group:
            command += ['--race-id', path.stem]
        children.append((subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True), command))
    directories = []
    for index, (child, command) in enumerate(children, 1):
        stdout, stderr = child.communicate()
        (output / f'group_{index}_stdout.txt').write_text(stdout)
        (output / f'group_{index}_stderr.txt').write_text(stderr)
        write(output / f'group_{index}_command.json', command)
        if child.returncode:
            raise RuntimeError(f'Audit subprocess {index} failed; retained logs in {output}')
        result = json.loads(stdout.strip().splitlines()[-1])
        directories.append(Path(result['output_directory']))
        print(json.dumps({'group_complete': index, 'races': result['races']}), flush=True)
    if any(sha(Path(path)) != digest for path, digest in input_hashes.items()):
        raise ValueError('Input snapshot changed during audit')
    if any(sha(Path(path)) != digest for path, digest in code_hashes.items()):
        raise ValueError('Core/script code changed during audit; final audit must use stable code')
    races, references, ids = [], [], set()
    for directory in directories:
        summary_path = directory / 'summary.json'
        summary = json.loads(summary_path.read_text())
        if summary['source_manifest_sha256'] != sha(frozen_manifest):
            raise ValueError('Audit used a different source manifest snapshot')
        references.append({'summary_path': str(summary_path), 'sha256': sha(summary_path)})
        for record in summary['races']:
            numeric = record['numeric_race_id']
            if numeric in ids:
                raise ValueError('Duplicate independent race in merged summary')
            ids.add(numeric)
            audit_path = directory / (numeric + '_audit.json')
            normalized_path = directory / (numeric + '_normalized.json')
            audit = json.loads(audit_path.read_text())
            normalized = json.loads(normalized_path.read_text())
            if audit['gate'] != record['gate'] or audit['model_ready'] != record['model_ready']:
                raise ValueError('Summary/report gate disagreement')
            if data_input_sha256(normalized, normalized['race_configs']) != audit['collected_audit']['input_sha256']:
                raise ValueError('Normalized input/audit hash mismatch')
            copied_audit = output / audit_path.name
            copied_normalized = output / normalized_path.name
            shutil.copyfile(audit_path, copied_audit)
            shutil.copyfile(normalized_path, copied_normalized)
            if sha(copied_audit) != sha(audit_path) or sha(copied_normalized) != sha(normalized_path):
                raise ValueError('Copied immutable race artifact SHA mismatch')
            races.append(dict(record, audit_path=str(copied_audit), audit_sha256=sha(copied_audit),
                              normalized_path=str(copied_normalized), normalized_sha256=sha(copied_normalized),
                              original_audit_path=str(audit_path), original_normalized_path=str(normalized_path)))
    expected = {p.stem for p in paths}
    if ids != expected:
        raise ValueError('Merged audit does not cover exactly the frozen input races')
    races.sort(key=lambda r:r['numeric_race_id'])
    write(output / 'full_summary.json', dict(race_count=36, batch_complete=True, independent_process_count=2,
        audited_at=stamp, source_manifest_sha256=sha(frozen_manifest), source_manifest_snapshots=manifest_sources,
        input_snapshot_path=str(output / 'input_snapshot.json'), input_snapshot_sha256=sha(output / 'input_snapshot.json'),
        source_reports=references, model_ready_race_ids=[r['race_id'] for r in races if r['model_ready']],
        races=races, approvals_used=[], model_calculations_executed=False))
    shutil.copyfile(output / 'full_summary.json', output / 'summary.json')
    if sha(output / 'summary.json') != sha(output / 'full_summary.json'):
        raise ValueError('Compatible summary copy SHA mismatch')
    print(json.dumps({'full_summary': str(output / 'full_summary.json'), 'races': 36,
                      'model_ready': sum(r['model_ready'] for r in races)}))


if __name__ == '__main__':
    main()
