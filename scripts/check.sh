#!/usr/bin/env bash
# Local quality gate (replaces CI; no GitHub Actions in this repo). Run before every push to main.
#   scripts/check.sh            all suites whose toolchain is installed
#   FAUXEL_TEST_ADMIN_URL=postgresql://fauxel@localhost:55432/postgres   enables the API's database tests
#   FAUXEL_CHECK_FULL=1         also runs the slow CV training test (needs torch)
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON:-python3}"; failed=()
run() { local name="$1"; shift; echo "== $name"; if ( "$@" ); then echo "   ok"; else echo "   FAILED"; failed+=("$name"); fi; }
has() { command -v "$1" >/dev/null 2>&1; }

run capture-ingestion bash -c "cd '$root/services/capture-ingestion' && $py -m pytest -q"
if [ -n "${FAUXEL_TEST_ADMIN_URL:-}" ]; then
  run api bash -c "cd '$root/services/api' && $py -m pytest -q tests"
else echo "== api: skipped (set FAUXEL_TEST_ADMIN_URL to a Postgres with postgis + vector)"; fi
run aps-forma bash -c "cd '$root/connectors/aps-forma' && $py -m pytest -q"
run openbim bash -c "cd '$root/connectors/ifc-speckle-bcf' && $py -m pytest -q"
[ -n "${FAUXEL_CHECK_FULL:-}" ] && run cv-inference bash -c "cd '$root/services/cv-inference' && $py -m pytest -q"
if has dotnet || [ -x "$HOME/.dotnet/dotnet" ]; then run connectors-dotnet bash -c "PATH=\$HOME/.dotnet:\$PATH bash '$root/connectors/build.sh'"; fi
if has npm; then
  run web-portal bash -c "cd '$root/apps/web-portal' && npm run -s typecheck && npx vitest run"
  run field-app bash -c "cd '$root/apps/field-app' && npx tsc --noEmit && npx jest --silent"
  run cloudflare-worker bash -c "cd '$root/infra/cloudflare/worker' && npx vitest run"
fi
has pwsh && run windows-scripts pwsh -NoProfile -Command '$e=$null; foreach($f in Get-ChildItem "'"$root"'/scripts/windows/*.ps1"){[System.Management.Automation.Language.Parser]::ParseFile($f.FullName,[ref]$null,[ref]$e)|Out-Null; if($e.Count){throw "$($f.Name): $($e[0])"}}'

echo; if [ ${#failed[@]} -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "FAILED: ${failed[*]}"; exit 1; fi
