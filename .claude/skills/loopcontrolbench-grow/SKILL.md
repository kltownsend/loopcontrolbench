---
name: loopcontrolbench-grow
description: >-
  Build and certify loopcontrolbench tasks: mine merged bug-fix PRs, gate each in the scoring
  container, admit reproducers, drop-and-document the rest. Use when you want to grow the pool, add
  tasks, mine a project, create or re-certify the bench, audit fixtures, or drop broken tasks. This
  is the CREATE side; scoring a model is loopcontrolbench-run. Triggers: "grow the pool", "add
  tasks / a project", "mine <owner/repo>", "certify / re-certify the pool", "audit the fixtures",
  "drop the broken tasks", "build the bench".
---

# loopcontrolbench — build and certify the pool

The repeatable recipe for growing the task pool without letting rot back in. Run from the
repository root. Companion skill `loopcontrolbench-run` scores a model against what this skill
admits.

**The pool is the ceiling.** A model score is only as honest as the tasks behind it. This skill's
whole job is to keep every admitted task (1) a real, reproducible bug with (2) the maintainer's
own test that decides done. The deterministic-validator discipline applied to the fixtures
themselves.

## Two rules that prevent every past failure

1. **Ingest, never infer.** A task is the real PR, verbatim. Buggy state = the repo at the merge
   parent. Test = the maintainer's own test from the fix commit. Canonical fix = the merged source
   diff. No snippets, no paraphrase, no reconstructed test. Inferring a test where a fix commit
   shipped none is exactly how invalid fixtures get in; inference is the rot. If a PR carries no
   co-located test, drop it.
2. **Pin the environment, not the PR.** One modern common environment is the constant
   (`loopcontrolbench-base`, Python 3.13). PRs are the growing, replaceable axis. A bug that only
   reproduced on an old interpreter fails the gate automatically (the buggy code passes on modern
   Python) and drops for free, no human ruling, no per-era environment museum. This is what keeps
   the bench forkable: mechanical curation, no intellectual dependency on any maintainer.

## Certify in the scoring container (non-negotiable)

The gate and the scorer must run in the **same** image, or the pool is certified against one
environment and scored in another. That gap silently contaminates results: a host that happens to
have compatible dependencies will admit tasks the scoring container cannot collect. The default
closes it: `tools/gate_sandboxed.py` certifies inside `loopcontrolbench-base`. `tools/gate.py` is
the same logic as a readable host reference, not for real certification.

Build the image first if needed: `docker build -t loopcontrolbench-base tools/`.

Worked example of a correct drop: `psf/requests` task-r030 was retired at the gate because its
test harness (`pytest-httpbin` -> `httpbin`) imports a `werkzeug` symbol removed years ago, so the
suite will not collect on modern Python. It lives in `tasks/_dropped/` with that reason.

## The gate (admission == fidelity, one check)

For a candidate `{repo, base_sha, fix_sha, source_files, test_files}`, inside the container:

1. Check out the parent (buggy), overlay the fix commit's real test, run it: it must **fail**.
2. Apply the fix commit's real source, run again: it must **pass**.
3. `fail_to_pass` = the tests that flip fail-to-pass. Non-empty => reproduces => **admit**. Empty
   => **drop** (`collection_failed` if nothing collected, else `no_reproduce_on_env`).

`tools/gate_sandboxed.py tasks/<task>.json` runs this for one spec. It reports `reproduce`,
`fail_to_pass`, `n_collected`, and any `drop` reason.

## Mine to grow

```bash
python3 tools/mine.py owner/repo <limit>     # e.g. python3 tools/mine.py psf/requests 12
```

`mine.py` finds merged bug-fix PRs, builds candidate specs (base = merge parent, fix = merge
commit, verbatim), certifies each through the container gate, and writes the admitted specs to
`mined_<repo>.json`. It needs `gh` auth, Docker, and the image. Then promote admitted specs into
`tasks/` and rebuild the index.

## Admit, drop, index

- **Admit:** write each admitted spec as `tasks/<task_id>.json` with the full schema below, then
  rebuild `tasks/_index.json` from the `tasks/*.json` files (excluding `_index.json`).
- **Drop and document:** a non-reproducing or env-incompatible task is moved to
  `tasks/_dropped/<task_id>.json` with a `dropped` block (`at`, `reason`, `detail`). An exclusion
  is a visible admission decision, never a silent hole.
- **Re-certify** when the image or environment changes: run `gate_sandboxed.py` across the whole
  pool (parallel via `run_one_gate.sh`), then admit/drop by the results. Keep `gate_results/` out
  of git.

## Task spec schema

```json
{
  "task_id":     "requests-pr1234",
  "repo":        "psf/requests",
  "base_sha":    "<merge parent, the buggy state>",
  "fix_sha":     "<merge commit, carries source + test>",
  "source_files":["src/requests/models.py"],
  "test_files":  ["tests/test_requests.py"],
  "fail_to_pass":["tests/test_requests.py::test_name"],
  "source_pr":   "<PR number or URL>"
}
```

No code is stored; the repo is checked out from the pinned commits at run time. `fail_to_pass` is
filled by the gate, not by hand.

## Contribution and quality rules

The gate makes *admission* mechanical, so contributions cannot bypass fidelity. It does not, by
itself, protect *corpus quality*; that is governed:

- one task per genuine bug; deduplicate against existing patterns.
- reasonable repo and task size (no denial-of-service repos).
- source project carries a recognized open-source license.
- disclose any task drawn from a repo you author or maintain, and any relationship with a project
  under test.
- a cap per source repo, to avoid concentration.
- no benchmark-targeted commits; selection happens before the task is scored against any model
  whose number will be reported; disclose prior runs.
- include provenance (repo, PR, commits) in the spec.

## Guardrails (non-negotiable)

- **Ingest, never infer.** Verbatim PR only. No co-located test => drop.
- **Certify in the scoring image.** The gate runs in `loopcontrolbench-base`, same as the scorer.
  Never re-open the env gap by gating on the host.
- **Drop and document.** Non-reproducing tasks go to `tasks/_dropped/` with a reason.
- **Mechanical curation = forkable.** Keep the gate a script a fork can run, never a taste. The
  instrument outlives its maintainer.

## Files

- `tools/gate_sandboxed.py` — the default in-container gate. `tools/gate.py` — readable host
  reference of the same logic (not for real certification).
- `tools/mine.py` — the growth engine (mine PRs -> container gate -> admit).
- `tools/run_one_gate.sh` — per-task gate wrapper for parallel re-certification.
- `tools/Dockerfile` — the certified base image (`python:3.13-slim` + git + build-essential).
- `tasks/*.json` + `tasks/_index.json` — the certified pool. `tasks/_dropped/` — documented drops.
- `mined_<repo>.json`, `gate_results/` — mining and re-certification artifacts (gitignored).
