#!/usr/bin/env bash
# Batch-score MODEL across the whole task pool. Requires OPENAI_API_KEY (and MODEL,
# optional OPENAI_BASE_URL) in the environment. Results -> results/<MODEL>/<task>.json
cd "$(dirname "$0")/.." || exit 1
: "${MODEL:=gpt-5-mini}"; export MODEL
: "${PAR:=6}"                                       # concurrency; lower for a big local model
rm -rf "results/$MODEL"; mkdir -p "results/$MODEL"; rm -f "bench_done_${MODEL}"
.venv/bin/python tools/run_sandboxed.py --setup    # start the egress proxy once, before fan-out
ls tasks/*.json | grep -v _index | xargs -P "$PAR" -n 1 tools/run_one_task.sh
.venv/bin/python tools/run_sandboxed.py --teardown # remove the proxy and its networks
touch "bench_done_${MODEL}"
echo "BENCH DONE: $(ls results/$MODEL/*.json 2>/dev/null | wc -l) results"
