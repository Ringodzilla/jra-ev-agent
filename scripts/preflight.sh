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

if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  if [[ "$PYTHON_BIN" != "python3" ]] && python3 -m pytest --version >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "pytest is required. Install dependencies with: $PYTHON_BIN -m pip install -r requirements.txt" >&2
    exit 1
  fi
fi

if ! "$PYTHON_BIN" -m coverage --version >/dev/null 2>&1; then
  if [[ "$PYTHON_BIN" != "python3" ]] \
    && python3 -m pytest --version >/dev/null 2>&1 \
    && python3 -m coverage --version >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "coverage is required. Install dependencies with: $PYTHON_BIN -m pip install -r requirements.txt" >&2
    exit 1
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

echo "== Feature Leakage Check =="
"$PYTHON_BIN" scripts/check_feature_leakage.py
echo

echo "== Unit Tests and Coverage =="
"$PYTHON_BIN" -m coverage run -m pytest -q
"$PYTHON_BIN" -m coverage report
echo

echo "preflight: OK"
