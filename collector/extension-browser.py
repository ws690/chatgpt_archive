"""Resident ordinary Chrome and NAS-clock scheduling. No automation driver."""
import datetime as dt
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import collector
from extension_schedule import DailySchedule

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
os.umask(0o077)
with Path('/state/profile.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    collector.preserve_stale_chrome_locks(Path('/state/profile-manual-chrome'))
    url='chrome://extensions/' if os.getenv('EXTENSION_SETUP')=='1' else 'https://chatgpt.com/'
    requested=os.getenv('EXTENSION_START_URL','')
    if requested.startswith('https://chatgpt.com/c/') and '?' not in requested and '#' not in requested:
        url=requested
    command=['google-chrome','--user-data-dir=/state/profile-manual-chrome']
    browser=subprocess.Popen([*command,url])
    print('Ordinary NAS browser running.',flush=True)
    try:
        if os.getenv('EXTENSION_SETUP')=='1':
            sys.exit(browser.wait())
        identity=Path('/extension/extension-id.txt').read_text().strip()
        if len(identity)!=32 or any(letter not in 'abcdefghijklmnop' for letter in identity):
            raise ValueError('Invalid archive extension ID')
        daily_url=f'chrome-extension://{identity}/runner.html?limit=50&mode=daily'

        def launch():
            try:
                subprocess.run([*command,daily_url], timeout=25, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=True)
            except (OSError, subprocess.SubprocessError):
                print('Daily browser handoff failed. Will retry if unacknowledged.',flush=True)

        schedule=DailySchedule(collector.STATE,launch)
        while True:
            try:
                sys.exit(browser.wait(timeout=20))
            except subprocess.TimeoutExpired:
                schedule.tick(dt.datetime.now(dt.timezone.utc),time.monotonic())
    finally:
        if browser.poll() is None:
            browser.terminate()
            try:browser.wait(timeout=10)
            except subprocess.TimeoutExpired:browser.kill();browser.wait()
