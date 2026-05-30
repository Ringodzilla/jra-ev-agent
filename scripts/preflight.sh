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

echo "== Review Gate =="
echo "branch: $(git branch --show-current)"
echo "head: $(git rev-parse --short HEAD)"
echo

echo "== Working Tree =="
git status --short
echo

echo "== Diff Summary =="
git diff --stat
echo

echo "== Staged Diff Summary =="
git diff --cached --stat
echo

if [[ -f "data/processed/race_last5.csv" ]]; then
  echo "== Feature Leakage Check =="
  "$PYTHON_BIN" scripts/check_feature_leakage.py data/processed/race_last5.csv
  echo
else
  echo "== Feature Leakage Check =="
  echo "skipped: data/processed/race_last5.csv not found"
  echo
fi

echo "== Unit Tests =="
"$PYTHON_BIN" -m unittest discover -s tests -v
echo

echo "preflight: OK"
