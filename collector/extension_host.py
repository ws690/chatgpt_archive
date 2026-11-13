#!/usr/bin/env python3
"""Chrome native host: validate and atomically publish local capture ZIPs only."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import collector as archive
from chat_files import save_image, stage_document

MAX_FRAME = 16 * 1024 * 1024
SAFE_ERRORS = {'authentication_required','authentication_challenge','chatgpt_unreachable','route_error','no_visible_chats','no_messages','conversation_not_stable','oversized_conversation','run_cancelled','run_timeout','local_writer_unavailable','page_or_browser_error','native_publish_failed','another_run_active','running'}


def add_file_counts(source, report, field):
    counts = source.get(field)
    if isinstance(counts, dict):
        report[field] = {key: counts[key] for key in ('saved', 'already_saved', 'skipped', 'deferred')
                         if type(counts.get(key)) is int and 0 <= counts[key] <= 10000}
        reasons = counts.get('skipReasons')
        allowed = ('viewer_unavailable','download_control_unavailable','download_not_seen','ambiguous_download',
                   'download_incomplete','download_interrupted','too_large','download_unavailable','writer_rejected')
        if field == 'documents' and isinstance(reasons, dict):
            report[field]['skipReasons'] = {key: reasons[key] for key in allowed
                                            if type(reasons.get(key)) is int and 0 <= reasons[key] <= 10000}


def execute(message):
    cmd = message.get('cmd')
    archive.STATE.mkdir(parents=True, exist_ok=True)
    if cmd == 'ping':
        return {'ok': True, 'writer_ready': True, 'free_space_ok': archive.INBOX.is_dir() and shutil.disk_usage(archive.INBOX).free >= 512*1024**2}
    if cmd == 'schedule':
        state = message.get('state')
        if state not in ('started', 'paused'):
            raise ValueError('invalid_schedule_state')
        now = dt.datetime.now(dt.timezone.utc)
        archive.atomic_json(archive.STATE/'extension-schedule.json',
                            {'day': now.astimezone(archive.TZ).date().isoformat(),
                             'state': state, 'at': now.isoformat()})
        return {'ok': True}
    if cmd == 'document_issue':
        cid, filename, failure = (message.get(key) for key in ('id', 'filename', 'failure'))
        if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', cid):
            raise ValueError('invalid_id')
        if not isinstance(filename, str) or not 1 <= len(filename) <= 191:
            raise ValueError('invalid_filename')
        if not isinstance(failure, str) or not re.fullmatch(r'[a-z_]{1,40}', failure):
            raise ValueError('invalid_failure')
        path = archive.STATE / 'extension-document-issues.jsonl'
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as log:
            log.write(json.dumps({'at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                  'id': cid, 'filename': filename, 'reason': failure}) + '\n')
        return {'ok': True}
    if cmd == 'stage_document':
        return stage_document(message)
    if cmd == 'save_image':
        return save_image(message)
    if cmd == 'diagnostic':
        layout=message.get('layout') or {}
        names={'author_nodes','user_role_nodes','assistant_role_nodes','streaming_buttons','visible_streaming_buttons','turn_nodes','conversation_turns','articles','role_articles','message_ids','markdown_nodes','user_testids','assistant_testids','main_nodes','iframes','shadow_roots','you_headings','assistant_headings'}
        counts={k:v for k,v in (layout.get('counts') or {}).items() if k in names and type(v) is int and 0<=v<=100000}
        attributes={k:v for k,v in (layout.get('attributes') or {}).items() if re.fullmatch(r'data-[a-z-]{1,50}',k) and type(v) is int and 0<=v<=100000}
        authors={}
        for k,v in (layout.get('author_attributes') or {}).items():
            if re.fullmatch(r'data-[a-z-]{1,50}',k) and isinstance(v,dict):
                authors[k]={r:n for r,n in v.items() if r in ('user','assistant') and type(n) is int and 0<=n<=100000}
        result={'at':dt.datetime.now(dt.timezone.utc).isoformat(),'counts':counts,'attributes':attributes,'author_attributes':authors}
        archive.atomic_json(archive.STATE/'extension-layout.json',result)
        return {'ok':True}
    if cmd in ('read_history_resume', 'write_history_resume'):
        cid = message.get('id')
        if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', cid):
            raise ValueError('invalid_history_chat')
        folder = archive.STATE / 'history-resume'
        path = folder / (cid + '.json')
        if folder.is_symlink() or path.is_symlink():
            raise ValueError('unsafe_history_resume')
        if cmd == 'read_history_resume':
            return {'ok': True, 'resume': json.loads(path.read_text()) if path.is_file() else None}
        resume = message.get('resume')
        if (not isinstance(resume, dict) or not isinstance(resume.get('messages'), list)
                or len(json.dumps(resume).encode()) > 4 * 1024**2):
            raise ValueError('invalid_history_resume')
        if path.is_file():
            previous = json.loads(path.read_text())
            if (previous.get('fingerprint') == resume.get('fingerprint')
                    and len(previous.get('messages', [])) > len(resume['messages'])):
                return {'ok': True}
        folder.mkdir(mode=0o700, exist_ok=True)
        archive.atomic_json(path, resume)
        return {'ok': True}
    if cmd == 'progress':
        phase = message.get('phase')
        phases = {'discovery','opening_chat','stabilizing','outputs','history','publishing'}
        if not isinstance(phase, str) or phase not in phases:
            raise ValueError('invalid_progress_phase')
        progress = {'phase':phase,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
        for key in ('chats','turns'):
            value = message.get(key)
            if type(value) is int and 0 <= value <= 10000:
                progress[key] = value
        add_file_counts(message, progress, 'documents')
        archive.atomic_json(archive.STATE/'extension-progress.json', progress)
        return {'ok':True}
    if cmd == 'status':
        source = message.get('report') or {}
        reason = source.get('reason')
        unchanged = source.get('unchanged')
        if source.get('status') == 'up_to_date' and type(unchanged) is int and 1 <= unchanged <= 50:
            report = {'status':'up_to_date','unchanged':unchanged,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
        else:
            report = {'status':'running' if source.get('status') == 'running' else 'attention_required', 'reason':reason if reason in SAFE_ERRORS else 'page_or_browser_error','at':dt.datetime.now(dt.timezone.utc).isoformat()}
        add_file_counts(source, report, 'documents')
        checks=source.get('checks')
        if isinstance(checks,dict) and type(checks.get('verification_title')) is bool and type(checks.get('visible_check_elements')) is int and 0<=checks['visible_check_elements']<=100:
            report['checks']={'verification_title':checks['verification_title'],'visible_check_elements':checks['visible_check_elements']}
        archive.atomic_json(archive.STATE/'extension-status.json', report)
        return {'ok':True,'report':report}
    if cmd != 'publish':
        raise ValueError('invalid_command')
    captures = message.get('captures')
    if not isinstance(captures,list) or not 1 <= len(captures) <= 50:
        raise ValueError('invalid_capture_count')
    conversations, ids = [], set()
    for item in captures:
        if not isinstance(item,dict):raise ValueError('invalid_capture')
        cid, title, messages = item.get('id'), item.get('title'), item.get('messages')
        if not isinstance(cid,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}',cid) or cid in ids:raise ValueError('invalid_id')
        if not isinstance(title,str) or len(title)>1024:raise ValueError('invalid_title')
        if not isinstance(messages,list) or not 1 <= len(messages) <= 10000:raise ValueError('invalid_messages')
        clean=[]
        for m in messages:
            if not isinstance(m,dict) or m.get('role') not in ('user','assistant') or not isinstance(m.get('text'),str) or not 0<len(m['text'])<=262144:raise ValueError('invalid_message')
            clean.append({'role':m['role'],'text':m['text']})
        if sum(len(m['text']) for m in clean)>1048576:raise ValueError('oversized_conversation')
        ids.add(cid)
        c=archive.normalize(cid,title,clean)
        c['capture']['source']='visible-browser-extension'
        history = item.get('history')
        if history in ('visible_start', 'gap', 'limit', 'stalled'):
            c['capture']['history'] = history
        conversations.append(c)
    source=message.get('report') or {}
    failed=source.get('failed',0)
    discovered=source.get('discovered',len(captures))
    if type(failed) is not int or not 0<=failed<=50 or type(discovered) is not int or not len(captures)<=discovered<=20000:raise ValueError('invalid_report')
    stops={'chat_limit','scroll_limit','sidebar_stable_not_proof_of_completeness','requested_chat','project_visible_links'}
    scopes={'sidebar','visible_chat_links','requested_chat','project'}
    report={'schema':1,'source':'nas-browser-extension','status':'partial_capture_with_errors' if failed else 'partial_capture','account_complete':False,'captured_at':dt.datetime.now(dt.timezone.utc).isoformat(),'captured':len(captures),'failed':failed,'discovered':discovered,'discovery_stop':source.get('discovery_stop') if source.get('discovery_stop') in stops else 'unknown','discovery_scope':source.get('discovery_scope') if source.get('discovery_scope') in scopes else 'unknown'}
    add_file_counts(source, report, 'images')
    add_file_counts(source, report, 'documents')
    history_states = [chat['capture'].get('history') for chat in conversations]
    if any(history_states):
        report['history'] = {state:history_states.count(state) for state in
                             ('visible_start', 'gap', 'limit', 'stalled')}
    unchanged = source.get('unchanged')
    if type(unchanged) is int and 0 <= unchanged <= 50:
        report['unchanged'] = unchanged
    reason = source.get('reason')
    if failed and isinstance(reason, str) and reason in SAFE_ERRORS:
        report['reason'] = reason
    report['zip']=archive.publish(conversations,report)
    archive.atomic_json(archive.STATE/'extension-status.json',report)
    return {'ok':True,'report':report}


def read_exact(stream,n):
    data=bytearray()
    while len(data)<n:
        b=stream.read(n-len(data))
        if not b:raise ValueError('truncated_frame')
        data.extend(b)
    return bytes(data)


def read_message(stream):
    first=stream.read(1)
    if not first:return None
    length=struct.unpack('<I',first+read_exact(stream,3))[0]
    if not 0<length<=MAX_FRAME:raise ValueError('invalid_frame_size')
    value=json.loads(read_exact(stream,length))
    if not isinstance(value,dict):raise ValueError('invalid_message')
    return value


def write_message(stream,value):
    data=json.dumps(value,ensure_ascii=False).encode()
    stream.write(struct.pack('<I',len(data))+data)
    stream.flush()


def main():
    os.umask(0o077)
    manifest=json.loads(Path(__file__).with_name('native-host.json').read_text())
    allowed={origin.rstrip('/') for origin in manifest['allowed_origins']}
    if len(sys.argv)<2 or sys.argv[1].rstrip('/') not in allowed:
        write_message(sys.stdout.buffer,{'ok':False,'error':'invalid_origin'})
        return 1
    while True:
        try:
            message=read_message(sys.stdin.buffer)
            if message is None:return 0
            result=execute(message)
        except Exception:
            # Never print exception text, incoming content, paths or credentials.
            write_message(sys.stdout.buffer,{'ok':False,'error':'native_writer_rejected_or_unavailable'})
            return 1
        write_message(sys.stdout.buffer,result)


if __name__=='__main__':
    sys.exit(main())
