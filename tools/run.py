#!/usr/bin/env python3
"""Runner. Run a model against one task and score it behind the deterministic gate.

Single attempt by default (the DCITL measurement: how much a model solves when a
deterministic validator, not the model, decides done). The worker is model-agnostic:
any OpenAI-compatible chat endpoint (OpenAI, vLLM, Ollama) via env:
  MODEL (default gpt-5-mini), OPENAI_BASE_URL (default OpenAI), OPENAI_API_KEY.

Usage: run.py <path/to/task.json>
Result JSON: {task_id, model, solved (bool), fail_to_pass, still_failing, ...}
"""
import subprocess, sys, tempfile, os, json, shutil, re, ast, glob as _g

PY = os.environ.get('BENCH_PY', 'python3.13')
MODEL = os.environ.get('MODEL', 'gpt-5-mini')
BASE_URL = os.environ.get('OPENAI_BASE_URL')  # None => OpenAI default
API_KEY = os.environ.get('OPENAI_API_KEY', 'x')

# Untrusted repo code (setup.py, tests) runs in subprocesses. Strip credentials from
# their environment so a malicious or careless repo can't read API keys, cloud creds,
# or tokens. This is env-only mitigation; real isolation needs a container (roadmap).
_SENS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD', 'CREDENTIAL')
_SENS_PREFIX = ('AWS_', 'GOOGLE_', 'GCP_', 'AZURE_', 'OPENAI_', 'ANTHROPIC_', 'GH_',
                'GITHUB_', 'SSH_', 'NPM_', 'DOCKER_', 'HF_', 'HUGGING')
SAFE_ENV = {k: v for k, v in os.environ.items()
            if not (any(s in k.upper() for s in _SENS) or k.upper().startswith(_SENS_PREFIX))}

def _run(cmd, cwd=None, timeout=900, env=SAFE_ENV):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)

def _parse(out):
    return {m.group(1): m.group(2) for line in out.splitlines()
            if (m := re.match(r'^(\S+::\S+)\s+(PASSED|FAILED|ERROR)', line))}

def _pytest(python, rp, tests):
    r = _run([python, '-m', 'pytest', '-v', '--tb=no', '-p', 'no:cacheprovider', *tests], cwd=rp, timeout=600)
    return _parse(r.stdout + r.stderr)

def prepare(spec, tmp):
    """Clone at base (buggy), install in the pinned env, overlay the fix commit's tests."""
    rp = os.path.join(tmp, 'repo')
    _run(['git', 'clone', '--quiet', '--no-checkout', 'https://github.com/' + spec['repo'], rp])
    for sha in (spec['fix_sha'], spec['base_sha']):
        _run(['git', 'fetch', '--quiet', 'origin', sha], cwd=rp)
    _run(['git', 'checkout', '--quiet', spec['base_sha']], cwd=rp)
    venv = os.path.join(tmp, 'venv'); _run([PY, '-m', 'venv', venv])
    pip = os.path.join(venv, 'bin', 'pip'); python = os.path.join(venv, 'bin', 'python')
    _run([pip, 'install', '-q', '-e', '.'], cwd=rp, timeout=900)
    for extra in ('test', 'tests', 'dev', 'testing', 'all'):
        _run([pip, 'install', '-q', '-e', '.[%s]' % extra], cwd=rp, timeout=600)
    for pat in ('requirements*dev*.txt', 'requirements*test*.txt', 'test-requirements.txt', 'dev-requirements.txt'):
        for req in _g.glob(os.path.join(rp, pat)):
            _run([pip, 'install', '-q', '-r', req], cwd=rp, timeout=600)
    _run([pip, 'install', '-q', 'pytest'], cwd=rp)
    for tf in spec['test_files']:
        _run(['git', 'checkout', spec['fix_sha'], '--', tf], cwd=rp)
    return rp, python

def extract_tests(rp, spec):
    """Return the source of the specific fail_to_pass test functions, ast-extracted."""
    want = set()
    for nid in spec['fail_to_pass']:
        want.add(nid.split('::')[-1])  # last component = function/method name
    out = []
    for tf in spec['test_files']:
        path = os.path.join(rp, tf)
        if not os.path.exists(path): continue
        src = open(path).read()
        try: tree = ast.parse(src)
        except SyntaxError: continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in want:
                seg = '\n'.join(lines[node.lineno - 1: node.end_lineno])
                out.append('# %s\n%s' % (tf, seg))
    return '\n\n'.join(out) or ('(test bodies not extractable; must satisfy: %s)' % ', '.join(spec['fail_to_pass']))

def build_prompt(spec, rp):
    parts = ['You are fixing a bug in the Python project `%s`.' % spec['repo'], '',
             'These test(s) currently fail and must pass after your change:', '',
             extract_tests(rp, spec), '', 'Source file(s) you may edit:']
    for sf in spec['source_files']:
        path = os.path.join(rp, sf)
        content = open(path).read() if os.path.exists(path) else ''
        parts += ['', 'FILE: %s' % sf, '```python', content, '```']
    parts += ['', 'Return your change as one or more search/replace edits, each exactly in this form:',
              'FILE: <path>', '<<<<<<< SEARCH', '<a few exact lines copied verbatim from the current file>',
              '=======', '<the replacement lines>', '>>>>>>> REPLACE', '',
              'Each SEARCH block must match the current file exactly, including indentation. Change only',
              'what is needed to make the failing test(s) pass. Do not modify tests.']
    return '\n'.join(parts)

def call_model(prompt, timeout=300):
    from openai import OpenAI
    kw = {'api_key': API_KEY, 'timeout': timeout}
    if BASE_URL:
        kw['base_url'] = BASE_URL
    client = OpenAI(**kw)
    try:
        r = client.chat.completions.create(model=MODEL, messages=[{'role': 'user', 'content': prompt}])
        return r.choices[0].message.content or ''
    except Exception:
        # A model timeout or API error yields no edit, so the task scores as a miss
        # (not solved, still in the denominator), never a crash-exclusion.
        return ''

def parse_edits(text):
    edits = []
    for m in re.finditer(r'FILE:\s*(\S+)\s*\n<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE',
                         text, re.DOTALL):
        edits.append((m.group(1).strip(), m.group(2), m.group(3)))
    return edits

def run_task(spec):
    tmp = tempfile.mkdtemp(prefix='benchrun_')
    log = {'task_id': spec['task_id'], 'model': MODEL}
    try:
        rp, python = prepare(spec, tmp)
        prompt = build_prompt(spec, rp)
        out = call_model(prompt)
        edits = parse_edits(out)
        allowed = set(spec['source_files'])
        applied, failed, oos = [], [], []
        for path, search, replace in edits:
            if path not in allowed:
                oos.append(path); continue
            full = os.path.join(rp, path); content = open(full).read()
            if search and search in content:
                open(full, 'w').write(content.replace(search, replace, 1)); applied.append(path)
            else:
                failed.append(path)
        res = _pytest(python, rp, spec['test_files'])
        still = [t for t in spec['fail_to_pass'] if res.get(t) != 'PASSED']
        log.update({'solved': not still, 'n_edits': len(edits), 'edits_applied': len(applied),
                    'edits_failed': len(failed), 'out_of_scope': oos, 'still_failing': still,
                    'completion_chars': len(out)})
        return log
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == '__main__':
    print(json.dumps(run_task(json.load(open(sys.argv[1]))), indent=1))
