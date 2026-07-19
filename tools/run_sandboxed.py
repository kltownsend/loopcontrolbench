#!/usr/bin/env python3
"""Sandboxed runner (default for real runs).

The untrusted steps — installing the repo (runs its setup.py) and running its tests —
happen inside a disposable container that gets NO host environment, so it never sees the
API key or any credential, and runs tests with the network turned off. The host does only
git and file operations (low risk) and the model call (which needs the key). The container
and the untrusted code never share an environment with the key.

Isolation per task: fresh container, --network none during tests, no docker socket,
no host mounts except the per-task work dir, memory/cpu/pid limits, --rm teardown.

Env: MODEL, OPENAI_API_KEY, optional OPENAI_BASE_URL, optional BENCH_IMAGE.
Usage: run_sandboxed.py <path/to/task.json>
"""
import subprocess, sys, tempfile, os, json, shutil
sys.path.insert(0, os.path.dirname(__file__))
from run import build_prompt, call_model, parse_edits, _parse  # host-side, no subprocess

IMAGE = os.environ.get('BENCH_IMAGE', 'loopcontrolbench-base')  # build: docker build -t loopcontrolbench-base tools/
# work dir must be under a Docker-shared path; ~ is shared on Docker Desktop for macOS.
WORKROOT = os.path.expanduser('~/.lcb_work')
LIMITS = ['--memory=2g', '--cpus=2', '--pids-limit=512', '--security-opt=no-new-privileges']

INSTALL = (
    'python -m venv /work/venv && '
    '/work/venv/bin/pip install -q -e . 2>/dev/null; '
    'for e in test tests dev testing all; do /work/venv/bin/pip install -q -e ".[$e]" 2>/dev/null; done; '
    'for r in requirements*dev*.txt requirements*test*.txt test-requirements.txt dev-requirements.txt; do '
    '[ -f "$r" ] && /work/venv/bin/pip install -q -r "$r" 2>/dev/null; done; '
    '/work/venv/bin/pip install -q pytest 2>/dev/null; echo __INSTALL_OK__'
)

def _run(cmd, timeout=1200):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def _docker(work, bash_cmd, network, timeout):
    # No -e / --env-file: the container inherits none of the host environment.
    return _run(['docker', 'run', '--rm', '--network', network, '--pull', 'never',
                 '-v', work + ':/work', '-w', '/work/repo', *LIMITS, IMAGE, 'bash', '-lc', bash_cmd],
                timeout=timeout)

def prepare_host(spec, work):
    rp = os.path.join(work, 'repo')
    _run(['git', 'clone', '--quiet', '--no-checkout', 'https://github.com/' + spec['repo'], rp])
    for sha in (spec['fix_sha'], spec['base_sha']):
        _run(['git', '-C', rp, 'fetch', '--quiet', 'origin', sha])
    _run(['git', '-C', rp, 'checkout', '--quiet', spec['base_sha']])
    for tf in spec['test_files']:
        _run(['git', '-C', rp, 'checkout', spec['fix_sha'], '--', tf])
    return rp

def run_task(spec):
    os.makedirs(WORKROOT, exist_ok=True)
    work = tempfile.mkdtemp(prefix='lcb_', dir=WORKROOT)
    log = {'task_id': spec['task_id'], 'model': os.environ.get('MODEL', 'gpt-5-mini'), 'sandboxed': True}
    try:
        rp = prepare_host(spec, work)
        inst = _docker(work, INSTALL, network='bridge', timeout=1200)   # pip needs network
        if '__INSTALL_OK__' not in inst.stdout:
            log['drop'] = 'install_failed'; return log
        prompt = build_prompt(spec, rp)                                 # host reads source
        out = call_model(prompt)                                        # host holds the key
        allowed = set(spec['source_files']); applied, failed, oos = [], [], []
        for path, search, replace in parse_edits(out):
            if path not in allowed:
                oos.append(path); continue
            full = os.path.join(rp, path); content = open(full).read()
            if search and search in content:
                open(full, 'w').write(content.replace(search, replace, 1)); applied.append(path)
            else:
                failed.append(path)
        r = _docker(work, '/work/venv/bin/python -m pytest -v --tb=no -p no:cacheprovider '
                    + ' '.join(spec['test_files']), network='none', timeout=600)   # tests: no network
        res = _parse(r.stdout + r.stderr)
        # The pool is gate-certified to collect and reproduce in THIS image
        # (tools/gate_sandboxed.py). So if a target test does not report here, the
        # model's own edit broke collection: that is a miss (still failing), never a
        # silent exclusion. The only legitimate run-time infra exclusion is
        # install_failed, handled above.
        still = [t for t in spec['fail_to_pass'] if res.get(t) != 'PASSED']
        log.update({'solved': not still, 'edits_applied': len(applied), 'edits_failed': len(failed),
                    'out_of_scope': oos, 'still_failing': still, 'completion_chars': len(out)})
        return log
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == '__main__':
    print(json.dumps(run_task(json.load(open(sys.argv[1]))), indent=1))
