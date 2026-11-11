"""Launch the signed-in extension once per UK day, with a durable acknowledgement."""
import datetime as dt
import json

from collector import TZ, atomic_json


def read_record(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class DailySchedule:
    def __init__(self, state, launch):
        self.ack = state / 'extension-schedule.json'
        self.status = state / 'extension-status.json'
        self.launch = launch
        self.day = None
        self.attempts = 0
        self.retry_at = 0

    def tick(self, now, monotonic):
        day = now.astimezone(TZ).date().isoformat()
        if read_record(self.ack).get('day') == day:
            return
        if day != self.day:
            self.day, self.attempts, self.retry_at = day, 0, 0
        if monotonic < self.retry_at:
            return
        if read_record(self.status).get('status') == 'running':
            try:
                if now.timestamp() - self.status.stat().st_mtime < 35 * 60:
                    return
            except FileNotFoundError:
                pass
        if self.attempts >= 3:
            atomic_json(self.status, {'status': 'attention_required', 'reason': 'schedule_launch_failed',
                                      'at': now.astimezone(dt.timezone.utc).isoformat()})
            atomic_json(self.ack, {'day': day, 'state': 'launch_failed'})
            return
        self.attempts += 1
        self.retry_at = monotonic + 120
        self.launch()
