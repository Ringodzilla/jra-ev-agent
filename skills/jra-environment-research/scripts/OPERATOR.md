# Research chain interface

This is a research-only artifact chain. It does not execute the public prediction
workflow, authorize bets, or prove that a human/LLM performed a role. The latest
wrapper is `report/research/20261024_batch_20261024T225718Z/research_stage_wrapper_v2.py`.
Old score/fit scripts are not permitted research entrypoints.

Every JSON reference is exactly `{"path":"/absolute/path","sha256":"..."}`.
Every stage payload includes `run_id`, `stage` and the role from
`config/skill_workflow.json:research_branch`.

Create a run after code, skills and all evidence have stopped changing:

```sh
.venv/bin/python skills/jra-environment-research/scripts/research_run.py \
  --root /Users/konishi/Documents/Playground/projects/jra-ev-agent \
  --run-dir /absolute/new/research/run/skill_runs/new-id \
  --input /absolute/preregistered-plan.json \
  --input /absolute/verified-labels.json \
  --input /absolute/environment-observations.json \
  --input /absolute/replay-export/summary.json \
  --input /absolute/parallel-audit/input_snapshot.json \
  --input /absolute/frozen-source-manifest.json
```

Use actual existing paths. The command prints the generated `run_id` and initial
shared-state hashes. Preserve these in intake. The initial plan and its label and
environment references must also occur in the manifest inputs.

01 intake (orchestrator): `training_date="2026-10-24"`,
`validation_dates=["2026-10-25"]`, `family="linear"`, `ridge=100`,
`variant="exact_structural_softmax_temperature1_no_mc_no_market"`,
`production_model_changed=false`, `plan`, `labels`, `environment`,
`shared_initial` copied from the newly created manifest.

02 collection (data_collector): `audit_summary` references the fresh replay-export
summary; `audits` contains exactly all 36 fresh `*_audit.json` references;
`source_manifests` lists original raw-byte manifests. Each raw audit must have
`collected_artifact` and `normalized_artifact` references. The collected wrapper is
`{"schema_version":1,"record_type":"collected_raw_references","collected":{...}}`.
Within source artifacts, `raw_reference={path,encoding,bytes_sha256,html_utf8_sha256}`
replaces only `html`; all other original keys remain. The gate reconstructs the
exact HTML and verifies original bytes, decoded SHA, artifact SHA and complete
source-manifest membership, including the official result-header source.

03 integrity_audit (data_integrity_auditor): `audit_code_snapshot` references the
current parallel audit's `input_snapshot.json` with `code_files` map. `selected` is
exactly every raw-audit PASS/model_ready race, each
`{race_id,audit:<reference>,normalized:<reference>}`. Excluded whole-race reasons
remain in the complete 36-race audit summary. The gate replays current
`audit_collected_data` for all 36 races from fully restored sources, compares the
complete saved audit, and requires acquisition coverage PASS for every selected
race. It never trusts a saved PASS alone.

04 approval_gate (approval_gate role, authored independently by root):
`integrity_output` references this run's `03_output.json`;
`approved_imputations=[]`, `blocked_imputations=[]`, `approval_evidence=[]`,
`unknowns=[]`. This schema supports only the no-substitution branch. The root must
personally inspect all selected findings, required missing fields, source issues
and acquisition coverage before authoring this artifact. The gate checks the
artifact; it does not make or manufacture that judgment.

Record each role-authored artifact in sequence:

```sh
.venv/bin/python report/research/20261024_batch_20261024T225718Z/research_stage_wrapper_v2.py \
  --run-dir /absolute/run --stage approval_gate --role approval_gate \
  --payload /absolute/root-authored-stage04.json --evidence /absolute/additional-evidence.json
```

For 05 analysis, 06 simulation, 07 probability, fit, review or report, dispatch the
corresponding role only after root authorizes that real-data step. A dispatch JSON
is `{record_type:"orchestrator_role_dispatch",run_id,stage,role}`. It records the
orchestrator's scoped instruction; it is not user imputation approval.

```sh
.venv/bin/python report/research/20261024_batch_20261024T225718Z/research_stage_wrapper_v2.py \
  --run-dir /absolute/run --stage analysis --role analyzer \
  --produce --dispatch /absolute/orchestrator-dispatch.json
```

The producer first verifies the entire preceding chain, then calls only its
role's pure functions. Numeric stage validators independently recalculate scores,
scenarios and two-stage probabilities. Fit validates labels against original
result bytes, full horse sets and dated measurements, uses fixed linear/ridge100,
and checks unchanged shared state. Independent metrics and conditional-likelihood
gradient checks reject candidate or report tampering. Every accepted output is
stored within the run; BLOCKED is terminal and requires a new run.

Run artificial tests without real race calculations:

```sh
.venv/bin/python skills/jra-environment-research/scripts/test_research_gate.py
.venv/bin/python skills/jra-environment-research/scripts/test_research_numeric.py
```
