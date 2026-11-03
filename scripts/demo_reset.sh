#!/usr/bin/env bash
# Resets the live demo: a clean copy of the mug run at v0, the edit server and the scene viewer started, the browser opened.
#   bash scripts/demo_reset.sh [SOURCE_RUN] [--no-browser]
# Object + AI Edit:  http://127.0.0.1:5070/?demo=1   (worldkit.serve on runs/demo_live; needs MODEL_API_KEY in pipeline/.env)
# Map scenes:        http://127.0.0.1:5050/?demo=1   (viewer/server.py)
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/pipeline/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY="$ROOT/pipeline/.venv/bin/python"
SRC="${1:-}"; [ "$SRC" = "--no-browser" ] && SRC=""
if [ -z "$SRC" ]; then SRC="$ROOT/pipeline/runs/mug_grounded"; [ -f "$SRC/model_print.stl" ] || SRC="$ROOT/viewer/assets/demo/run_mug"; fi
LIVE="$ROOT/pipeline/runs/demo_live"

pkill -f "worldkit.serve" 2>/dev/null || true
pkill -f "viewer/server.py" 2>/dev/null || true

rm -rf "$LIVE"; mkdir -p "$LIVE"
cp "$SRC/model.glb" "$SRC/model_print.stl" "$SRC/report.json" "$LIVE/"
[ -d "$SRC/parts" ] && cp -r "$SRC/parts" "$LIVE/"
echo "demo_live reset to v0 from $SRC"

(cd "$ROOT/pipeline" && "$PY" -m worldkit.serve "$LIVE" --port 5070 >/dev/null 2>&1 &)
(cd "$ROOT" && "$PY" viewer/server.py >/dev/null 2>&1 &)
for port in 5070 5050; do for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$port/api/health" && break; sleep 0.5; done; done
echo "edit server http://127.0.0.1:5070/?demo=1   scene viewer http://127.0.0.1:5050/?demo=1"
case " $* " in *" --no-browser "*) ;; *) (command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:5070/?demo=1") || (command -v open >/dev/null && open "http://127.0.0.1:5070/?demo=1") || start "http://127.0.0.1:5070/?demo=1" 2>/dev/null || true ;; esac
