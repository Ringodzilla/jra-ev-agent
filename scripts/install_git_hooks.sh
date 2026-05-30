#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p .git/hooks
cp scripts/hooks/pre-commit .git/hooks/pre-commit
cp scripts/hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-commit .git/hooks/pre-push

echo "Installed git hooks:"
echo "- .git/hooks/pre-commit"
echo "- .git/hooks/pre-push"
