"""Ordered, evidence-bearing gates for the public prediction entrypoints.

These execute deterministic checks, not an LLM or the Markdown instructions.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

from src.artifacts import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
STEPS = (
    ('intake', 'jra-01-intake'), ('collection', 'jra-02-official-collection'),
    ('integrity_audit', 'jra-03-integrity-audit'), ('approval_gate', 'jra-04-approval-gate'),
    ('analysis', 'jra-05-ability-analysis'), ('simulation', 'jra-06-pace-simulation'),
    ('probability', 'jra-07-probability-calculation'), ('ev', 'jra-08-ev-calculation'),
    ('tickets', 'jra-09-ticket-building'), ('review', 'jra-10-review'),
    ('decision', 'jra-11-final-decision'), ('report', 'jra-12-report'),
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


class SkillGateBlocked(ValueError):
    """A recorded failure; later prediction stages must not run."""


class SkillSequence:
    def __init__(self, output_root: Path, *, mode: str = 'prediction') -> None:
        if mode not in {'prediction', 'final'}:
            raise ValueError('unsupported prediction mode')
        validate_skill_contracts()
        self.mode = mode
        self.directory = output_root / 'skill_runs' / uuid4().hex
        self.records: list[dict] = []
        self._previous: dict | None = None

    def record(self, step_id: str, output: dict, *, checks: dict[str, bool],
               failure_code: str = 'STAGE_VALIDATION_FAILED') -> dict:
        index = len(self.records)
        if index >= len(STEPS) or STEPS[index][0] != step_id:
            raise SkillGateBlocked(f'out of order step: {step_id}')
        skill = STEPS[index][1]
        path = ROOT / 'skills' / skill / 'SKILL.md'
        # Missing instructions stop the public workflow rather than silently drifting.
        content = path.read_bytes()
        if self._previous and not self.verify():
            raise SkillGateBlocked('previous stage evidence changed')
        checks = dict(checks)
        checks['skill_contract_exists'] = bool(content)
        success = all(value is True for value in checks.values())
        artifact = self.directory / f'{index + 1:02d}_{step_id}_output.json'
        atomic_write_json(artifact, output)
        record = {
            'schema_version': 1, 'step_id': step_id, 'skill_name': skill,
            'status': 'PASS' if success else 'BLOCKED',
            'execution_kind': 'deterministic_code_gate', 'mode': self.mode,
            'skill_sha256': hashlib.sha256(content).hexdigest(),
            'input_artifacts': [] if self._previous is None else [self._previous],
            'output_artifacts': [{'path': str(artifact.resolve()), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}],
            'checks': [{'id': key, 'status': 'PASS' if passed is True else 'FAIL'}
                       for key, passed in checks.items()],
            'reason_codes': [] if success else [failure_code],
            'next_action': STEPS[index + 1][0] if success and index + 1 < len(STEPS) else 'STOP',
        }
        event = self.directory / f'{index + 1:02d}_{step_id}.json'
        atomic_write_json(event, record)
        self.records.append(record)
        self._previous = {'path': str(event.resolve()), 'sha256': hashlib.sha256(event.read_bytes()).hexdigest()}
        atomic_write_json(self.directory / 'sequence.json', self.summary())
        if not success:
            raise SkillGateBlocked(f'{step_id}: {failure_code}')
        return record

    def verify(self) -> bool:
        for record in self.records:
            for ref in record['input_artifacts'] + record['output_artifacts']:
                path = Path(ref['path'])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
                    return False
        return self._previous is None or hashlib.sha256(Path(self._previous['path']).read_bytes()).hexdigest() == self._previous['sha256']

    def summary(self) -> dict:
        return {'schema_version': 1, 'mode': self.mode, 'directory': str(self.directory.resolve()),
                'completed_steps': [r['step_id'] for r in self.records if r['status'] == 'PASS'],
                'blocked_step': next((r['step_id'] for r in self.records if r['status'] != 'PASS'), None),
                'not_executed': [step for step, _ in STEPS[len(self.records):]],
                'execution_kind': 'deterministic_code_gates_not_llm_skill_execution'}


def row_checks(rows: list[dict], previous: list[dict] | None = None) -> dict[str, bool]:
    def identities(items):
        return [(str(r.get('race_id', '')), str(r.get('horse_id') or r.get('horse_number') or '')) for r in items]
    ids = identities(rows)
    finite = all(not isinstance(v, float) or math.isfinite(v) for r in rows for v in r.values())
    return {'rows_present': bool(rows), 'identities_unique': len(ids) == len(set(ids)),
            'identities_present': all(all(i) for i in ids), 'numeric_values_finite': finite,
            'lineup_matches': previous is None or set(ids) == set(identities(previous))}


def validate_skill_contracts() -> None:
    """Keep documented skills and executable stage order from drifting apart."""
    manifest = json.loads((ROOT / 'config/skill_workflow.json').read_text())
    expected = [*STEPS, ('purchase', 'jra-13-purchase-record'), ('retrospective', 'jra-14-retrospective')]
    if manifest.get('schema_version') != 1 or [(s['step_id'], s['skill_name']) for s in manifest['steps']] != expected:
        raise ValueError('skill contract order mismatch')
    for mode in ('prediction', 'final'):
        if manifest['modes'].get(mode) != [step for step, _ in STEPS]:
            raise ValueError('skill mode order mismatch')
    for spec in manifest['steps']:
        path = ROOT / spec['path']
        if path != ROOT / 'skills' / spec['skill_name'] / 'SKILL.md' or not path.is_file():
            raise ValueError('missing or misplaced skill contract')


def probability_checks(rows: list[dict]) -> dict[str, bool]:
    groups: dict[str, list[float]] = {}
    try:
        for row in rows:
            value = float(row['win_prob'])
            if not math.isfinite(value) or not 0 <= value <= 1:
                return {'probabilities_valid': False}
            groups.setdefault(str(row['race_id']), []).append(value)
    except (ValueError, TypeError, KeyError):
        return {'probabilities_valid': False}
    return {**row_checks(rows), 'probabilities_normalized': bool(groups) and all(abs(sum(v) - 1) <= 3e-6 for v in groups.values())}


def ev_checks(rows: list[dict]) -> dict[str, bool]:
    try:
        valid = all(math.isfinite(float(r['ev_current'])) and float(r['current_odds']) > 0
                    and abs(float(r['ev_current']) - float(r['win_prob']) * float(r['current_odds'])) <= 2e-5 for r in rows)
    except (ValueError, TypeError, KeyError):
        valid = False
    return {'ev_recalculation_matches': bool(rows) and valid}


def attach_history_sources(collected: dict, raw_dir: Path) -> None:
    """Supply cached official HTML for actual reparse; never assert its validity."""
    from jra_scraper.scraper import safe_filename
    artifacts = []
    seen = set()
    for race_id in sorted({str(row.get('race_id', '')) for row in collected.get('rows', [])}):
        path = raw_dir / f'race_{safe_filename(race_id)}.html'
        if path.is_file():
            html = path.read_text(encoding='utf-8')
            artifacts.append({'kind': 'race_detail', 'html': html,
                              'sha256': hashlib.sha256(html.encode()).hexdigest(),
                              'path': str(path.resolve()), 'race_id': race_id})
    for row in collected.get('rows', []):
        key = (str(row.get('race_id', '')), str(row.get('horse_id', '')))
        if key in seen:
            continue
        seen.add(key)
        path = raw_dir / f'horse_{safe_filename(key[1])}.html'
        if path.is_file():
            html = path.read_text(encoding='utf-8')
            artifacts.append({'kind': 'horse_history', 'html': html,
                              'sha256': hashlib.sha256(html.encode()).hexdigest(),
                              'path': str(path.resolve()), 'race_id': key[0], 'horse_id': key[1],
                              'horse_name': row.get('horse_name', ''), 'horse_url': ''})
    collected['source_audit'] = {'artifacts': artifacts}
