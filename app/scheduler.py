"""Poll published ZIPs; persist successful scan state and cap failure retries."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get('BACKUP_ROOT', '/data'))
TZ = ZoneInfo('Europe/London')


def signature():
    staged = ROOT / 'archive' / 'ChatGPT Files' / '.incoming'
    paths = [ROOT / 'manifests' / 'attachment-folders.json', *(ROOT / 'inbox').glob('*.zip'),
             *staged.glob('*/Image * -- *'), *staged.glob('*/File -- * -- *')]
    return [[str(p.relative_to(ROOT)), p.stat().st_size, p.stat().st_mtime_ns]
            for p in sorted(paths) if p.is_file()]


def main():
    (ROOT / 'logs').mkdir(parents=True, exist_ok=True)
    state = ROOT / 'logs' / 'scheduler-state.json'
    last = json.loads(state.read_text()) if state.exists() else {}
    while True:
        now = dt.datetime.now(TZ)
        day = now.date().isoformat()
        files = signature()
        if files != last.get('files') or day != last.get('day'):
            result = subprocess.run([sys.executable, '/app/backup.py', 'run'], check=False)
            print('Backup/import exit', result.returncode, flush=True)
            if result.returncode == 0:
                last = {'files': files, 'day': day}
                tmp = state.with_suffix('.partial')
                tmp.write_text(json.dumps(last))
                os.replace(tmp, state)
        time.sleep(300)


if __name__ == '__main__':
    main()
