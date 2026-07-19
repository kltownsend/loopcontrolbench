#!/usr/bin/env bash
# Batch-score MODEL across the whole task pool. Requires OPENAI_API_KEY (and MODEL,
# optional OPENAI_BASE_URL) in the environment. Results -> results/<MODEL>/<task>.json
cd "$(dirname "$0")/.." || exit 1
: "${MODEL:=gpt-5-mini}"; export MODEL
rm -rf "results/$MODEL"; mkdir -p "results/$MODEL"; rm -f "bench_done_${MODEL}"
ls tasks/*.json | grep -v _index | xargs -P 6 -n 1 tools/run_one_task.sh
touch "bench_done_${MODEL}"
echo "BENCH DONE: $(ls results/$MODEL/*.json 2>/dev/null | wc -l) results"
