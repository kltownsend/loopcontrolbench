#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
spec="$1"; tid=$(basename "$spec" .json); mkdir -p gate_results
res=$(timeout 900 .venv/bin/python tools/gate_sandboxed.py "$spec" 2>/dev/null)
[ -z "$res" ] && res="{\"task_id\":\"$tid\",\"drop\":\"gate_crash\"}"
printf '%s\n' "$res" > "gate_results/$tid.json"; echo "gated $tid"
