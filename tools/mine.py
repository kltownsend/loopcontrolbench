#!/usr/bin/env python3
"""Growth engine. Mine merged bug-fix PRs from a repo, build candidate task specs
(base = merge parent, fix = merge commit, real test + source files from the PR), run
each through the reproduce-or-drop gate, and admit the reproducers. This is how the
pool scales: find new PRs, gate them, keep what reproduces. No inference, no env museum.

Usage: mine.py <owner/repo> [limit] [--exclude-shas sha,sha,...]
"""
import subprocess, json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from gate_sandboxed import gate  # certify in the same container the scorer uses

def gh(path, paginate=False):
    cmd = ['gh', 'api', path] + (['--paginate'] if paginate else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None

def find_prs(repo, limit, exclude):
    # merged PRs whose title mentions a fix; newest first (modern env)
    q = 'repo:%s is:pr is:merged in:title fix' % repo
    res = gh('search/issues?per_page=%d&sort=updated&order=desc&q=%s' % (limit * 4, q.replace(' ', '+')))
    items = (res or {}).get('items', [])
    specs = []
    for it in items:
        n = it['number']
        pr = gh('repos/%s/pulls/%d' % (repo, n))
        if not pr or not pr.get('merged_at'):
            continue
        merge = pr.get('merge_commit_sha')
        if not merge or merge in exclude:
            continue
        files = [f['filename'] for f in (gh('repos/%s/pulls/%d/files' % (repo, n), paginate=True) or [])]
        tests = [f for f in files if 'test' in f.lower() and f.endswith('.py')]
        srcs = [f for f in files if 'test' not in f.lower() and f.endswith('.py')]
        if not tests or not srcs:
            continue
        commit = gh('repos/%s/commits/%s' % (repo, merge))
        parents = (commit or {}).get('parents', [])
        if not parents:
            continue
        specs.append({'pr': n, 'repo': repo, 'base_sha': parents[0]['sha'], 'fix_sha': merge,
                      'test_files': tests, 'source_files': srcs, 'title': it['title']})
        if len(specs) >= limit:
            break
    return specs

if __name__ == '__main__':
    repo = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 6
    exclude = set()
    if '--exclude-shas' in sys.argv:
        exclude = set(sys.argv[sys.argv.index('--exclude-shas') + 1].split(','))
    cands = find_prs(repo, limit, exclude)
    print('candidate merged bug-fix PRs: %d' % len(cands))
    admitted = []
    for s in cands:
        r = gate({**s, 'task_id': '%s-pr%s' % (s['repo'].split('/')[-1], s['pr'])})
        tag = 'ADMIT' if r.get('reproduce') else 'drop:' + str(r.get('drop', '?'))
        print('  PR #%-6d %-22s %s' % (s['pr'], tag, s['title'][:52]))
        if r.get('reproduce'):
            admitted.append({**s, 'fail_to_pass': r['fail_to_pass']})
    print('admitted %d / %d candidates' % (len(admitted), len(cands)))
    out = os.path.join(os.path.dirname(__file__), '..', 'mined_%s.json' % repo.split('/')[-1])
    json.dump(admitted, open(out, 'w'), indent=1)
