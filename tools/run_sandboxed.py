#!/usr/bin/env python3
"""Sandboxed runner (default for real runs).

The untrusted steps — installing the repo (runs its setup.py) and running its tests —
happen inside a disposable container that gets NO host environment, so it never sees the
API key or any credential. The host does only git and file operations (low risk) and the
model call (which needs the key). The container and the untrusted code never share an
environment with the key.

Egress is locked down in both untrusted phases. Tests run with --network none. Install runs
on an --internal Docker network with no route out; its ONLY egress is a small allowlist proxy
(package hosts only, and it refuses any host resolving to a private/link-local IP). So a
malicious build backend or dependency hook cannot reach the cloud metadata endpoint, internal
services, or arbitrary hosts, even if it ignores the proxy env: with no other route, the
packets have nowhere to go. The proxy runs from the trusted base image, never from a repo.

Isolation per task: fresh container, allowlist-proxied install then --network none tests, no
docker socket, no host mounts except the per-task work dir, memory/cpu/pid limits, --rm teardown.

Env: MODEL, OPENAI_API_KEY, optional OPENAI_BASE_URL, optional BENCH_IMAGE.
Usage: run_sandboxed.py <path/to/task.json>   |   run_sandboxed.py --teardown
"""
import subprocess, sys, tempfile, os, json, shutil, time
sys.path.insert(0, os.path.dirname(__file__))
from run import build_prompt, call_model, parse_edits, _parse, last_error  # host-side, no subprocess

IMAGE = os.environ.get('BENCH_IMAGE', 'loopcontrolbench-base')  # build: docker build -t loopcontrolbench-base tools/
# work dir must be under a Docker-shared path; ~ is shared on Docker Desktop for macOS.
WORKROOT = os.path.expanduser('~/.lcb_work')
LIMITS = ['--memory=2g', '--cpus=2', '--pids-limit=512', '--security-opt=no-new-privileges',
          '--cap-drop=ALL']  # pip/pytest need no Linux capabilities; drop them all

# Egress control for the install phase: an internal (no-route-out) network for the untrusted
# install container, plus a proxy that straddles it and an egress network and only tunnels to
# package hosts. The proxy is the sole path out, so blocking is not opt-in on the repo's code.
EGRESS_NET = 'lcb-egress'
INTERNAL_NET = 'lcb-internal'
PROXY_NAME = 'lcb-proxy'
PROXY_PORT = '8899'
PROXY_ALLOW = os.environ.get('BENCH_PROXY_ALLOW', 'pypi.org,files.pythonhosted.org,pythonhosted.org')
_PROXY_URL = 'http://%s:%s' % (PROXY_NAME, PROXY_PORT)
PROXY_ENV = [a for k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy')
             for a in ('-e', '%s=%s' % (k, _PROXY_URL))]

# Fail closed: the required steps (venv, base install, explicit requirements files, pytest) abort
# the whole script on failure via `set -e`, so a failed install never reaches __INSTALL_OK__ and
# the caller (which checks BOTH the exit code and the marker) drops the task as install_failed
# instead of letting a broken environment be scored as a model miss. Optional extras stay
# best-effort (|| true): a project may legitimately lack a [test] extra.
INSTALL = (
    'set -e; '
    'python -m venv /work/venv; '
    '/work/venv/bin/pip install -q -e .; '
    'for e in test tests dev testing all; do /work/venv/bin/pip install -q -e ".[$e]" 2>/dev/null || true; done; '
    'for r in requirements*dev*.txt requirements*test*.txt test-requirements.txt dev-requirements.txt; do '
    'if [ -f "$r" ]; then /work/venv/bin/pip install -q -r "$r"; fi; done; '
    '/work/venv/bin/pip install -q pytest; echo __INSTALL_OK__'
)

def _run(cmd, timeout=1200):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def _docker(work, bash_cmd, network, timeout, env_extra=None):
    # No -e / --env-file beyond env_extra: the container inherits none of the host environment.
    # env_extra carries only the proxy address for the install phase (not a credential).
    return _run(['docker', 'run', '--rm', '--network', network, '--pull', 'never',
                 *(env_extra or []),
                 '-v', work + ':/work', '-w', '/work/repo', *LIMITS, IMAGE, 'bash', '-lc', bash_cmd],
                timeout=timeout)

def _net_exists(name):
    return subprocess.run(['docker', 'network', 'inspect', name],
                          capture_output=True).returncode == 0

def _proxy_running():
    r = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', PROXY_NAME],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == 'true'

def _ready():
    return _proxy_running() and _net_exists(INTERNAL_NET) and _net_exists(EGRESS_NET)

def ensure_proxy():
    """Idempotently create the two networks and start the allowlist proxy. Reused across tasks.
    A lockdir serializes setup so parallel tasks (bench.sh runs 6 at once) don't race."""
    os.makedirs(WORKROOT, exist_ok=True)
    if _ready():
        return
    lock = os.path.join(WORKROOT, '.proxy.lock')
    for _ in range(90):                       # wait out whoever holds the lock, then re-check
        try:
            os.mkdir(lock); break
        except FileExistsError:
            if _ready(): return
            time.sleep(1)
    else:
        return                                # best effort; proceed even if we never got the lock
    try:
        if _ready():
            return
        if not _net_exists(EGRESS_NET):
            _run(['docker', 'network', 'create', EGRESS_NET])
        if not _net_exists(INTERNAL_NET):
            _run(['docker', 'network', 'create', '--internal', INTERNAL_NET])
        if not _proxy_running():
            subprocess.run(['docker', 'rm', '-f', PROXY_NAME], capture_output=True)  # clear stale
            _run(['docker', 'run', '-d', '--name', PROXY_NAME, '--network', INTERNAL_NET,
                  *LIMITS, IMAGE, 'python', '/allowlist_proxy.py', PROXY_PORT, PROXY_ALLOW])
            _run(['docker', 'network', 'connect', EGRESS_NET, PROXY_NAME])  # give the proxy egress
            time.sleep(2)                     # let it bind before the first install connects
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass

def install_in_sandbox(work, timeout=1200):
    """Run the untrusted install on the no-egress network, reachable out only via the proxy."""
    ensure_proxy()
    return _docker(work, INSTALL, network=INTERNAL_NET, timeout=timeout, env_extra=PROXY_ENV)

def teardown_proxy():
    subprocess.run(['docker', 'rm', '-f', PROXY_NAME], capture_output=True)
    for n in (INTERNAL_NET, EGRESS_NET):
        subprocess.run(['docker', 'network', 'rm', n], capture_output=True)

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
        inst = install_in_sandbox(work)                                 # pip, egress via allowlist proxy
        # Check BOTH the exit code and the marker: with `set -e` a failed required step aborts
        # before the marker prints and the container exits non-zero, so a broken environment is
        # dropped here, never scored downstream as a model miss.
        if inst.returncode != 0 or '__INSTALL_OK__' not in inst.stdout:
            log.update({'drop': 'install_failed', 'install_returncode': inst.returncode,
                        'outcome': 'infrastructure_error'}); return log
        prompt = build_prompt(spec, rp)                                 # host reads source
        out = call_model(prompt)                                        # host holds the key
        err = last_error()                                              # None on success
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
        solved = not still
        # One explicit outcome per task, so aggregation is not left to interpret the counts.
        if solved:
            outcome = 'solved'
        elif err:                                                       # the model call itself failed
            outcome = 'model_timeout' if 'timeout' in err.lower() else 'infrastructure_error'
        elif applied:
            outcome = 'model_miss'                                      # applied an edit, tests still fail
        else:
            outcome = 'invalid_edit'                                    # no applicable edit: empty, unparseable, unmatched, or out-of-scope
        log.update({'solved': solved, 'outcome': outcome, 'edits_applied': len(applied),
                    'edits_failed': len(failed), 'out_of_scope': oos, 'still_failing': still,
                    'completion_chars': len(out), 'model_error': err})
        return log
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    if arg == '--teardown':
        teardown_proxy(); print('proxy and networks removed')
    elif arg == '--setup':
        ensure_proxy(); print('proxy ready' if _ready() else 'proxy setup incomplete')
    else:
        print(json.dumps(run_task(json.load(open(arg))), indent=1))
