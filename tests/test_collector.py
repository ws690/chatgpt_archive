import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
c = load('collector', HERE / 'collector/collector.py')
b = load('backup', HERE / 'app/backup.py')

class CollectorTests(unittest.TestCase):
    def test_dst_and_daily_restart(self):
        tz = ZoneInfo('Europe/London')
        for day in ['2026-03-29', '2026-10-25']:
            now = dt.datetime.fromisoformat(day+'T00:00').replace(tzinfo=tz)
            self.assertTrue(c.due(now, None))
            self.assertFalse(c.due(now, day))

    def test_archive_roundtrip_dedup_search_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            b.ROOT = root / 'archive'
            c.INBOX = b.ROOT / 'inbox'
            b.init()
            # Existing unrelated archive content must survive collector/import.
            sentinel = b.ROOT / 'archive' / 'existing.txt'
            sentinel.write_text('keep me')
            original = c.normalize('abc-123', 'Kitchen', [{'role':'user','text':'quartz kitchen'}, {'role':'assistant','text':'Oak cabinets'}])
            report = {'account_complete':False, 'status':'partial_capture'}
            name = c.publish([original], report)
            self.assertEqual(list(c.INBOX.glob('*.partial')), [])
            with zipfile.ZipFile(c.INBOX/name) as z:
                self.assertFalse(json.loads(z.read('BROWSER-CAPTURE.json'))['account_complete'])
            self.assertTrue(b.import_zip(c.INBOX/name))
            self.assertFalse(b.import_zip(c.INBOX/name))
            # A second daily capture of unchanged content must not duplicate versions.
            second = c.publish([original], report)
            b.import_zip(c.INBOX/second)
            b.verify()
            with sqlite3.connect(b.ROOT/'database/index.sqlite3') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE messages MATCH 'quartz'").fetchone()[0], 1)
            self.assertEqual(sentinel.read_text(), 'keep me')
            b.snapshot(root/'second-storage')
            generation = next((root/'second-storage').iterdir())
            b.restore(generation, root/'restored')
            b.ROOT = root/'restored'
            b.verify()

    def test_stale_marker_cleanup_never_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = c.STATE
            c.STATE = Path(temp)
            try:
                profile = c.STATE/'profile-manual-chrome'
                profile.mkdir()
                outside = c.STATE/'session-sentinel'
                outside.write_text('preserve')
                marker = profile/'SingletonLock'
                marker.symlink_to(outside)
                c.preserve_stale_chrome_locks(profile)
                self.assertFalse(marker.is_symlink())
                self.assertEqual(outside.read_text(), 'preserve')
                saved = list((c.STATE/'stale-browser-locks').glob('*/SingletonLock'))
                self.assertEqual(len(saved), 1)
                self.assertTrue(saved[0].is_symlink())
            finally:
                c.STATE = previous

    def test_profile_lock_excludes_another_collector(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temp:
            previous = c.STATE
            c.STATE = Path(temp)
            try:
                with (c.STATE/'profile.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertEqual(c.run_once(), 75)
            finally:
                c.STATE = previous

    def test_browser_failure_sets_safe_attention_status(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            previous = c.STATE
            c.STATE = Path(temp)
            try:
                with patch.object(c, 'browser', side_effect=ValueError('private session detail')):
                    self.assertEqual(c.run_once(), 1)
                status = (c.STATE/'status.json').read_text()
                self.assertIn('attention_required', status)
                self.assertNotIn('private session detail', status)
            finally:
                c.STATE = previous

    def test_empty_capture_never_publishes(self):
        with tempfile.TemporaryDirectory() as temp:
            c.INBOX = Path(temp)
            with self.assertRaises(RuntimeError): c.publish([], {})
            self.assertEqual(list(c.INBOX.iterdir()), [])

class BrowserFixtureTests(unittest.TestCase):
    def test_visible_sidebar_and_message_dom(self):
        # No login, cookies, account, or network calls in fixture test.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            engine, _, options = c.browser_settings(p)
            browser = engine.launch(**options)
            page = browser.new_page()
            html = '''<title>Fixture</title><nav style="height:100px;overflow:auto"><a href="/c/abc-123">Kitchen</a><a href="https://evil.example/c/no">Ignore</a></nav><main><div data-message-author-role="user">quartz kitchen</div><div data-message-author-role="assistant">Oak cabinets</div></main>'''
            page.route('https://chatgpt.com/**', lambda route: route.fulfill(body=html, content_type='text/html'))
            page.goto('https://chatgpt.com/')
            links, stop = c.discover(page, 50, 5)
            self.assertEqual(links, [('abc-123','Kitchen')])
            self.assertEqual(len(c.read_messages(page)), 2)
            page.set_content('<title>Just a moment...</title>')
            with self.assertRaisesRegex(RuntimeError,'authentication_challenge'): c.check_page(page)
            browser.close()

if __name__ == '__main__': unittest.main()
