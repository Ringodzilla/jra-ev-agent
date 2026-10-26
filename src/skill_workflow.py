"""Ordered, evidence-bearing gates for the public prediction entrypoints.

These execute deterministic checks, not an LLM or the Markdown instructions.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, DecimalException, localcontext
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
        self._outputs: dict[str, dict] = {}
        self._events: list[dict] = []
        self._blocked = False

    def _binding(self, output: dict) -> dict:
        from src.data_integrity import data_input_sha256
        from src.skill_stage_contracts import configurations, race_identity
        step = STEPS[len(self.records)][0] if len(self.records) < len(STEPS) else "COMPLETE"
        intake = output if step == "intake" else self._outputs.get("intake", {})
        configs = configurations(intake)
        collection = output if step == "collection" else self._outputs.get("collection")
        return {"run_id": self.directory.name, "step_id": step,
                "race_ids": sorted(race_identity(c) for c in configs),
                "config_sha256": digest(configs),
                "input_sha256": data_input_sha256(collection, configs) if collection is not None else digest(configs),
                "previous_event_sha256": self._previous["sha256"] if self._previous else None}

    def bind(self, output: dict) -> dict:
        """Attach current run/input identity; never overwrite another run's binding.

        This metadata is not evidence that a human or an LLM performed a skill.
        """
        if not isinstance(output, dict):
            return output
        copied = dict(output)
        if "_skill_binding" not in copied:
            try:
                copied["_skill_binding"] = self._binding(output)
            except (ValueError, TypeError, KeyError):
                pass  # record() persists the invalid input as a blocked attempt.
        return copied

    def record(self, step_id: str, output: dict, *, checks: dict[str, bool],
               failure_code: str = 'STAGE_VALIDATION_FAILED') -> dict:
        from src.skill_stage_contracts import stage_checks
        if self._blocked or any(r["status"] != "PASS" for r in self.records):
            raise SkillGateBlocked('terminal blocked sequence')
        index = len(self.records)
        if index >= len(STEPS):
            raise SkillGateBlocked(f'out of order step: {step_id}')
        expected, skill = STEPS[index]
        path = ROOT / 'skills' / skill / 'SKILL.md'
        try:
            content = path.read_bytes()
        except OSError:
            content = b""
        derived = {"sequence_order": expected == step_id,
                   "previous_evidence_intact": self.verify(),
                   "caller_checks_present": isinstance(checks, dict) and bool(checks),
                   "skill_contract_exists": bool(content),
                   "output_is_object": isinstance(output, dict)}
        try:
            digest(output)
            derived["payload_serializable"] = True
            derived["run_input_binding"] = isinstance(output, dict) and output.get("_skill_binding") == self._binding(output)
            if derived["sequence_order"] and isinstance(output, dict):
                derived.update(stage_checks(step_id, output, self._outputs, mode=self.mode))
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
            derived["payload_contract_valid"] = False
        caller = {"caller:" + str(key): value for key, value in checks.items()} if isinstance(checks, dict) else {}
        validation = {**caller, **{"contract:" + key: value for key, value in derived.items()}}
        success = all(value is True for value in validation.values())
        artifact = self.directory / f'{index + 1:02d}_{expected}_output.json'
        try:
            atomic_write_json(artifact, output if derived.get("payload_serializable") else {"invalid_payload": repr(output)})
        except (ValueError, TypeError):
            atomic_write_json(artifact, {"invalid_payload": repr(output)})
            success = False
        record = {
            'schema_version': 1, 'step_id': step_id, 'expected_step_id': expected, 'skill_name': skill,
            'run_id': self.directory.name, 'status': 'PASS' if success else 'BLOCKED',
            'execution_kind': 'deterministic_code_gate', 'mode': self.mode,
            'skill_sha256': hashlib.sha256(content).hexdigest(),
            'input_artifacts': [] if self._previous is None else [dict(self._previous)],
            'output_artifacts': [{'path': str(artifact.resolve()), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}],
            'checks': [{'id': key, 'status': 'PASS' if passed is True else 'FAIL'}
                       for key, passed in validation.items()],
            'reason_codes': [] if success else [failure_code],
            'next_action': STEPS[index + 1][0] if success and index + 1 < len(STEPS) else 'STOP',
        }
        event = self.directory / f'{index + 1:02d}_{expected}.json'
        atomic_write_json(event, record)
        self.records.append(record)
        self._previous = {'path': str(event.resolve()), 'sha256': hashlib.sha256(event.read_bytes()).hexdigest()}
        self._events.append(dict(self._previous))
        self._blocked = not success
        if success:
            self._outputs[step_id] = json.loads(artifact.read_text())
        atomic_write_json(self.directory / 'sequence.json', self.summary())
        if not success:
            raise SkillGateBlocked(f'{step_id}: {failure_code}')
        return record

    def verify(self) -> bool:
        try:
            for index, (record, event) in enumerate(zip(self.records, self._events)):
                expected, skill = STEPS[index]
                if (record.get("run_id") != self.directory.name or record.get("mode") != self.mode
                        or record.get("skill_name") != skill or record.get("expected_step_id") != expected
                        or record.get("status") not in {"PASS", "BLOCKED"}
                        or (record["status"] == "BLOCKED" and index != len(self.records) - 1)):
                    return False
                if record["status"] == "PASS" and record.get("step_id") != expected:
                    return False
                if hashlib.sha256((ROOT / 'skills' / skill / 'SKILL.md').read_bytes()).hexdigest() != record["skill_sha256"]:
                    return False
                refs = record.get('input_artifacts', []) + record.get('output_artifacts', []) + [event]
                expected_inputs = [] if index == 0 else [self._events[index - 1]]
                if record.get("input_artifacts") != expected_inputs or len(record.get("output_artifacts", [])) != 1:
                    return False
                for ref in refs:
                    path = Path(ref['path'])
                    if (path.resolve().parent != self.directory.resolve() or not path.is_file()
                            or hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']):
                        return False
                if json.loads(Path(event["path"]).read_text()) != record:
                    return False
                if record["status"] == "PASS" and self._outputs.get(expected) != json.loads(
                        Path(record["output_artifacts"][0]["path"]).read_text()):
                    return False
            return len(self.records) == len(self._events)
        except (OSError, ValueError, KeyError, TypeError):
            return False

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
        with localcontext() as context:
            context.prec = 50
            unit = Decimal("0.000001")
            epsilon = unit / 2
            valid = bool(rows)
            for row in rows:
                probability, odds, ev = (
                    Decimal(str(row[key])) for key in ("win_prob", "current_odds", "ev_current"))
                if not all(value.is_finite() for value in (probability, odds, ev)):
                    valid = False
                    break
                if not (0 <= probability <= 1 and odds > 0 and ev >= 0):
                    valid = False
                    break
                # src.model._fmt independently rounds p, odds and p*odds to six
                # decimals. Reject other precision rather than widening its contract.
                if any(value != value.quantize(unit) for value in (probability, odds, ev)):
                    valid = False
                    break
                # |E_saved - p_saved*o_saved| <= epsE + |o|epsP
                #     + |p|epsO + epsP*epsO. Decimal avoids binary boundary noise.
                bound = epsilon * (1 + abs(odds) + abs(probability)) + epsilon * epsilon
                if abs(ev - probability * odds) > bound:
                    valid = False
                    break
    except (ValueError, TypeError, KeyError, DecimalException):
        valid = False
    return {'ev_recalculation_matches': valid}


def attach_history_sources(collected: dict, raw_dir: Path) -> None:
    """Supply cached official HTML for actual reparse; never assert its validity."""
    from jra_scraper.scraper import safe_filename
    source_audit = dict(collected.get('source_audit') or {})
    artifacts = list(source_audit.get('artifacts') or [])
    cached = []
    seen = set()
    for race_id in sorted({str(row.get('race_id', '')) for row in collected.get('rows', [])}):
        path = raw_dir / f'race_{safe_filename(race_id)}.html'
        if path.is_file():
            html = path.read_text(encoding='utf-8')
            cached.append({'kind': 'race_detail', 'html': html,
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
            cached.append({'kind': 'horse_history', 'html': html,
                              'sha256': hashlib.sha256(html.encode()).hexdigest(),
                              'path': str(path.resolve()), 'race_id': key[0], 'horse_id': key[1],
                              'horse_name': row.get('horse_name', ''), 'horse_url': ''})
    for artifact in cached:
        if artifact not in artifacts:
            artifacts.append(artifact)
    collected['source_audit'] = dict(source_audit, artifacts=artifacts)
