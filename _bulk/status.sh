#!/usr/bin/env bash
# One command that answers "is anything actually happening?"
cd "$(dirname "$0")/.."
echo "=== $(date -u +'%H:%M:%S UTC') ==="
n=$(ps -W 2>/dev/null | grep -ci python)
echo "python processes : $n  $([ "$n" -gt 0 ] && echo '(running)' || echo '(NOTHING RUNNING)')"
python -c "
import json,pathlib,sys
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
d=json.loads(pathlib.Path('_bulk/state.json').read_text(encoding='utf-8'))
done=d.get('done',[]); failed=d.get('failed',{})
print(f'chapters done    : {len(done)}/144  {sorted(done)[-8:]}')
print(f'chapters failed  : {len(failed)}  {sorted(failed,key=int)}')
"
echo "last log line    : $(tail -1 _bulk/bulk.log 2>/dev/null | cut -c1-90)"
echo "log age          : $(( ($(date +%s) - $(stat -c %Y _bulk/bulk.log 2>/dev/null || echo 0)) / 60 )) min"
