import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/'collector'))
import extension_host as host
import chat_files
import collector

class NativeWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.old=(collector.STATE,collector.INBOX)
        collector.STATE=self.root/'state';collector.STATE.mkdir()
        collector.INBOX=self.root/'inbox';collector.INBOX.mkdir()
    def tearDown(self):
        collector.STATE,collector.INBOX=self.old
        self.temp.cleanup()
    def payload(self):
        return {'cmd':'publish','captures':[{'id':'abc-123','title':'Fixture','messages':[{'role':'user','text':'quartz kitchen'},{'role':'assistant','text':'Oak cabinets'}]}],'report':{'discovered':1,'failed':0,'discovery_stop':'chat_limit','discovery_scope':'sidebar'}}
    def test_native_publish_import_search_and_existing_preservation(self):
        result=host.execute(self.payload());self.assertTrue(result['ok'])
        self.assertFalse(result['report']['account_complete'])
        with zipfile.ZipFile(collector.INBOX/result['report']['zip']) as z:
            data=json.loads(z.read('conversations.json'))
            self.assertEqual(data[0]['capture']['source'],'visible-browser-extension')
        spec=importlib.util.spec_from_file_location('extension_test_backup',ROOT/'app/backup.py')
        backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
        backup.ROOT=self.root/'archive';backup.init()
        sentinel=backup.ROOT/'archive/existing';sentinel.write_text('keep')
        backup.import_zip(collector.INBOX/result['report']['zip']);backup.verify()
        with sqlite3.connect(backup.ROOT/'database/index.sqlite3') as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE messages MATCH 'quartz'").fetchone()[0],1)
            saved = backup.ROOT / db.execute('SELECT path FROM versions').fetchone()[0]
        self.assertTrue(saved.name.startswith('Fixture -- '))
        self.assertEqual(saved.parent.name, 'Fixture')
        self.assertIn('quartz kitchen', saved.with_suffix('.md').read_text())
        self.assertIn('Oak cabinets', saved.with_suffix('.md').read_text())
        self.assertEqual(sentinel.read_text(),'keep')

        legacy = backup.ROOT / 'archive/browser-abc-123'
        chat = backup.ROOT / 'archive/Fixture'
        chat.rmdir()
        saved.parent.rename(legacy)
        with sqlite3.connect(backup.ROOT/'database/index.sqlite3') as db:
            db.execute('DELETE FROM chat_folders')
            db.execute('UPDATE versions SET path=?', (str((legacy/saved.name).relative_to(backup.ROOT)),))
        backup.name_existing_folders()
        backup.name_existing_folders()
        backup.tidy_chats()
        backup.tidy_chats()
        self.assertTrue(saved.is_file())
        self.assertFalse(legacy.exists())
        master = chat / 'Fixture.md'
        self.assertEqual(list(chat.iterdir()), [master])
        self.assertIn('Partial transcript', master.read_text())

        payload = self.payload()
        payload['captures'][0]['history'] = 'visible_start'
        payload['captures'][0]['messages'][0]['text'] = 'Full starting prompt'
        complete = host.execute(payload)['report']['zip']
        backup.import_zip(collector.INBOX/complete)
        backup.refresh_chat_masters()
        contents = master.read_bytes()
        self.assertIn(b'Full starting prompt', contents)
        payload['captures'][0]['history'] = 'gap'
        payload['captures'][0]['messages'][0]['text'] = 'Incomplete new capture'
        partial = host.execute(payload)['report']['zip']
        backup.import_zip(collector.INBOX/partial)
        backup.refresh_chat_masters()
        self.assertEqual(master.read_bytes(), contents)
        image = b'\x89PNG\r\n\x1a\nfixture'
        payload = {'cmd':'save_image','id':'abc-123','title':'Fixture','index':1,
                   'data':base64.b64encode(image).decode()}
        staging = backup.ROOT / 'archive' / 'ChatGPT Files'
        with patch.object(chat_files, 'ROOT', staging):
            self.assertTrue(host.execute(payload)['saved'])
            backup.place_chat_images()
            target, = chat.glob('Images/*.png')
            self.assertEqual(target.read_bytes(), image)
            self.assertFalse(list(staging.glob('.incoming/*/*.png')))
            self.assertFalse(host.execute(payload)['saved'])

        with sqlite3.connect(backup.ROOT/'database/index.sqlite3') as db:
            self.assertEqual(backup.chat_folder(db,'another','fixture').name, 'fixture (2)')
    def test_invalid_payload_never_writes_zip(self):
        for change in ['path','role','empty']:
            p=self.payload()
            if change=='path':p['captures'][0]['id']='../../database'
            if change=='role':p['captures'][0]['messages'][0]['role']='system'
            if change=='empty':p['captures']=[]
            with self.assertRaises(ValueError):host.execute(p)
        self.assertEqual(list(collector.INBOX.iterdir()),[])

    def test_partial_run_preserves_a_safe_connection_failure_reason(self):
        payload = self.payload()
        payload['report'].update(discovered=2, failed=1, reason='chatgpt_unreachable')
        report = host.execute(payload)['report']
        self.assertEqual(report['status'], 'partial_capture_with_errors')
        self.assertEqual(report['reason'], 'chatgpt_unreachable')
    def test_layout_diagnostic_filters_text_and_unknown_fields(self):
        host.execute({'cmd':'diagnostic','layout':{'counts':{'author_nodes':4,'secret':'private text'},'attributes':{'data-message-role':4,'password':'private value'},'author_attributes':{'data-message-role':{'user':2,'assistant':2,'private':'private'}} ,'text':'private chat'}})
        saved=(collector.STATE/'extension-layout.json').read_text()
        self.assertNotIn('private',saved)
        self.assertEqual(json.loads(saved)['counts'],{'author_nodes':4})

    def test_native_frame_boundaries(self):
        raw=json.dumps({'cmd':'ping'}).encode()
        self.assertEqual(host.read_message(io.BytesIO(struct.pack('<I',len(raw))+raw)),{'cmd':'ping'})
        for raw in [b'\x01',struct.pack('<I',host.MAX_FRAME+1),struct.pack('<I',100)+b'{}']:
            with self.assertRaises(ValueError):host.read_message(io.BytesIO(raw))
    def test_chat_image_preserves_bytes_and_deduplicates(self):
        image = b'\x89PNG\r\n\x1a\nfixture'
        payload = {'cmd':'save_image','id':'abc-123','title':'Kitchen / plans','index':1,
                   'data':base64.b64encode(image).decode()}
        with patch.object(chat_files, 'ROOT', self.root):
            self.assertTrue(host.execute(payload)['saved'])
            self.assertFalse(host.execute(payload)['saved'])
            saved, = self.root.glob('.incoming/*/*.png')
            self.assertEqual(saved.read_bytes(), image)
            self.assertTrue(saved.parent.name.startswith('Kitchen _ plans'))
            payload['data'] = base64.b64encode(b'<!doctype html>').decode()
            with self.assertRaises(ValueError):
                host.execute(payload)
            payload['id'] = '../other'
            with self.assertRaises(ValueError):
                host.execute(payload)

    def test_wrong_origin_cannot_write(self):
        env=dict(os.environ,COLLECTOR_STATE=str(collector.STATE),COLLECTOR_INBOX=str(collector.INBOX))
        data=json.dumps(self.payload()).encode()
        r=subprocess.run([sys.executable,str(ROOT/'collector/extension_host.py'),'chrome-extension://wrong/'],input=struct.pack('<I',len(data))+data,capture_output=True,env=env)
        self.assertNotEqual(r.returncode,0)
        self.assertIn(b'invalid_origin',r.stdout)
        self.assertEqual(list(collector.INBOX.iterdir()),[])

@unittest.skipUnless(os.getenv('EXTENSION_E2E')=='1','requires built extension image and explicit isolated test paths')
class ExtensionBrowserTests(unittest.TestCase):
    def test_auth_challenge_keeps_tab_without_zip(self):
        import time
        from playwright.sync_api import sync_playwright
        inbox=Path(os.environ['COLLECTOR_INBOX']);inbox.mkdir(parents=True,exist_ok=True)
        before=set(inbox.glob('*.zip'))
        identity=Path('/extension/extension-id.txt').read_text().strip()
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as p:
            context=p.chromium.launch_persistent_context(profile,channel='chromium',headless=True,chromium_sandbox=True,args=['--disable-extensions-except=/extension','--load-extension=/extension'])
            context.route('https://chatgpt.com/**',lambda r:r.fulfill(body='<title>Just a moment...</title><main>Verification needed</main>',content_type='text/html'))
            page=context.new_page();page.goto(f'chrome-extension://{identity}/runner.html?limit=3&mode=manual')
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                text=page.locator('#status').inner_text()
                if 'authentication_challenge' in text and page.locator('#cancel').is_disabled():break
                page.wait_for_timeout(1000)
            else:raise AssertionError(page.locator('#status').inner_text())
            self.assertTrue(any(t.url.startswith('https://chatgpt.com/') for t in context.pages))
            self.assertIn('Complete sign-in or verification',page.locator('#status').inner_text())
            self.assertEqual(set(inbox.glob('*.zip')),before)
            context.close()

    def test_extension_ui_to_native_zip(self):
        from playwright.sync_api import expect, sync_playwright
        inbox=Path(os.environ['COLLECTOR_INBOX']);inbox.mkdir(parents=True,exist_ok=True)
        ext=Path('/extension')
        identity=(ext/'extension-id.txt').read_text().strip()
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as p:
            context=p.chromium.launch_persistent_context(profile,channel='chromium',headless=True,chromium_sandbox=True,args=['--disable-extensions-except=/extension','--load-extension=/extension'])
            def route(req):
                path=req.request.url.split('chatgpt.com',1)[-1]
                body='<title>Fixture</title><input type="password" hidden><nav><a href="https://chatgpt.com/g/g-p-fixture/c/abc-123">Kitchen</a></nav>'
                if '/c/' in path:
                    body += '''<main style="height:350px;overflow:auto">
                        <button aria-label="Stop generating" hidden></button>
                        <div id="turns" style="min-height:1000px;padding-top:700px">
                            <div><h5 data-conversation-role="user">You said:</h5><div>quartz kitchen</div></div>
                            <div><h6 data-conversation-role="assistant">ChatGPT said:</h6>
                                <div><div id="answer"></div><img id="attachment"></div>
                            </div>
                        </div>
                    </main>
                    <script>
                        const main = document.querySelector("main");
                        const turns = document.querySelector("#turns");
                        const image = Uint8Array.from([137,80,78,71,13,10,26,10,12]);
                        document.querySelector("#attachment").src =
                            URL.createObjectURL(new Blob([image], {type:"image/png"}));
                        main.scrollTop = main.scrollHeight;
                        main.addEventListener("scroll", () => {
                            if (main.scrollTop > 200 || document.querySelector("#first")) return;
                            turns.insertAdjacentHTML("afterbegin", `
                                <div id="first"><h5 data-conversation-role="user">You said:</h5><div>original prompt</div></div>
                                <div><h6 data-conversation-role="assistant">ChatGPT said:</h6><div>first reply</div></div>
                            `);
                        });
                        setTimeout(() => document.querySelector("#answer").textContent = "Oak cabinets", 8000);
                    </script>'''
                req.fulfill(body=body,content_type='text/html')
            context.route('https://chatgpt.com/**',route)
            existing_chat=context.new_page()
            existing_chat.goto('https://chatgpt.com/g/g-p-fixture/c/abc-123')
            page=context.new_page()
            page.goto(f'chrome-extension://{identity}/native-check.html')
            expect(page.locator('#status')).to_contain_text('"ok":true', timeout=20000)
            page.goto(f'chrome-extension://{identity}/runner.html?limit=3&mode=manual')
            import time
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                text=page.locator('#status').inner_text()
                if 'partial_capture' in text:break
                if 'attention_required' in text:raise AssertionError(text)
                page.wait_for_timeout(1000)
            else:raise AssertionError('Extension did not finish: '+page.locator('#status').inner_text())
            status=json.loads(page.locator('#status').inner_text())
            self.assertEqual(status['captured'],1)
            self.assertEqual(status['history']['visible_start'],1, status)
            self.assertEqual(status['images'], {'saved':1,'already_saved':0,'skipped':0})
            image, = Path('/chat-files').glob('.incoming/Kitchen*/*.png')
            self.assertEqual(image.read_bytes(), b'\x89PNG\r\n\x1a\n\x0c')
            self.assertFalse(status['account_complete'])
            with zipfile.ZipFile(inbox/status['zip']) as archive:
                records = json.loads(archive.read('conversations.json'))
                messages = [node['message'] for node in records[0]['mapping'].values()]
                self.assertEqual(
                    [(m['author']['role'], m['content']['parts']) for m in messages],
                    [('user', ['original prompt']), ('assistant', ['first reply']),
                     ('user', ['quartz kitchen']), ('assistant', ['Oak cabinets'])],
                )
            self.assertFalse(existing_chat.is_closed())
            self.assertEqual(existing_chat.url,'https://chatgpt.com/g/g-p-fixture/c/abc-123')
            expect(page.locator('#cancel')).to_be_disabled()
            before = set(inbox.glob('*.zip'))
            page.goto(f'chrome-extension://{identity}/runner.html?limit=50&mode=manual')
            expect(page.locator('#status')).to_contain_text('"up_to_date"', timeout=90000)
            self.assertEqual(set(inbox.glob('*.zip')), before)
            context.close()

if __name__=='__main__':unittest.main()
