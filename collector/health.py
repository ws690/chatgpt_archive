import datetime as dt
import json
from pathlib import Path
import sys
try:
    status = json.loads(Path('/state/status.json').read_text())
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(status['captured_at'])
    sys.exit(0 if status['status'] == 'partial_capture' and age.total_seconds() < 36*3600 else 1)
except (OSError, ValueError, KeyError):
    sys.exit(1)
