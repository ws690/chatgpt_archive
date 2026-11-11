"""Visible-page collector. No private endpoints, tokens, or security bypasses."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
import time
import uuid
import zipfile
from zoneinfo import ZoneInfo

BASE = 'https://chatgpt.com'
LINK = re.compile(r'^/c/([a-zA-Z0-9-]{1,100})$')
STATE = Path(os.getenv('COLLECTOR_STATE', '/state'))
INBOX = Path(os.getenv('COLLECTOR_INBOX', '/inbox'))
TZ = ZoneInfo('Europe/London')


def atomic_json(path, data):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def due(now, last):
    return now.date().isoformat() != last


def normalize(cid, title, messages):
    # Browser versions have their own namespace; never masquerade as official exports.
    mapping = {}
    for i, m in enumerate(messages):
        key = str(i)
        mapping[key] = {'id': key, 'parent': str(i-1) if i else None,
                        'children': [str(i+1)] if i+1 < len(messages) else [],
                        'message': {'author': {'role': m['role']}, 'create_time': i,
                                    'content': {'content_type': 'text', 'parts': [m['text']]}}}
    return {'id': 'browser-' + cid, 'title': '[Browser capture] ' + title,
            'mapping': mapping, 'capture': {'source': 'visible-browser-dom',
            'partial': True, 'url': BASE + '/c/' + cid,
            'limitations': 'Rendered text only; no guarantee of all turns, branches, attachments or account history.'}}


def publish(conversations, report):
    if not conversations:
        raise RuntimeError('no_captures')
    INBOX.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(INBOX).free < 512 * 1024**2:
        raise RuntimeError('low_disk_space')
    name = 'browser-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8] + '.zip'
    with tempfile.NamedTemporaryFile(dir=INBOX, suffix='.partial', delete=False) as f:
        tmp = Path(f.name)
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('conversations.json', json.dumps(conversations, ensure_ascii=False))
            z.writestr('BROWSER-CAPTURE.json', json.dumps(report, indent=2))
        with zipfile.ZipFile(tmp) as z:
            if z.testzip():
                raise RuntimeError('zip_verification_failed')
        with tmp.open('rb') as f:
            os.fsync(f.fileno())
        target = INBOX / name
        os.replace(tmp, target)  # Importer never sees a partially written ZIP.
        return target.name
    finally:
        tmp.unlink(missing_ok=True)


def check_page(page):
    if not page.url.startswith(BASE + '/'):
        raise RuntimeError('authentication_required')
    # Scan page chrome, not conversation text, for common challenge screens.
    title = page.title().lower()
    if any(x in title for x in ('just a moment', 'verify you are human', 'access denied')):
        raise RuntimeError('authentication_challenge')
    if page.locator('input[type="password"], iframe[src*="challenges.cloudflare.com"]').count():
        raise RuntimeError('authentication_challenge')
    if '/auth/' in page.url or '/login' in page.url:
        raise RuntimeError('authentication_required')


def discover(page, max_chats, max_scrolls):
    seen = {}
    stable = 0
    for _ in range(max_scrolls):
        check_page(page)
        before = len(seen)
        for a in page.locator('nav a[href^="/c/"], aside a[href^="/c/"], [data-testid="history"] a[href^="/c/"]').all():
            href = a.get_attribute('href') or ''
            match = LINK.fullmatch(href.split('?')[0])
            if match:
                seen[match.group(1)] = a.inner_text().strip() or 'Untitled'
                if len(seen) >= max_chats:
                    return list(seen.items()), 'chat_limit'
        # Scroll the actual sidebar overflow ancestors, including virtualized lists.
        page.locator('nav, aside, [data-testid="history"]').evaluate_all('''els => {
            const candidates = new Set();
            for (const root of els) for (const e of [root, ...root.querySelectorAll('*')])
                if (e.scrollHeight > e.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(e).overflowY)) candidates.add(e);
            for (const e of candidates) e.scrollTop += Math.max(400, e.clientHeight);
        }''')
        page.wait_for_timeout(1500)
        stable = stable + 1 if len(seen) == before else 0
        if stable >= 3:
            return list(seen.items()), 'sidebar_stable_not_proof_of_completeness'
    return list(seen.items()), 'scroll_limit'


def read_messages(page):
    page.locator('[data-message-author-role="user"], [data-message-author-role="assistant"]').first.wait_for(timeout=30000)
    previous = None
    for _ in range(8):
        check_page(page)
        messages = page.locator('[data-message-author-role="user"], [data-message-author-role="assistant"]').evaluate_all('''els => els.map(e => ({role:e.getAttribute('data-message-author-role'),text:e.innerText.trim()})).filter(m => m.text)''')
        if page.get_by_role('button', name=re.compile('Stop (generating|streaming)', re.I)).count():
            previous = None
            page.wait_for_timeout(2000)
            continue
        if messages and messages == previous:
            return messages
        previous = messages
        page.wait_for_timeout(2000)
    raise RuntimeError('conversation_not_stable')


def browser_settings(playwright):
    profile = os.getenv('BROWSER_PROFILE')
    if profile and profile not in {'profile', 'profile-chrome', 'profile-firefox', 'profile-manual-chrome'}:
        raise RuntimeError('invalid_browser_profile')
    engine = os.getenv('BROWSER_ENGINE', 'chromium')
    if engine == 'firefox':
        return playwright.firefox, profile or 'profile-firefox', {}
    if engine != 'chromium':
        raise RuntimeError('invalid_browser_engine')
    channel = os.getenv('BROWSER_CHANNEL') or None
    return playwright.chromium, profile or ('profile-chrome' if channel == 'chrome' else 'profile'), {
        'channel': channel, 'chromium_sandbox': True}


def preserve_stale_chrome_locks(profile):
    # Caller holds /state/profile.lock; all app-managed browsers share that lock.
    # Container-local process/socket markers cannot survive container replacement.
    # Move symlinks only: never follow them or touch cookie/session databases.
    markers = [profile / name for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie')]
    markers = [p for p in markers if p.is_symlink()]
    if not markers:
        return
    backup = STATE / 'stale-browser-locks' / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700)
    for marker in markers:
        os.replace(marker, backup / marker.name)


def browser(mode):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        engine, profile, options = browser_settings(p)
        if os.getenv('BROWSER_ENGINE', 'chromium') == 'chromium':
            preserve_stale_chrome_locks(STATE / profile)
        context = engine.launch_persistent_context(
            str(STATE / profile), headless=mode != 'login' and os.getenv('HEADLESS', '1') != '0',
            accept_downloads=False, viewport={'width': 1280, 'height': 900},
            locale='en-GB', **options)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(30000)
            page.goto(BASE + '/', wait_until='domcontentloaded', timeout=60000)
            if mode == 'login':
                print('Sign in manually through the SSH-tunnelled VNC session. Stop auth service when finished. Session closes in 20 minutes.', flush=True)
                page.wait_for_timeout(1200000)
                return
            page.wait_for_timeout(5000)
            links, discovery = discover(page, int(os.getenv('MAX_CHATS', '50')), int(os.getenv('MAX_SCROLLS', '20')))
            if not links:
                raise RuntimeError('login_or_sidebar_attention_required')
            captures, failures = [], 0
            for cid, title in links:
                try:
                    page.goto(BASE + '/c/' + cid, wait_until='domcontentloaded', timeout=60000)
                    check_page(page)
                    if page.url.split('?')[0] != BASE + '/c/' + cid:
                        raise RuntimeError('conversation_redirected')
                    captures.append(normalize(cid, title, read_messages(page)))
                except Exception as exc:
                    # Never log exception text, DOM, cookies, URLs, or screenshots.
                    failures += 1
                    if isinstance(exc, RuntimeError) and str(exc).startswith('authentication'):
                        break
                page.wait_for_timeout(3000)
            report = {'schema': 1, 'status': 'partial_capture_with_errors' if failures else 'partial_capture', 'account_complete': False,
                      'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                      'discovered': len(links), 'captured': len(captures), 'failed': failures,
                      'discovery_stop': discovery}
            report['zip'] = publish(captures, report)
            atomic_json(STATE / 'status.json', report)
        finally:
            context.close()


def run_once(mode='collect'):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'profile.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
        try:
            browser(mode)
            return 0
        except Exception as exc:
            reason = str(exc) if isinstance(exc, RuntimeError) and re.fullmatch('[a-z_]+', str(exc)) else 'browser_or_ui_failure'
            atomic_json(STATE / 'status.json', {'status': 'attention_required', 'reason': reason,
                         'at': dt.datetime.now(dt.timezone.utc).isoformat()})
            print('Collector needs attention: ' + reason, flush=True)
            return 1


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.umask(0o077)
    mode = argparse.ArgumentParser()
    mode.add_argument('mode', choices=['collect', 'schedule', 'login'])
    args = mode.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    if args.mode != 'schedule':
        return run_once(args.mode)
    # One scheduler per state folder; a separate lock also excludes manual/profile use.
    with (STATE / 'scheduler.lock').open('a') as scheduler_lock:
        fcntl.flock(scheduler_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            now = dt.datetime.now(TZ)
            stamp = STATE / 'last-attempt.json'
            last = json.loads(stamp.read_text()).get('day') if stamp.exists() else None
            if due(now, last):
                # Record attempt before launch: failures/restarts never hammer login daily.
                atomic_json(stamp, {'day': now.date().isoformat()})
                try:
                    import subprocess
                    process = subprocess.Popen([sys.executable, __file__, 'collect'], start_new_session=True)
                    result = process.wait(timeout=1800)
                    if result:
                        atomic_json(STATE / 'status.json', {'status': 'attention_required', 'reason': 'collector_process_failed', 'exit_code': result, 'at': dt.datetime.now(dt.timezone.utc).isoformat()})
                        print('Daily collection needs attention; see status.json', flush=True)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    atomic_json(STATE / 'status.json', {'status': 'attention_required', 'reason': 'run_timeout'})
            time.sleep(30)


if __name__ == '__main__':
    sys.exit(main())
