#!/usr/bin/env python3
"""Reproduce-or-drop gate. Given a task spec (repo, base_sha, fix_sha, test_files,
source_files), check out the repo at base (buggy), overlay the fix commit's tests,
run them; then apply the fix commit's source and run again. FAIL_TO_PASS = tests that
fail on buggy and pass on fixed. Non-empty => the bug reproduces on THIS pinned modern
environment and the task is admitted; empty => drop. Fully mechanical, no inference."""
import subprocess, sys, tempfile, os, json, shutil, re, glob as _g

# Untrusted repo code (setup.py, tests) runs in subprocesses. Strip credentials from
# their environment so a repo can't read API keys, cloud creds, or tokens. Env-only
# mitigation; real isolation needs a container (roadmap).
_SENS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD', 'CREDENTIAL')
_SENS_PREFIX = ('AWS_', 'GOOGLE_', 'GCP_', 'AZURE_', 'OPENAI_', 'ANTHROPIC_', 'GH_',
                'GITHUB_', 'SSH_', 'NPM_', 'DOCKER_', 'HF_', 'HUGGING')
SAFE_ENV = {k: v for k, v in os.environ.items()
            if not (any(s in k.upper() for s in _SENS) or k.upper().startswith(_SENS_PREFIX))}

def _run(cmd, cwd=None, timeout=900, env=SAFE_ENV):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)

def _parse(out):
    res = {}
    for line in out.splitlines():
        m = re.match(r'^(\S+::\S+)\s+(PASSED|FAILED|ERROR)', line)
        if m:
            res[m.group(1)] = m.group(2)
    return res

def _pytest(python, rp, test_files):
    r = _run([python, '-m', 'pytest', '-v', '--tb=no', '-p', 'no:cacheprovider', *test_files],
             cwd=rp, timeout=600)
    return _parse(r.stdout + r.stderr)

def gate_spec(repo, base_sha, fix_sha, test_files, source_files, py='python3.13'):
    srcs = [f for f in source_files if f.endswith('.py')]
    tmp = tempfile.mkdtemp(prefix='bench_'); rp = os.path.join(tmp, 'repo')
    log = {'repo': repo, 'fix_sha': fix_sha}
    try:
        _run(['git', 'clone', '--quiet', '--no-checkout', 'https://github.com/' + repo, rp])
        for sha in (fix_sha, base_sha):
            _run(['git', 'fetch', '--quiet', 'origin', sha], cwd=rp)
        if _run(['git', 'checkout', '--quiet', base_sha], cwd=rp).returncode != 0:
            log['drop'] = 'checkout_failed'; return log
        venv = os.path.join(tmp, 'venv'); _run([py, '-m', 'venv', venv])
        pip = os.path.join(venv, 'bin', 'pip'); python = os.path.join(venv, 'bin', 'python')
        if _run([pip, 'install', '-q', '-e', '.'], cwd=rp, timeout=900).returncode != 0:
            log['drop'] = 'install_failed'; return log
        for extra in ('test', 'tests', 'dev', 'testing', 'all'):
            _run([pip, 'install', '-q', '-e', '.[%s]' % extra], cwd=rp, timeout=600)
        for pat in ('requirements*dev*.txt', 'requirements*test*.txt', 'test-requirements.txt',
                    'dev-requirements.txt', 'requirements/*test*.txt', 'requirements/*dev*.txt'):
            for req in _g.glob(os.path.join(rp, pat)):
                _run([pip, 'install', '-q', '-r', req], cwd=rp, timeout=600)
        _run([pip, 'install', '-q', 'pytest'], cwd=rp)
        for tf in test_files:
            _run(['git', 'checkout', fix_sha, '--', tf], cwd=rp)
        buggy = _pytest(python, rp, test_files)
        for sf in srcs:
            _run(['git', 'checkout', fix_sha, '--', sf], cwd=rp)
        fixed = _pytest(python, rp, test_files)
        f2p = sorted(n for n in buggy if buggy[n] in ('FAILED', 'ERROR') and fixed.get(n) == 'PASSED')
        log.update({'reproduce': bool(f2p), 'fail_to_pass': f2p, 'n_collected': len(buggy)})
        if not f2p:
            log['drop'] = 'collection_failed' if len(buggy) == 0 else 'no_reproduce_on_env'
        return log
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == '__main__':
    s = json.load(open(sys.argv[1]))
    print(json.dumps(gate_spec(s['repo'], s['base_sha'], s['fix_sha'],
                               s['test_files'], s['source_files']), indent=1))
