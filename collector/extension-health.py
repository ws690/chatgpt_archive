import datetime as dt
import json
from pathlib import Path
import sys
try:
 s=json.loads(Path('/state/extension-status.json').read_text())
 status=s['status']
 timestamp=s['at'] if status=='up_to_date' else s['captured_at']
 age=dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(timestamp)
 sys.exit(0 if status in ('partial_capture','up_to_date') and 0<=age.total_seconds()<36*3600 else 1)
except (OSError,ValueError,KeyError):sys.exit(1)
