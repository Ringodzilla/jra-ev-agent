#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi
BASELINE_JSON="${ROOT_DIR}/report/baseline_eval.json"
CANDIDATE_JSON="${ROOT_DIR}/report/candidate_eval.json"
INTEGRITY_JSON="${ROOT_DIR}/report/baseline_eval_integrity.json"
INPUT_PATH="${1:-${ROOT_DIR}/data/processed/race_last5.csv}"
RESULTS_PATH="${RESULTS_PATH:-}"
EVAL_MIN_EV="${EVAL_MIN_EV:-1.05}"
EVAL_MAX_BETS="${EVAL_MAX_BETS:-2}"
EVAL_STAKE="${EVAL_STAKE:-100}"
INTEGRITY_KEY_ENV="${INTEGRITY_KEY_ENV:-EVALUATION_INTEGRITY_KEY}"

if [[ -z "${!INTEGRITY_KEY_ENV:-}" ]]; then
  echo "A signing key is required in ${INTEGRITY_KEY_ENV}." >&2
  exit 1
fi

if [[ -z "${RESULTS_PATH}" ]]; then
  if [[ -f "$(dirname "${INPUT_PATH}")/results.csv" ]]; then
    RESULTS_PATH="$(dirname "${INPUT_PATH}")/results.csv"
  elif [[ -f "${ROOT_DIR}/tasks/horse_racing_ev/files/valid/results.csv" ]]; then
    RESULTS_PATH="${ROOT_DIR}/tasks/horse_racing_ev/files/valid/results.csv"
  fi
fi

RESULTS_ARGS=()
if [[ -n "${RESULTS_PATH}" ]]; then
  RESULTS_ARGS=(--results "${RESULTS_PATH}")
else
  echo "A labeled results file is required for integrity-checked evaluation." >&2
  exit 1
fi

EVAL_ARGS=(
  --min-ev "${EVAL_MIN_EV}"
  --max-bets-per-race "${EVAL_MAX_BETS}"
  --stake "${EVAL_STAKE}"
)
INTEGRITY_ARGS=(
  --file "input=${INPUT_PATH}"
  --file "results=${RESULTS_PATH}"
  --file "baseline=${BASELINE_JSON}"
  --file "evaluator=${ROOT_DIR}/scripts/evaluate_strategy.py"
  --file "leakage_guard=${ROOT_DIR}/scripts/check_feature_leakage.py"
  --file "integrity_guard=${ROOT_DIR}/scripts/check_evaluation_integrity.py"
  --file "candidate_promoter=${ROOT_DIR}/scripts/promote_evaluation_candidate.py"
  --file "experiment_runner=${ROOT_DIR}/scripts/run_codex_experiment.sh"
  --file "constitution=${ROOT_DIR}/CODEX_STRATEGY.md"
  --parameter "min_ev=${EVAL_MIN_EV}"
  --parameter "max_bets_per_race=${EVAL_MAX_BETS}"
  --parameter "stake=${EVAL_STAKE}"
  --key-env "${INTEGRITY_KEY_ENV}"
)

mkdir -p "${ROOT_DIR}/report" "${ROOT_DIR}/experiments"

echo "[0/4] leakage guard"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_feature_leakage.py"

if [[ ! -f "${BASELINE_JSON}" ]]; then
  echo "[1/4] baseline evaluation"
  "${PYTHON_BIN}" "${ROOT_DIR}/scripts/evaluate_strategy.py" \
    --input "${INPUT_PATH}" \
    "${RESULTS_ARGS[@]}" \
    "${EVAL_ARGS[@]}" \
    --out "${BASELINE_JSON}"
  "${PYTHON_BIN}" - "${BASELINE_JSON}" <<'PY'
import json
import sys

metrics = json.load(open(sys.argv[1], encoding="utf-8"))
if metrics.get("label_status") != "available":
    raise SystemExit("baseline result labels are unavailable")
if int(metrics.get("result_label_count", 0)) <= 0:
    raise SystemExit("baseline has no result labels")
PY
  "${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_evaluation_integrity.py" \
    --mode create \
    --manifest "${INTEGRITY_JSON}" \
    "${INTEGRITY_ARGS[@]}"
  echo "Baseline created at ${BASELINE_JSON}. Apply your patch, then rerun this script."
  exit 0
fi

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_evaluation_integrity.py" \
  --mode verify \
  --manifest "${INTEGRITY_JSON}" \
  "${INTEGRITY_ARGS[@]}"

PREVIOUS_MANIFEST_SHA256=$("${PYTHON_BIN}" - "${INTEGRITY_JSON}" <<'PY'
import hashlib
import sys
from pathlib import Path

print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

echo "[1/4] candidate evaluation"
EVAL_OUTPUT=$("${PYTHON_BIN}" "${ROOT_DIR}/scripts/evaluate_strategy.py" \
  --input "${INPUT_PATH}" \
  "${RESULTS_ARGS[@]}" \
  "${EVAL_ARGS[@]}" \
  --out "${CANDIDATE_JSON}" \
  --baseline-json "${BASELINE_JSON}" \
  --experiment-id "$(date -u +%Y-%m-%d_%H%M%S)" \
  --hypothesis "${HYPOTHESIS:-}" \
  --files-changed "${FILES_CHANGED:-}" \
  --log-dir "${ROOT_DIR}/experiments")

echo "${EVAL_OUTPUT}"

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_evaluation_integrity.py" \
  --mode verify \
  --manifest "${INTEGRITY_JSON}" \
  "${INTEGRITY_ARGS[@]}"

DECISION=$("${PYTHON_BIN}" "${ROOT_DIR}/scripts/promote_evaluation_candidate.py" \
  --reported-json "${EVAL_OUTPUT}" \
  --candidate "${CANDIDATE_JSON}" \
  --baseline "${BASELINE_JSON}")

if [[ "${DECISION}" == "keep" ]]; then
  "${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_evaluation_integrity.py" \
    --mode create \
    --replace \
    --expected-previous-manifest-sha256 "${PREVIOUS_MANIFEST_SHA256}" \
    --manifest "${INTEGRITY_JSON}" \
    "${INTEGRITY_ARGS[@]}"
  echo "[2/4] decision=keep -> baseline updated"
else
  echo "[2/4] decision=revert -> baseline unchanged"
fi

echo "[3/4] decision log written to experiments/*.json"
echo "[4/4] see report/candidate_eval.json and report/baseline_eval.json"
