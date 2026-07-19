#!/usr/bin/env python3
"""Reproduce-or-drop gate, run inside the SAME container the scorer uses.

This certifies each task against the actual pinned environment (loopcontrolbench-base),
not the host. The host does only git; install and both pytest passes run in the container
with no host environment. A task is admitted only if, in that image, the target tests
FAIL on the buggy base and PASS after the fix commit's source is applied.

Certifying in the run environment closes the gate-env != run-env gap: once a task passes
this gate, the base is guaranteed to collect in the image, so at score time any collection
failure is the model's edit (a miss), never a silent infra exclusion.

Env: optional BENCH_IMAGE. Usage: gate_sandboxed.py <path/to/task.json>
"""
import subprocess, sys, tempfile, os, json, shutil
sys.path.insert(0, os.path.dirname(__file__))
from run import _parse
from run_sandboxed import IMAGE, WORKROOT, LIMITS, INSTALL, _run, _docker

def prepare_base(spec, work):
    """Host-side git only: clone, checkout buggy base, overlay the fix commit's tests."""
    rp = os.path.join(work, 'repo')
    _run(['git', 'clone', '--quiet', '--no-checkout', 'https://github.com/' + spec['repo'], rp])
    for sha in (spec['fix_sha'], spec['base_sha']):
        _run(['git', '-C', rp, 'fetch', '--quiet', 'origin', sha])
    if _run(['git', '-C', rp, 'checkout', '--quiet', spec['base_sha']]).returncode != 0:
        return None
    for tf in spec['test_files']:
        _run(['git', '-C', rp, 'checkout', spec['fix_sha'], '--', tf])
    return rp

def _pytest(work, spec):
    r = _docker(work, '/work/venv/bin/python -m pytest -v --tb=no -p no:cacheprovider '
                + ' '.join(spec['test_files']), network='none', timeout=600)
    return _parse(r.stdout + r.stderr)

def gate(spec):
    os.makedirs(WORKROOT, exist_ok=True)
    work = tempfile.mkdtemp(prefix='gate_', dir=WORKROOT)
    log = {'task_id': spec['task_id'], 'repo': spec['repo'], 'image': IMAGE}
    try:
        rp = prepare_base(spec, work)
        if rp is None:
            log['drop'] = 'checkout_failed'; return log
        if '__INSTALL_OK__' not in _docker(work, INSTALL, network='bridge', timeout=1200).stdout:
            log['drop'] = 'install_failed'; return log
        buggy = _pytest(work, spec)                                   # base: tests must fail
        srcs = [f for f in spec['source_files'] if f.endswith('.py')]
        for sf in srcs:
            _run(['git', '-C', rp, 'checkout', spec['fix_sha'], '--', sf])
        fixed = _pytest(work, spec)                                   # fixed: tests must pass
        f2p = sorted(n for n in buggy if buggy[n] in ('FAILED', 'ERROR') and fixed.get(n) == 'PASSED')
        log.update({'reproduce': bool(f2p), 'fail_to_pass': f2p, 'n_collected': len(buggy)})
        if not f2p:
            log['drop'] = 'collection_failed' if len(buggy) == 0 else 'no_reproduce_on_env'
        return log
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == '__main__':
    print(json.dumps(gate(json.load(open(sys.argv[1]))), indent=1))
