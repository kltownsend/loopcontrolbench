#!/usr/bin/env python3
"""Pass-at-N on the free tier. For one task, run DRAWS independent attempts with NO feedback,
each resetting the source to base, all behind the same deterministic validator, and record the
first draw that solves. This measures what re-rolling the free model recovers, the resample
curve, as distinct from a feedback loop: the loop's value has to beat this to be feedback rather
than a second roll of the dice.

Env: MODEL plus the reasoning env (VLLM_THINK / OLLAMA_THINK, NUM_PREDICT); DRAWS (default 4).
Install runs once; the draws reuse the venv. Usage: resample.py <task.json>
"""
import sys, os, json, tempfile, shutil, subprocess
sys.path.insert(0, os.path.dirname(__file__))
from run import build_prompt, call_model, parse_edits, _parse, MODEL
from run_sandboxed import prepare_host, install_in_sandbox, _docker, WORKROOT

DRAWS = int(os.environ.get('DRAWS', '4'))

def _reset(rp, spec):
    for sf in spec['source_files']:
        subprocess.run(['git', '-C', rp, 'checkout', spec['base_sha'], '--', sf], capture_output=True)

def _apply(rp, spec, comp):
    allowed = set(spec['source_files'])
    for path, search, replace in parse_edits(comp):
        if path not in allowed:
            continue
        full = os.path.join(rp, path)
        try:
            c = open(full).read()
        except OSError:
            continue
        if search and search in c:
            open(full, 'w').write(c.replace(search, replace, 1))

def _solved(work, spec):
    r = _docker(work, '/work/venv/bin/python -m pytest -v --tb=no -p no:cacheprovider '
                + ' '.join(spec['test_files']), network='none', timeout=600)
    res = _parse(r.stdout + r.stderr)
    return all(res.get(t) == 'PASSED' for t in spec['fail_to_pass'])

def run_task(spec):
    os.makedirs(WORKROOT, exist_ok=True)
    work = tempfile.mkdtemp(prefix='rs_', dir=WORKROOT)
    log = {'task_id': spec['task_id'], 'model': MODEL, 'draws': DRAWS}
    try:
        rp = prepare_host(spec, work)
        inst = install_in_sandbox(work)
        if inst.returncode != 0 or '__INSTALL_OK__' not in inst.stdout:
            log['outcome'] = 'install_failed'; return log
        first = None; d = 0
        for d in range(1, DRAWS + 1):
            _reset(rp, spec)
            _apply(rp, spec, call_model(build_prompt(spec, rp)))
            if _solved(work, spec):
                first = d; break
        log.update({'solved': first is not None, 'first_solved_draw': first, 'draws_run': d})
        return log
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == '__main__':
    print(json.dumps(run_task(json.load(open(sys.argv[1]))), indent=1))
