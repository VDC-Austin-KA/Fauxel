#!/usr/bin/env bash
# Local replacement for CI: run every service's tests before pushing. No GitHub Actions in this repo.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
for svc in "$root"/services/*/; do
  [ -f "$svc/pyproject.toml" ] || continue
  echo "== $(basename "$svc")"
  (cd "$svc" && python3 -m pip install -q -e ".[test]" && python3 -m pytest -q)
done
