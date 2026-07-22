#!/usr/bin/env python3
"""Stateful repair loop: a real running conversation, so the worker refines instead of restarting.

Messages accumulate. Assistant turns carry the worker's EDIT (thinking stripped, to keep context
bounded: stateful-on-answers, the fair standard). User turns carry either the full test failure
(rich feedback) or, if CONTROL_MODEL is set, a frontier controller's guidance derived from it. The
deterministic validator calls done. Source is reset to base before each attempt; the conversation,
not the working tree, carries the history.

Worker: MODEL + reasoning env (VLLM_THINK/OLLAMA_THINK, NUM_PREDICT). Controller (optional):
CONTROL_MODEL (gpt-*/claude-*) + CONTROL_KEY. TURNS (default 4). Records first_solved_turn,
control_tokens, and worker_tokens (hosted-worker usage; ~0 for the free local pod). Usage:
stateful.py <task.json>
"""
import sys, os, json, tempfile, shutil, subprocess
sys.path.insert(0, os.path.dirname(__file__))
from run import (build_prompt, parse_edits, _parse, MODEL, BASE_URL, API_KEY, NUM_PREDICT,
                 VLLM_THINK, OLLAMA_THINK, OLLAMA_HOST, OLLAMA_NUM_CTX)
from run_sandboxed import prepare_host, install_in_sandbox, _docker, WORKROOT

TURNS = int(os.environ.get('TURNS', '4'))
CONTROL_MODEL = os.environ.get('CONTROL_MODEL')
CONTROL_KEY = os.environ.get('CONTROL_KEY', '')
CONTROL_MAX = int(os.environ.get('CONTROL_MAX', '4000'))
CONTROL_STATEFUL = bool(os.environ.get('CONTROL_STATEFUL'))  # controller sees the full trajectory
_WORKER_TOKENS = 0   # accumulates hosted-worker token usage across this task's turns

def _reset(rp, spec):
    for sf in spec['source_files']:
        subprocess.run(['git', '-C', rp, 'checkout', spec['base_sha'], '--', sf], capture_output=True)

def _apply(rp, spec, comp):
    for path, search, replace in parse_edits(comp):
        if path not in set(spec['source_files']):
            continue
        full = os.path.join(rp, path)
        try:
            c = open(full).read()
        except OSError:
            continue
        if search and search in c:
            open(full, 'w').write(c.replace(search, replace, 1))

def _edit_text(comp):
    """The worker's edit only, thinking stripped, for the assistant turn."""
    parts = ['FILE: %s\n<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE' % (p, s, r)
             for p, s, r in parse_edits(comp)]
    return '\n\n'.join(parts) if parts else '(no parseable edit)'

def _test(work, spec):
    r = _docker(work, '/work/venv/bin/python -m pytest -vv --tb=long -p no:cacheprovider '
                + ' '.join(spec['test_files']), network='none', timeout=600)
    out = r.stdout + r.stderr
    res = _parse(out)
    return all(res.get(t) == 'PASSED' for t in spec['fail_to_pass']), out

def _worker(messages):
    if OLLAMA_THINK:
        import urllib.request
        body = {'model': MODEL, 'messages': messages, 'stream': False, 'think': True,
                'options': {'num_predict': NUM_PREDICT, 'num_ctx': OLLAMA_NUM_CTX}}
        req = urllib.request.Request(OLLAMA_HOST + '/api/chat', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=1800) as r:
            return json.load(r).get('message', {}).get('content') or ''
    from openai import OpenAI
    kw = {'api_key': API_KEY, 'timeout': 1800 if VLLM_THINK else 300}
    if BASE_URL:
        kw['base_url'] = BASE_URL
    params = {'model': MODEL, 'messages': messages}
    if VLLM_THINK:
        params['max_tokens'] = NUM_PREDICT
        params['extra_body'] = {'chat_template_kwargs': {'enable_thinking': True}}
    global _WORKER_TOKENS
    r = OpenAI(**kw).chat.completions.create(**params)
    try:
        _WORKER_TOKENS += r.usage.total_tokens
    except Exception:
        pass
    return r.choices[0].message.content or ''

def _control(spec, rp, last_edit, test_out, history=None):
    hist = ('\n\nIts FULL history of prior attempts and the guidance it already received (oldest '
            'first):\n' + history[:6000]) if history else ''
    prompt = (
        'You are the loop controller for a smaller local model fixing a bug. It failed. Do NOT '
        'write the fix. Diagnose why and give one concise, specific piece of guidance for its next '
        'attempt.\n\nThe task:\n' + build_prompt(spec, rp)[:6000] +
        "\n\nIts last edit:\n" + last_edit[:3000] + hist + '\n\nThe test result:\n' + test_out[-4000:] +
        '\n\nIn three to five sentences, name the root cause it missed and the change of approach '
        'to take next' + (', accounting for everything it has already tried above' if history else '') +
        '. Do not write code.')
    if CONTROL_MODEL.startswith(('gpt', 'o')):
        from openai import OpenAI
        r = OpenAI(api_key=CONTROL_KEY, base_url='https://api.openai.com/v1').chat.completions.create(
            model=CONTROL_MODEL, messages=[{'role': 'user', 'content': prompt}],
            max_completion_tokens=CONTROL_MAX)
        return (r.choices[0].message.content or ''), r.usage.prompt_tokens + r.usage.completion_tokens
    import anthropic
    r = anthropic.Anthropic(api_key=CONTROL_KEY).messages.create(
        model=CONTROL_MODEL, max_tokens=CONTROL_MAX, messages=[{'role': 'user', 'content': prompt}])
    return (''.join(b.text for b in r.content if getattr(b, 'type', '') == 'text'),
            r.usage.input_tokens + r.usage.output_tokens)

def run_task(spec):
    os.makedirs(WORKROOT, exist_ok=True)
    work = tempfile.mkdtemp(prefix='st_', dir=WORKROOT)
    log = {'task_id': spec['task_id'], 'worker': MODEL, 'controller': CONTROL_MODEL,
           'turns': TURNS, 'control_tokens': 0}
    try:
        rp = prepare_host(spec, work)
        inst = install_in_sandbox(work)
        if inst.returncode != 0 or '__INSTALL_OK__' not in inst.stdout:
            log['outcome'] = 'install_failed'; return log
        messages = [{'role': 'user', 'content': build_prompt(spec, rp)}]
        for turn in range(1, TURNS + 1):
            _reset(rp, spec)
            comp = _worker(messages); _apply(rp, spec, comp)
            ok, out = _test(work, spec)
            if ok:
                log.update({'outcome': 'solved', 'first_solved_turn': turn}); return log
            messages.append({'role': 'assistant', 'content': _edit_text(comp)})
            if CONTROL_MODEL:
                try:
                    hist = None
                    if CONTROL_STATEFUL:
                        hist = '\n'.join(('WORKER EDIT: ' if m['role'] == 'assistant' else 'GUIDANCE: ')
                                         + m['content'] for m in messages[1:-1])
                    guidance, toks = _control(spec, rp, _edit_text(comp), out, hist)
                except Exception as e:
                    log['outcome'] = 'control_error'; log['error'] = type(e).__name__; return log
                log['control_tokens'] += toks
                messages.append({'role': 'user', 'content': 'A reviewer analyzed that attempt and '
                                 'advises:\n' + guidance + '\n\nApply this and return a corrected '
                                 'search/replace edit in the required format.'})
            else:
                messages.append({'role': 'user', 'content': 'The tests still failed:\n' + out[-6000:]
                                 + '\n\nDiagnose why your edit did not fix it, then return a '
                                 'corrected search/replace edit in the required format.'})
        log.update({'outcome': 'unsolved', 'first_solved_turn': None}); return log
    finally:
        log['worker_tokens'] = _WORKER_TOKENS
        shutil.rmtree(work, ignore_errors=True)

if __name__ == '__main__':
    print(json.dumps(run_task(json.load(open(sys.argv[1]))), indent=1))
