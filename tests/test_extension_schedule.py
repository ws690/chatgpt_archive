import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
from extension_schedule import DailySchedule


class DailyScheduleTests(unittest.TestCase):
    def test_uk_midnight_and_acknowledgement_survive_restart(self):
        for before, midnight, previous, today in (
            ('2026-03-28T23:59:00+00:00', '2026-03-29T00:00:00+00:00', '2026-03-28', '2026-03-29'),
            ('2026-10-24T22:59:00+00:00', '2026-10-24T23:00:00+00:00', '2026-10-24', '2026-10-25'),
        ):
            with tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                launches = []
                launch = lambda: launches.append(True)
                schedule = DailySchedule(state, launch)
                schedule.ack.write_text(json.dumps({'day': previous, 'state': 'started'}))
                schedule.tick(dt.datetime.fromisoformat(before), 0)
                self.assertEqual(launches, [])
                now = dt.datetime.fromisoformat(midnight)
                schedule.tick(now, 60)
                self.assertEqual(launches, [True])
                schedule.ack.write_text(json.dumps({'day': today, 'state': 'paused'}))
                DailySchedule(state, launch).tick(now, 180)
                self.assertEqual(launches, [True])

    def test_catch_up_and_bound_unacknowledged_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            launches = []
            schedule = DailySchedule(Path(directory), lambda: launches.append(True))
            now = dt.datetime.fromisoformat('2026-11-08T07:00:00+00:00')
            for elapsed in (0, 20, 120, 240, 360, 480):
                schedule.tick(now + dt.timedelta(seconds=elapsed), elapsed)
            self.assertEqual(launches, [True, True, True])
            self.assertEqual(json.loads(schedule.status.read_text())['reason'], 'schedule_launch_failed')
