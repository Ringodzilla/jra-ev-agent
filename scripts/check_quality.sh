#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

RUFF_COMMAND=("$PYTHON_BIN" -m ruff)
if ! "${RUFF_COMMAND[@]}" --version >/dev/null 2>&1; then
  if [[ -x ".venv/bin/ruff" ]]; then
    RUFF_COMMAND=(".venv/bin/ruff")
  elif command -v ruff >/dev/null 2>&1; then
    RUFF_COMMAND=("$(command -v ruff)")
  else
    echo "ruff is required. Install dependencies with: $PYTHON_BIN -m pip install -r requirements.txt" >&2
    exit 1
  fi
fi

echo "== Ruff =="
"${RUFF_COMMAND[@]}" check \
  agent.py analysis jra_scraper report/note.py scripts src strategy tests
echo

echo "== Architecture Boundaries =="
"$PYTHON_BIN" scripts/check_architecture.py
echo

echo "== Feature Leakage =="
"$PYTHON_BIN" scripts/check_feature_leakage.py
echo

echo "quality checks: OK"
