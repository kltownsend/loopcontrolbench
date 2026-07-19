#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
REPOS="more-itertools/more-itertools jpadilla/pyjwt python-hyper/h2 dateutil/dateutil prettytable/prettytable psf/requests pallets/click marshmallow-code/marshmallow"
python3 - <<'PY'
import json,glob,collections
ex=collections.defaultdict(list)
for f in glob.glob('tasks/*.json'):
    if f.endswith('_index.json'): continue
    s=json.load(open(f)); ex[s['repo']].append(s['fix_sha'])
json.dump(ex, open('/tmp/lb_exclude.json','w'))
PY
rm -f grow_done grow_*.log
for repo in $REPOS; do
  ex=$(python3 -c "import json;print(','.join(json.load(open('/tmp/lb_exclude.json')).get('$repo',[])))")
  ( python3 tools/mine.py "$repo" 12 --exclude-shas "$ex" > "grow_${repo##*/}.log" 2>&1 ) &
done
wait
touch grow_done
echo "GROW COMPLETE"
