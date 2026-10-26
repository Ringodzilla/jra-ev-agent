"""Independent attacks against caller assertions and run identity boundaries."""
import copy
from pathlib import Path

import pytest

from src.skill_workflow import STEPS, SkillGateBlocked, SkillSequence


def _intake():
    return {"race_configs": [{"race_id": "20261025_京都_11", "race_date": "2026-10-25",
                              "track": "京都", "race_number": 11,
                              "source_url": "https://www.jra.go.jp/JRADB/accessD.html"}]}


@pytest.mark.parametrize("payload", [{}, {"status": "PASS"}, {"race_configs": []}])
def test_caller_true_cannot_replace_intake_evidence(tmp_path, payload):
    sequence = SkillSequence(tmp_path)
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", sequence.bind(payload), checks={"all_done": True})
    assert sequence.summary()["blocked_step"] == "intake"


@pytest.mark.parametrize("checks", [{}, {"claimed": 1}, {"claimed": "PASS"}, {"claimed": False}])
def test_missing_or_truthy_caller_assertions_cannot_pass(tmp_path, checks):
    sequence = SkillSequence(tmp_path)
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", sequence.bind(_intake()), checks=checks)


def test_failed_stage_is_terminal_even_after_exception_is_caught(tmp_path):
    sequence = SkillSequence(tmp_path)
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", sequence.bind({}), checks={"claim": True})
    with pytest.raises(SkillGateBlocked, match="terminal"):
        sequence.record("collection", sequence.bind({}), checks={"claim": True})
    assert len(sequence.records) == 1
    assert "collection" not in sequence.summary()["completed_steps"]


def test_same_race_other_run_binding_cannot_be_rebound(tmp_path):
    first, second = SkillSequence(tmp_path / "a"), SkillSequence(tmp_path / "b")
    foreign = first.bind(_intake())
    assert second.bind(foreign)["_skill_binding"] == foreign["_skill_binding"]
    with pytest.raises(SkillGateBlocked):
        second.record("intake", second.bind(foreign), checks={"claim": True})


def test_bound_config_edit_is_not_a_new_valid_input(tmp_path):
    sequence = SkillSequence(tmp_path)
    payload = copy.deepcopy(sequence.bind(_intake()))
    payload["race_configs"][0]["race_number"] = 10
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", payload, checks={"claim": True})


def test_empty_collection_cannot_pass_after_real_intake(tmp_path):
    sequence = SkillSequence(tmp_path)
    sequence.record("intake", sequence.bind(_intake()), checks={"claim": True})
    with pytest.raises(SkillGateBlocked):
        sequence.record("collection", sequence.bind({"status": "PASS"}), checks={"claim": True})
    assert sequence.summary()["completed_steps"] == ["intake"]


@pytest.mark.parametrize("step", [step for step, _ in STEPS])
def test_every_stage_rejects_foreign_binding_despite_valid_payload(tmp_path, step):
    from test_skill_stage_contracts import run_until, stage_payloads

    payloads = stage_payloads()
    current = SkillSequence(tmp_path / "current", mode="final")
    foreign = SkillSequence(tmp_path / "foreign", mode="final")
    run_until(current, payloads, step)
    run_until(foreign, payloads, step)
    with pytest.raises(SkillGateBlocked):
        current.record(step, current.bind(foreign.bind(payloads[step])), checks={"ok": True})


def test_caller_cannot_shadow_failed_internal_contract_name(tmp_path):
    sequence = SkillSequence(tmp_path)
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", sequence.bind({}),
                        checks={"configs_complete": True, "contract:configs_complete": True})


@pytest.mark.parametrize("mutation", ["records", "outputs"])
def test_mutable_in_memory_state_cannot_rewrite_recorded_evidence(tmp_path, mutation):
    from test_skill_stage_contracts import run_until, stage_payloads

    sequence = SkillSequence(tmp_path, mode="final")
    payloads = stage_payloads()
    run_until(sequence, payloads, "collection")
    if mutation == "records":
        sequence.records[0]["checks"] = [{"id": "invented", "status": "PASS"}]
    else:
        sequence._outputs["intake"]["race_configs"][0]["race_number"] = 12
    with pytest.raises(SkillGateBlocked):
        sequence.record("collection", sequence.bind(payloads["collection"]), checks={"ok": True})


def test_unpromoted_environment_cannot_smuggle_nonzero_adjustment(tmp_path):
    from test_skill_stage_contracts import run_until, stage_payloads

    payloads = stage_payloads()
    sequence = SkillSequence(tmp_path, mode="final")
    run_until(sequence, payloads, "analysis")
    payloads["analysis"]["feature_rows"][0]["environment_score"] = 100
    with pytest.raises(SkillGateBlocked):
        sequence.record("analysis", sequence.bind(payloads["analysis"]), checks={"ok": True})


def test_false_final_gate_cannot_be_relabelled_as_go(tmp_path):
    from src.skill_stage_contracts import stage_checks

    ticket = {"horse_number": "1", "stake": 100, "hit_prob": 1}
    checks = {name: True for name in ["deadline", "final_go_window_open", "odds_fresh",
              "conditions_fresh", "combination_coverage", "tickets_use_jra_live_odds",
              "quantitative_review", "snapshot_complete"]}
    checks["final_go_window_open"] = False
    prior = {"tickets": {"tickets": [ticket]}, "review": {
        "status": "OK", "decision": "GO", "checks": checks}}
    result = stage_checks("decision", {"decision": "GO", "tickets": [ticket]}, prior, mode="final")
    assert result["go_requires_positive_review"] is False


def test_non_object_and_unserializable_payloads_are_persisted_as_blocked(tmp_path):
    for number, payload in enumerate([[], {"race_configs": [{"bad": object()}]}]):
        sequence = SkillSequence(tmp_path / str(number))
        bound = sequence.bind(payload)
        with pytest.raises(SkillGateBlocked):
            sequence.record("intake", bound, checks={"ok": True})
        assert sequence.records[-1]["status"] == "BLOCKED"
        assert Path(sequence.records[-1]["output_artifacts"][0]["path"]).is_file()


def test_missing_skill_between_initialization_and_record_blocks(tmp_path, monkeypatch):
    import src.skill_workflow as module

    sequence = SkillSequence(tmp_path)
    original = Path.read_bytes
    missing = module.ROOT / "skills/jra-01-intake/SKILL.md"

    def read(path):
        if path == missing:
            raise FileNotFoundError(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", sequence.bind(_intake()), checks={"ok": True})
    assert any(c["id"] == "contract:skill_contract_exists" and c["status"] == "FAIL"
               for c in sequence.records[-1]["checks"])


def test_serialization_failure_after_validation_never_records_pass(tmp_path, monkeypatch):
    import src.skill_workflow as module

    sequence = SkillSequence(tmp_path)
    original = module.atomic_write_json
    attempted = []

    def write(path, value):
        if path.name.endswith("_output.json") and not attempted:
            attempted.append(path)
            raise TypeError("serialization failed after validation")
        return original(path, value)

    monkeypatch.setattr(module, "atomic_write_json", write)
    with pytest.raises(SkillGateBlocked):
        sequence.record("intake", sequence.bind(_intake()), checks={"ok": True})
    assert sequence.records[-1]["status"] == "BLOCKED"


@pytest.mark.parametrize("attack", ["owner", "step", "skill_hash", "input_refs", "malformed_ref"])
def test_verification_rejects_specific_evidence_corruption(tmp_path, attack):
    sequence = SkillSequence(tmp_path)
    sequence.record("intake", sequence.bind(_intake()), checks={"ok": True})
    record = sequence.records[0]
    if attack == "owner":
        record["run_id"] = "foreign"
    elif attack == "step":
        record["step_id"] = "collection"
    elif attack == "skill_hash":
        record["skill_sha256"] = "0" * 64
    elif attack == "input_refs":
        record["input_artifacts"] = [dict(sequence._events[0])]
    else:
        record["output_artifacts"] = [{}]
    assert sequence.verify() is False
