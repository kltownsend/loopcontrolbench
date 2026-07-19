# loopcontrolbench

**How much reproducible, maintainer-tested Python library bug-fixing can a model do on its own, when a deterministic test, not the model, decides whether it is done?**

loopcontrolbench scores coding agents against real merged bug-fix pull requests, gated by each project's own tests. Every task is a real PR: the buggy code at the parent commit, the maintainer's own test, and the merged fix. A model is scored only on whether the named failing test flips to passing. No LLM judges, no rubrics, no hand-written fixtures, nothing inferred.

The population is specific and worth stating up front: **reproducible, maintainer-tested Python library bug fixes that reproduce on a modern shared environment.** That is a valuable slice of software maintenance, not a neutral sample of it. See [What this does not claim](#what-this-does-not-claim).

## ⚠️ Security: this runs untrusted code

loopcontrolbench clones arbitrary third-party repositories and executes their build system, package installation hooks, test dependencies, and test suites. **That is remote code execution by design.** The repository, its `setup.py`/`pyproject`, its dependencies, and its tests must all be treated as untrusted.

Run it **only inside a disposable, isolated VM or container** with:

- no cloud credentials, API keys, SSH keys, or tokens in the environment
- no host home directory or sensitive paths mounted
- no Docker socket
- no access to internal networks
- CPU, memory, and wall-clock limits, and explicit teardown after each task

The default runner (`tools/run_sandboxed.py`, which `bench.sh` uses) enforces this. Each task's install and tests run in a disposable container (`loopcontrolbench-base`, built from `python:3.13-slim` plus `git` and a compiler) that inherits **none** of your environment (no API keys, no credentials, no tokens), mounts only the per-task work directory (no home directory, no SSH or cloud config, no Docker socket), runs the tests with the **network disabled**, is held to memory, CPU, and PID limits, and is torn down after every task. The host does only git and the model call, so the key never enters the container. Docker is required for this path; build the image once with `docker build -t loopcontrolbench-base tools/`.

Egress is locked down in both untrusted phases. Tests run with `--network none`. **Dependency installation** needs `pip`, so it cannot be fully offline, but it does not get open network either: the install container runs on an `--internal` Docker network with no route out, and its only egress is a small allowlist proxy (`tools/allowlist_proxy.py`, baked into the image) that tunnels HTTPS to package hosts only and refuses any host resolving to a private, loopback, or link-local address. So a malicious build backend or dependency hook cannot reach the cloud metadata endpoint (`169.254.169.254`), internal services, or arbitrary hosts — even if it ignores the proxy environment, there is no other route for the packets. The allowlist is `pypi.org,files.pythonhosted.org,pythonhosted.org` by default (override with `BENCH_PROXY_ALLOW`). The proxy runs from the trusted base image, never from a repo.

The scoring logic also exists as `tools/run.py` for local development, which runs a single task **unsandboxed** — do not point it at untrusted repositories outside a VM.

## The reference number

`gpt-5-mini`, one attempt per task, no escalation, no repair loop:

**46 / 70 = 65.7% solved.**

> This is 65.7% of *this certified Python task pool*, under this prompt, this edit format, the `loopcontrolbench-base` (Python 3.13) container, and a one-attempt policy. It is **not** 65.7% of software bugs in general.

**0 harness artifacts** across the scoring run. Every one of the 70 certified tasks produced a scoreable outcome: 46 solved and 24 model misses, with zero invalid edits and zero run-time infrastructure exclusions. The pool is certified against the scoring image before scoring, so setup and harness-compatibility drops happen at admission, not silently mid-run: one candidate (`psf/requests`, task-r030) was excluded at the gate because its test harness will not import on modern Python, and it is recorded in `tasks/_dropped/`. See [Outcomes and the denominator](#outcomes-and-the-denominator) for the exact definitions.

| repo | solved / total |
|---|---|
| h2 | 2 / 2 |
| pyjwt | 5 / 6 |
| dateutil | 5 / 5 |
| more-itertools | 12 / 16 |
| marshmallow | 9 / 11 |
| click | 4 / 9 |
| prettytable | 6 / 12 |
| requests | 3 / 9 |
| **total** | **46 / 70** |

The per-task results behind this number are committed in `reference/gpt-5-mini.json`, so the figure is auditable without rerunning.

## Why this exists

The interesting question in an agentic loop is not who controls the loop. It is who determines done. Put that authority in a deterministic test harness instead of the model, and a plain measurement falls out: how much does a model actually clear when it cannot grade its own work? On this class of task, a cheap model clears the majority on a single attempt.

That means escalation is not required for every task. Whether a more capable, more expensive tier improves *coverage* or only changes *throughput* is an empirical question this harness can measure, not a conclusion this reference run settles. It settles one thing: a cheap model, gated by a deterministic test, already clears most of this pool alone.

## The boundary

**Published here: the harness.** The task pool, the gate that admits tasks, the miner that grows it, the runner that scores a model. All of it mechanical.

**Not published: the judgment.** How a score becomes an assessment of a product, a model, or a vendor is not in this repo, and it is not meant to be. Here is the harness. Build your own read on it. An instrument you can audit is worth more than a verdict you have to trust.

## How it works

- **Task pool** (`tasks/`) — one spec per task: `repo`, `base_sha`, `fix_sha`, `source_files`, `test_files`, `fail_to_pass`. No code is stored; the repo is checked out from the pinned commits at run time, so a task is the real project, not a snippet of it.
- **The gate** (`tools/gate_sandboxed.py`, the default; `tools/gate.py` is the same logic as a readable host reference) — admission and fidelity are the same check. Check out the parent (buggy), overlay the PR's real test, run it: it must fail. Apply the PR's real source change, run again: it must pass. The tests that flip fail-to-pass are the gate. If nothing flips, the task is not admitted. The gate runs **inside the same container the scorer uses** (`loopcontrolbench-base`), so a task is certified against the exact environment it will be scored in. Two things fall out automatically, with no human ruling: a bug that only reproduced on an old interpreter is excluded (it passes on modern Python), and a task whose *test harness* will not import on modern Python is excluded too (this is why `psf/requests` task-r030 dropped: its `pytest-httpbin` chain needs a `werkzeug` symbol removed years ago). Certifying in the scoring image is also what lets the runner treat any later collection failure as the model's fault rather than an infrastructure excuse.
- **The miner** (`tools/mine.py`) — the growth engine. It finds merged bug-fix PRs, builds candidate specs, and runs each through the gate. Whatever reproduces is admitted. The pool grows by finding PRs, not by maintaining environments.
- **The runner** — scores a model. The scoring logic (`tools/run.py`) validates and applies the model's edit and determines the outcome; the default entry point (`tools/run_sandboxed.py`, which `bench.sh` uses) executes that logic inside an isolated per-task container (see [Security](#-security-this-runs-untrusted-code)). Invariants:
  - the model is shown only the buggy source and the failing test, and asked for a search/replace edit;
  - **it may only edit the task's allowlisted `source_files`; edits to any other path, including any test file, are rejected;**
  - the maintainer's tests (from the fix commit) are overlaid and are the tests that get scored, so the model cannot alter or disable them;
  - the named `fail_to_pass` tests must actually execute and pass. A collection failure, a skipped or xfailed target, or an inapplicable edit all count as **not solved**, never as a pass.

  This measures **test-satisfying repair**, not proven semantic correctness. A degenerate fix that hard-codes the tested value in a source file would pass; the runner constrains the write surface but does not (yet) run the broader suite to catch adjacent regressions. See the roadmap.

## Quickstart

Run inside a disposable VM or container (see the security section).

```bash
# 1. build the sandbox image once
docker build -t loopcontrolbench-base tools/

# 2. controller venv (for the OpenAI client)
python3.13 -m venv .venv && .venv/bin/pip install openai

# 3. score a model across the whole pool
OPENAI_API_KEY=sk-... MODEL=gpt-5-mini ./tools/bench.sh
#   -> results/<model>/<task>.json, one per task

# 4. run a single task (sandboxed; requires Docker)
OPENAI_API_KEY=sk-... MODEL=gpt-5-mini .venv/bin/python tools/run_sandboxed.py tasks/<task>.json

# 5. grow the pool from fresh PRs (mines, then certifies each in the container)
python3 tools/mine.py owner/repo 12
```

The sandboxed path needs `git`, `gh`, `docker`, and network. Each task clones on the host, then installs and tests inside a disposable container. It is CPU and disk work, no GPU. The miner certifies candidates through the same container gate, so it needs the image too.

**Development only (unsandboxed):** `tools/run.py <task.json>` runs the scoring logic directly on the host, without a container. It is for local iteration on the runner itself and must never be pointed at untrusted repositories outside a VM.

**Claude Code skills.** `.claude/skills/` ships two skills for anyone driving this with Claude Code: `loopcontrolbench-run` (score a model behind the gate and report the number honestly) and `loopcontrolbench-grow` (mine PRs, certify each in the container, admit or drop-and-document). They encode the same discipline this README describes.

## Outcomes and the denominator

Each attempted task resolves to exactly one outcome:

- **solved** — the edit applied and every named `fail_to_pass` test now passes.
- **model miss** — the edit applied cleanly but a target test still fails.
- **invalid edit** — the model's response could not be parsed as the required edit, its search text did not match the file, or it targeted a non-allowlisted path. Counted as **not solved** (the model produced an unusable response) and it stays in the denominator.
- **model timeout** — the model did not return a usable response within the solve budget. Counted as **not solved**, in the denominator. A hanging model does not earn a cleaner denominator than one that returns a bad edit.
- **excluded at certification** — a task that cannot be set up or reproduced in the scoring image is dropped **at the gate, before any model runs**, never mid-score: a clone or install failure, or a test harness that will not collect on modern Python. These are recorded in `tasks/_dropped/` with a reason, so an exclusion is a visible admission decision, not a silent hole in a run. A model's own edit breaking collection is *not* this: because the pool is certified to collect, a collection failure during scoring is counted as a **model miss**, in the denominator. A benchmark-wide service outage is reported separately.

The reference denominator is **70 certified tasks**, all of which produced scoreable outcomes: 46 solved and 24 model misses, with zero invalid edits, zero model timeouts, and zero run-time infrastructure exclusions. "0 harness artifacts" means nothing fell into an excluded bucket during scoring and no response was invalid, so the 65.7% is a clean model measurement. One further candidate was excluded at certification (task-r030) and is not part of the 70.

## Reproducibility

Be precise about what is guaranteed:

- **Auditable** — yes. The task definitions and the committed result records can be inspected directly.
- **Re-runnable** — yes. Anyone can execute the same procedure and should land close to the reference.
- **Bit-for-bit reproducible** — not yet guaranteed. Task identity is pinned (commits, tests), but dependency resolution is not: `pip install -e .` plus test extras can resolve to different transitive versions over time, and OS/compiler/toolchain drift. Python 3.13 is a constant; the rest of the environment is not.

So the honest claim is "audit the published figure and rerun the same procedure," not "get the identical number forever." An immutable reference execution image and a per-result environment manifest are on the roadmap to close that gap.

## Contamination and what the score is not

The task specs contain `fix_sha`, and the fixes are public PRs. **The answer is retrievable.** loopcontrolbench is a transparent engineering measurement under an honor-system, no-answer-lookup policy. It is **not** a contamination-resistant held-out evaluation, and it should not be used as one. An agent with network access or with read access to the local git history could look up the merged fix. When scoring, disable network during the solve step, and do not hand the model `fix_sha`, the PR URL, commit messages, or repository history beyond the base checkout. The reference runner passes the model only the buggy source and the failing test.

## What this does not claim

- It is a snapshot, not a leaderboard. 70 tasks across 8 libraries is enough to be honest, not enough to rank models to a decimal point.
- It measures single-attempt, test-satisfying repair behind a validator. It says nothing here about repair loops, escalation, or semantic correctness beyond the test.
- The population favors localized, well-specified, testable library bugs. It excludes multi-file and architectural changes, environment-specific and distributed-systems failures, UI bugs, performance regressions, and anything without a clean maintainer test that flips on a modern interpreter.
- The validator only checks what the test covers. A wrong fix that passes a thin test, or a correct fix that fails a bad one, are both invisible to it. Task quality is the ceiling, which is why the gate is strict about what it admits.

## Growing the pool

`tools/mine.py owner/repo <limit>` mines recent merged bug-fix PRs and admits the ones that reproduce. The gate makes *admission* mechanical, so contributions cannot bypass the fail-to-pass fidelity gate. It does not, by itself, protect *corpus quality* — representativeness and adversarial selection are governed, not automatic.

Contributions welcome under these rules:

- one task per genuine bug; deduplicate against existing patterns
- reasonable repository and task size (no denial-of-service repos)
- source project must carry a recognized open-source license
- disclose any task drawn from a repository you author or maintain
- no benchmark-targeted commits; task selection must happen before the task is evaluated against any model whose score will be reported, and contributors must disclose any prior model runs
- a cap per source repository, to avoid concentration
- include provenance (repo, PR, commits) in the spec

## Independence and disclosure

Independent and self-funded. No vendor paid for, commissioned, or reviewed this benchmark. The practice behind it has commercial relationships with infrastructure vendors; those are disclosed on the associated lab pages, and none of them are model providers being measured here.

## License

The code and task metadata in this repository are MIT (see `LICENSE`). **The projects the harness clones remain under their own licenses**, and running the benchmark invokes, patches, and evaluates that third-party code; complying with those licenses is the user's responsibility. Result files store only outcome metadata (task id, pass/fail, failing-test list), not source excerpts or full generated patches, to avoid redistributing third-party code. Returns, not algorithms: the harness is open, the assessment methodology built on top of it is not.

## Roadmap

Per-task container isolation (no host env, restricted mounts, no Docker socket, resource limits, network off during tests, teardown) is **done**. Install-phase egress lockdown (internal network with no route out, egress only through an allowlist proxy to package hosts, private/link-local IPs refused) is **done**. Remaining:

- Tighten the install allowlist further (per-run pinned index, hash-checked downloads) and offer a fully offline pre-fetched install mode for operators who can pre-populate a wheel cache.
- Dependency pinning, an immutable reference image digest, and a per-result environment manifest (OS, arch, Python patch, installed versions).
- Full result-integrity metadata (benchmark SHA, task/prompt/runner hashes, model response hash, log hashes, exit codes, token/latency) and a `results verify` command.
- Optional adjacent-regression scoring: run the tests nearest the modified module and report, without necessarily gating on the full suite.
