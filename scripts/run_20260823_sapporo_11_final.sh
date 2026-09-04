#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

exec python3 "$repo_root/scripts/run_final_prediction.py" \
  --config-path "$repo_root/config/20260823_sapporo_11.json" \
  --baseline-path "$repo_root/data/collected/20260823_sapporo_11/race_last5.csv" \
  --fixed-analysis-dir "$repo_root/data/collected/20260823_sapporo_11" \
  --output-root "$repo_root/report/final_predictions" \
  --bankroll-per-race 1000 \
  --min-ev 1.03 \
  --mode balanced \
  "$@"
