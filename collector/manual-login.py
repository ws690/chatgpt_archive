"""Ordinary Chrome control test. No Playwright, WebDriver or debugging endpoint."""
import fcntl
from pathlib import Path
import signal
import subprocess
import sys

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
with Path('/state/profile.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    process = subprocess.Popen(['google-chrome', '--user-data-dir=/state/profile-manual-chrome', 'https://chatgpt.com/'])
    print('Ordinary Chrome opened for manual sign-in. No automation controller is attached. Closes in 20 minutes.', flush=True)
    try:
        process.wait(timeout=1200)
    except subprocess.TimeoutExpired:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
