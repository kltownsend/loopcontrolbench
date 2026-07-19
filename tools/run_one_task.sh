#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
spec="$1"; tid=$(basename "$spec" .json)
mkdir -p "results/$MODEL"
res=$(timeout 600 .venv/bin/python tools/run_sandboxed.py "$spec" 2>/dev/null)
[ -z "$res" ] && res="{\"task_id\":\"$tid\",\"error\":\"run_crash\"}"
printf '%s\n' "$res" > "results/$MODEL/$tid.json"
echo "done $tid"
