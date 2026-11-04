#!/usr/bin/env bash
# Runs every WorldKit test suite and prints one summary line per suite (Git Bash on Windows, Linux, macOS).
#   bash scripts/test_all.sh          # offline suites
#   bash scripts/test_all.sh --live   # also the real-Supabase test and the live-site check
# Needs: pipeline/.venv (Python 3.10) and `npm install` in viewer/tests and web. Three browser suites (viewer, integration, sam3d)
# make one real Muse Spark call each and need MODEL_API_KEY in pipeline/.env; without it they stop at that step.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/pipeline/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY="$ROOT/pipeline/.venv/bin/python"
SUMMARY=""; FAILED=0

run() {  # run NAME DIR CMD...
  local name="$1" dir="$2"; shift 2
  echo "== $name"
  local out code
  out="$(cd "$dir" && "$@" 2>&1)"; code=$?
  local line; line="$(echo "$out" | grep -E "passed|checks passed|^# (pass|fail) " | tail -2 | tr '\n' ';')"
  [ $code -ne 0 ] && { echo "$out" | grep -E "^FAIL|CRASH|Error" | head -5; FAILED=1; }
  SUMMARY+="$( [ $code -eq 0 ] && echo PASS || echo FAIL )  $name  $line"$'\n'
}

run "pytest" "$ROOT" "$PY" -m pytest pipeline/tests -q -p no:cacheprovider

"$PY" "$ROOT/viewer/server.py" >/dev/null 2>&1 &
SERVER=$!
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:5050/ && break; sleep 1; done
for t in browser_test integration_test sam3d_browser_test scene_physics_browser_test demo_readiness_test ai_edit_browser_test map_scene_browser_test parts_browser_test replay_browser_test; do
  run "viewer/$t" "$ROOT/viewer/tests" node "$t.js"
done
kill $SERVER 2>/dev/null

run "web unit" "$ROOT/web" node --test tests/lib.test.js
run "web e2e (mocked Supabase)" "$ROOT" node web/tests/e2e_mock.mjs
if [ "${1:-}" = "--live" ]; then
  run "web e2e (real Supabase)" "$ROOT" node web/tests/e2e_real.mjs
  run "live site (check_live)" "$ROOT" node scripts/check_live.mjs
fi

echo; printf "%s" "$SUMMARY"
exit $FAILED
