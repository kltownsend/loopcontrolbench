#!/usr/bin/env python3
"""External-worker validator. Exposes the same deterministic gate the runner uses, so any agent or
model can be scored against the pool without writing worker code.

  cval.py <task_id> --prompt      -> print the exact worker task text (build_prompt), after prep
  cval.py <task_id>  (edit stdin) -> apply the parsed search/replace edit to a PERSISTENT per-task
                                     sandbox (installed once, reused), run fail_to_pass, print verdict

Verdict lines: 'RESULT: SOLVED' | 'RESULT: FAILED' | 'RESULT: NO_EDIT_APPLIED' | 'RESULT: INSTALL_FAILED'
Persistent sandboxes live under ~/.lcb_cval/<task_id> so repeated attempts skip reinstall.
"""
import sys, os, json, subprocess
sys.path.insert(0, os.path.dirname(__file__))
from run import parse_edits, _parse, build_prompt
from run_sandboxed import prepare_host, install_in_sandbox, _docker
ROOT = os.path.expanduser('~/.lcb_cval')

def prep(spec):
    work = os.path.join(ROOT, spec['task_id']); os.makedirs(work, exist_ok=True)
    marker = os.path.join(work, '.installed'); rpf = os.path.join(work, '.rp')
    if os.path.exists(marker) and os.path.exists(rpf):
        return work, open(rpf).read().strip(), True
    rp = prepare_host(spec, work)
    inst = install_in_sandbox(work)
    ok = inst.returncode == 0 and '__INSTALL_OK__' in inst.stdout
    if ok:
        open(marker, 'w').write('ok'); open(rpf, 'w').write(rp)
    return work, rp, ok

def reset(rp, spec):
    for sf in spec['source_files']:
        subprocess.run(['git', '-C', rp, 'checkout', spec['base_sha'], '--', sf], capture_output=True)

def apply(rp, spec, comp):
    allowed = set(spec['source_files']); applied = []
    for path, search, replace in parse_edits(comp):
        if path not in allowed: continue
        full = os.path.join(rp, path)
        try: c = open(full).read()
        except OSError: continue
        if search and search in c:
            open(full, 'w').write(c.replace(search, replace, 1)); applied.append(path)
    return applied

def test(work, spec):
    r = _docker(work, '/work/venv/bin/python -m pytest -vv --tb=long -p no:cacheprovider '
                + ' '.join(spec['test_files']), network='none', timeout=600)
    out = r.stdout + r.stderr; res = _parse(out)
    return all(res.get(t) == 'PASSED' for t in spec['fail_to_pass']), out

if __name__ == '__main__':
    tid = sys.argv[1]; spec = json.load(open(f'tasks/{tid}.json'))
    work, rp, ok = prep(spec)
    if not ok:
        print('RESULT: INSTALL_FAILED'); sys.exit(0)
    if len(sys.argv) > 2 and sys.argv[2] == '--prompt':
        print(build_prompt(spec, rp)); sys.exit(0)
    comp = sys.stdin.read(); reset(rp, spec); applied = apply(rp, spec, comp)
    if not applied:
        print('RESULT: NO_EDIT_APPLIED (path must match allowlist and SEARCH must match file exactly)')
        print('ALLOWLIST:', spec['source_files']); sys.exit(0)
    passed, out = test(work, spec)
    print('RESULT: SOLVED' if passed else 'RESULT: FAILED')
    print('APPLIED_TO:', applied, '| FAIL_TO_PASS:', spec['fail_to_pass'])
    print('--- pytest output (tail) ---'); print(out[-4000:])
