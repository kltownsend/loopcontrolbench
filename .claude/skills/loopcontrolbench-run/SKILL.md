---
name: loopcontrolbench-run
description: >-
  Score a model against loopcontrolbench and report the number honestly. Use when you want to run
  the bench, benchmark a model behind the gate, get or refresh the reference number, try a local
  (vLLM/Ollama) or hosted model on the pool, or update reference/<model>.json. This is the RUN
  side: a certified pool already exists; you are scoring against it, not building it (that is
  loopcontrolbench-grow). Triggers: "run the bench", "score <model>", "what does <model> get",
  "re-run the reference", "benchmark behind the gate", "run loopcontrolbench".
---

# loopcontrolbench — run a model behind the gate

The repeatable recipe for scoring a model against the certified task pool. Run from the repository
root. `README.md` is the reference; this file is the operating how. Companion skill
`loopcontrolbench-grow` builds and certifies the pool.

**The one measurement.** How much of this pool does a model clear on a single attempt when a
deterministic test, not the model, decides done? The validator determines done, not the loop.

## Prerequisites (once)

1. **Docker running**, and the sandbox image built:
   ```
   docker build -t loopcontrolbench-base tools/
   ```
   The image is `python:3.13-slim` + `git` + `build-essential`. If a "hung" build sits for
   minutes, it is likely pulling a full base image invisibly; the Dockerfile builds from the slim
   image, which is fast.
2. **Controller venv** (holds the OpenAI client, host-side only):
   ```
   python3.13 -m venv .venv && .venv/bin/pip install openai
   ```
3. **Credentials in the shell, never in the container.** Export `OPENAI_API_KEY` however you
   manage secrets. Set `MODEL`, and `OPENAI_BASE_URL` for a non-OpenAI endpoint (vLLM, Ollama, any
   OpenAI-compatible server).

## Run it (sandboxed, always)

The default runner is `tools/run_sandboxed.py` (via `tools/bench.sh`). Untrusted repo install and
tests run in a disposable container with no host env, no host mounts beyond the per-task work dir,
no Docker socket, network off during tests, resource limits, `--rm` teardown. **Never** point the
unsandboxed `tools/run.py` at the pool for a real run; it exists only for host-side development on
the runner itself. This bench executes third-party code by design (see the README security section).

```bash
export OPENAI_API_KEY=sk-...
export MODEL=gpt-5-mini                 # or a local model id
# export OPENAI_BASE_URL=http://host:8000/v1   # a local OpenAI-compatible endpoint
./tools/bench.sh                        # -> results/<MODEL>/<task>.json, one per task
```

- Concurrency is `xargs -P 6` in `bench.sh`. A small local model can go higher; bump `-P`.
- A single task, for a spot check: `.venv/bin/python tools/run_sandboxed.py tasks/<task>.json`.
- macOS caveat: batch with the per-task script (`run_one_task.sh` + `xargs -P N -n1`), never
  `xargs -I{}` (the replacement blows the command-line length limit and nothing runs).

## Aggregate and classify (the honest denominator)

Every scored task resolves to exactly one outcome. Because the pool is certified collectable in
the scoring image (grow certifies in-container), the taxonomy has no run-time infra escape:

- **solved** — edit applied and every `fail_to_pass` test passes.
- **model miss** — edit applied cleanly but a target test still fails. **A collection failure
  during scoring is a miss**, not an exclusion: the pool is certified to collect, so a broken
  collection is the model's edit.
- **invalid edit** — unparseable, search text did not match, or targeted a non-allowlisted path.
  Not solved, stays in the denominator.
- **model timeout / API error** — no usable response in budget. Not solved, in the denominator
  (a hanging model earns no cleaner denominator than a bad edit).
- **excluded at certification only** — dropped at the gate, before scoring, recorded in
  `tasks/_dropped/`. Never mid-run. `install_failed` is the only run-time exclusion.

Aggregate by reading `results/<MODEL>/*.json`, join `task_id -> repo` via `tasks/_index.json`,
count `solved` over scored, and break down per repo.

## Report it (the discipline)

- State the number as **"X of N on this certified pool, one attempt, behind the gate, in the
  `loopcontrolbench-base` (Python 3.13) container."** Never "X% of software bugs." The population
  is reproducible, maintainer-tested Python library bug fixes that reproduce on a modern env.
- Cite the denominator and the outcome counts (solved / misses / any exclusions).
- Land on the verdict, scoped. Whether a pricier tier improves *coverage* or only *throughput* is
  a question the harness can measure, not a conclusion a single run settles.
- This is the instrument, not the judgment. How a score becomes an assessment is out of scope here.

## Update the committed reference

`reference/<model>.json` is the auditable-without-rerunning record. Regenerate it from
`results/<MODEL>/` with: `model`, `env`, `attempts`, `sandboxed`, `pool_certified_in_image`,
`solved`, `total`, `pass_rate`, a `by_repo` map, a `dropped_at_gate` list (mirrors
`tasks/_dropped/`), and a `results` list of `{task_id, repo, solved, still_failing}`. Then update
the README reference number, the per-repo table, and any prose citing the figure so all three stay
consistent. The current reference is `gpt-5-mini = 46/70 = 65.7%`.

## Local / reasoning models

- Point `OPENAI_BASE_URL` at your server (vLLM, Ollama). Bench work is CPU + disk on the host plus
  containers; the GPU work is the model serving.
- For a reasoning model, enable its reasoning. That is the lever, not temperature. Model variance
  is expected and is not a defect of the measurement: the validator, not the model, is the
  deterministic part.
- Small-context models choke on the current whole-file prompt (`run.py` puts the entire source
  file in the prompt; real files reach ~170KB). A relevant-slice input mode is the fix; until it
  lands, expect large-file tasks to fail on context, and say so rather than scoring a clean miss.

## Guardrails (non-negotiable)

- **Sandbox always.** Real runs go through `run_sandboxed.py`. The key never enters the container.
- **The validator determines done.** No LLM judge, no rubric. A collection failure or a
  skipped/xfailed target is not a pass.
- **Honest denominator.** Report the pool, the attempt policy, and the environment. Never inflate
  to "bugs in general." Exclusions are visible admission decisions, not silent holes.
- **Do not conflate with judgment.** The number is a measurement, not a verdict on a product.

## Files

- `tools/bench.sh` — batch scorer over the whole pool. `tools/run_one_task.sh` — per-task wrapper.
- `tools/run_sandboxed.py` — the default sandboxed runner (host does git + model call; container
  installs and tests). `tools/run.py` — the same scoring logic, host-side, dev only.
- `tasks/*.json` + `tasks/_index.json` — the certified pool. `tasks/_dropped/` — certification drops.
- `results/<MODEL>/<task>.json` — per-task outcomes (gitignored). `reference/<model>.json` — the
  committed reference record.
- `README.md` — security + outcomes/denominator + boundary.
