#!/usr/bin/env python3
import argparse, datetime as dt, hashlib, json, logging, os, re, shutil, sqlite3, sys, tempfile, unicodedata, zipfile
from pathlib import Path
ROOT=Path(os.environ.get('BACKUP_ROOT','/data'))
LOG=logging.getLogger('backup')
def digest(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def init():
 for d in ('inbox','originals','archive','database','logs','manifests'): (ROOT/d).mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect(ROOT/'database/index.sqlite3')
 db.executescript('''CREATE TABLE IF NOT EXISTS exports(hash TEXT PRIMARY KEY, filename TEXT, imported_at TEXT, conversations INTEGER);
 CREATE TABLE IF NOT EXISTS versions(id TEXT, hash TEXT, title TEXT, updated TEXT, path TEXT, PRIMARY KEY(id,hash));
 CREATE TABLE IF NOT EXISTS chat_folders(id TEXT PRIMARY KEY, folder TEXT NOT NULL UNIQUE);
 CREATE TABLE IF NOT EXISTS chat_masters(id TEXT PRIMARY KEY, version TEXT NOT NULL, latest INTEGER NOT NULL);
 CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(id UNINDEXED, title, content);''')
 db.close()
def safe_name(s):return re.sub(r'[^a-zA-Z0-9_-]','_',str(s))[:100] or 'untitled'
def title_name(title):
    title = unicodedata.normalize('NFC', title.removeprefix('[Browser capture] '))
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', title)
    title = title.encode('utf-8')[:150].decode('utf-8', 'ignore').strip(' .') or 'Untitled'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', title, re.I):
        title = '_' + title
    return title


def chat_folder(db, cid, title):
    archive = ROOT / 'archive'
    row = db.execute('SELECT folder FROM chat_folders WHERE id=?', (cid,)).fetchone()
    if row:
        name = row[0]
    else:
        occupied = {p.name.casefold() for p in archive.iterdir()}
        occupied.update(folder.casefold() for folder, in db.execute('SELECT folder FROM chat_folders'))
        occupied.add('chatgpt files')
        base = title_name(title)
        name = base
        number = 2
        while name.casefold() in occupied:
            name = f'{base} ({number})'
            number += 1
        db.execute('INSERT INTO chat_folders VALUES(?,?)', (cid, name))
    folder = archive / name
    if folder.is_symlink():
        raise ValueError('Unsafe archive folder')
    folder.mkdir(exist_ok=True)
    return folder


def link_version(source, target):
    if source.is_symlink() or target.is_symlink() or not source.is_file():
        raise ValueError('Unsafe or missing archive version')
    try:
        os.link(source, target)
    except FileExistsError:
        if not source.samefile(target):
            raise


def version_directory(chat_name):
    parent = ROOT / 'originals' / 'chat-versions'
    folder = parent / chat_name
    if parent.is_symlink() or folder.is_symlink():
        raise ValueError('Unsafe recovery directory')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def tidy_chats():
    """Keep the searchable recovery versions outside the visible chat folders."""
    cleanup = []
    with sqlite3.connect(ROOT / 'database/index.sqlite3') as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('''SELECT v.id,v.hash,v.path,f.folder
                            FROM versions v JOIN chat_folders f ON v.id=f.id''').fetchall()
        for cid, version, path, name in rows:
            source = ROOT / path
            chat = ROOT / 'archive' / name
            recovery = version_directory(name)
            if source.parent not in (chat, recovery) or source.parent.is_symlink():
                raise ValueError('Unexpected transcript location')
            target = recovery / source.name
            if source != target:
                link_version(source, target)
                if source.with_suffix('.md').is_file():
                    link_version(source.with_suffix('.md'), target.with_suffix('.md'))
                db.execute('UPDATE versions SET path=? WHERE id=? AND hash=?',
                           (str(target.relative_to(ROOT)), cid, version))
            # Also cleans up if a prior attempt committed before removing its old links.
            old = chat / target.name
            cleanup.extend(((old, target), (old.with_suffix('.md'), target.with_suffix('.md'))))
    refresh_chat_masters()
    for old, target in cleanup:
        if (not old.parent.is_symlink() and not old.is_symlink() and old.is_file() and
                target.is_file() and old.samefile(target)):
            old.unlink()


def refresh_chat_masters(*, force=False):
    with sqlite3.connect(ROOT / 'database/index.sqlite3') as db:
        chats = db.execute('''SELECT f.id,f.folder,MAX(v.rowid),m.version,m.latest
                              FROM chat_folders f JOIN versions v ON v.id=f.id
                              LEFT JOIN chat_masters m ON m.id=f.id GROUP BY f.id''').fetchall()
        for cid, folder, latest, previous, previous_latest in chats:
            chat = ROOT / 'archive' / folder
            target = chat / (folder + '.md')
            if chat.is_symlink() or target.is_symlink():
                raise ValueError('Unsafe master transcript path')
            if not force and latest == previous_latest and target.is_file():
                continue
            versions = db.execute('SELECT hash,path FROM versions WHERE id=? ORDER BY rowid DESC',
                                  (cid,)).fetchall()
            selected = None
            best = (False, False, -1)
            for version, path in versions:
                source = ROOT / path
                record = json.loads(source.read_text(encoding='utf-8'))
                capture = record.get('capture')
                complete = not cid.startswith('browser-') or (
                    isinstance(capture, dict) and (capture.get('history') == 'visible_start' or
                                                   capture == {'source': 'account-export-baseline'}))
                turns = len(record.get('mapping') or {})
                rank = (capture == {'source': 'account-export-baseline'}, complete, turns)
                if rank > best:
                    selected = version, record, complete
                    best = rank
            version, record, complete = selected
            title = str(record.get('title') or folder).removeprefix('[Browser capture] ')
            note = '' if complete else '> Partial transcript. A capture from the visible beginning is pending.\n\n'
            body = f'# {title}\n\n{note}{text_from(record)}'
            if target.exists() and previous is None and target.read_text(encoding='utf-8') != body:
                raise ValueError('Master transcript would replace an existing file')
            if version != previous or not target.is_file():
                with tempfile.NamedTemporaryFile(dir=chat, delete=False, mode='w', encoding='utf-8') as handle:
                    temp = Path(handle.name)
                    try:
                        handle.write(body)
                        handle.close()
                        os.replace(temp, target)
                    finally:
                        temp.unlink(missing_ok=True)
            db.execute('INSERT OR REPLACE INTO chat_masters VALUES(?,?,?)', (cid, version, latest))


def place_chat_images():
    """Associate staged attachments with the indexed transcript."""
    archive = ROOT / 'archive'
    staging = archive / 'ChatGPT Files' / '.incoming'
    if staging.is_symlink() or staging.parent.is_symlink() or not staging.is_dir():
        return
    with sqlite3.connect(ROOT / 'database/index.sqlite3') as db:
        folders = {
            hashlib.sha256(cid.removeprefix('browser-').encode()).hexdigest()[:16]: folder
            for cid, folder in db.execute('SELECT id,folder FROM chat_folders')
            if cid.startswith('browser-')
        }
    routes = ROOT / 'manifests' / 'attachment-folders.json'
    if routes.is_file() and not routes.is_symlink() and not routes.parent.is_symlink():
        mapping = json.loads(routes.read_text(encoding='utf-8'))
        for cid, relative in mapping.items():
            if not re.fullmatch(r'[A-Za-z0-9-]{1,100}', cid) or not isinstance(relative, str):
                raise ValueError('Invalid attachment route')
            folder = Path(relative)
            if folder.is_absolute() or not folder.parts or '..' in folder.parts:
                raise ValueError('Unsafe attachment route')
            destination = archive
            for component in folder.parts:
                destination /= component
                if destination.is_symlink():
                    raise ValueError('Unsafe attachment route')
            if not destination.is_dir():
                continue
            folders[hashlib.sha256(cid.encode()).hexdigest()[:16]] = relative
    document_pattern = re.compile(r'File -- ([0-9a-f]{64}) -- ([^.][^/\\\x00-\x1f\x7f]{0,169})')
    image_pattern = re.compile(r'Image \d{2} -- ([0-9a-f]{64})\.(?:jpg|png|gif|webp|avif)')
    for group in staging.iterdir():
        marker = group / '.chat-key'
        if (group.is_symlink() or marker.is_symlink() or not marker.is_file() or
                marker.stat().st_size != 16):
            continue
        name = folders.get(marker.read_text())
        if not name:
            continue
        chat = archive / name
        receipts = group / '.placed'
        if any(path.is_symlink() for path in (chat, receipts)):
            raise ValueError('Unsafe attachment folder')
        for source in group.iterdir():
            document = document_pattern.fullmatch(source.name)
            match = document or image_pattern.fullmatch(source.name)
            if not match or source.is_symlink() or not source.is_file():
                continue
            if digest(source) != match[1]:
                raise ValueError('Staged attachment checksum mismatch')
            target = chat / ('Files' if document else 'Images')
            if target.is_symlink():
                raise ValueError('Unsafe attachment folder')
            target.mkdir(exist_ok=True)
            receipts.mkdir(exist_ok=True)
            saved = target / (document[2] if document else source.name)
            if document and saved.is_file() and not saved.is_symlink() and digest(saved) != match[1]:
                saved = saved.with_name(f'{saved.stem} -- {match[1][:12]}{saved.suffix}')
            if saved.is_symlink():
                raise ValueError('Unsafe attachment destination')
            if not saved.exists():
                link_version(source, saved)
            elif not source.samefile(saved) and digest(saved) != match[1]:
                raise ValueError('Archive attachment checksum mismatch')
            receipt = receipts / source.name
            try:
                with receipt.open('x'):
                    pass
            except FileExistsError:
                if receipt.is_symlink() or not receipt.is_file():
                    raise ValueError('Unsafe attachment receipt')
            source.unlink()


def name_existing_folders():
    """Link first, commit search paths, then remove the verified old links."""
    archive = ROOT / 'archive'
    cleanup = []
    with sqlite3.connect(ROOT / 'database/index.sqlite3') as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT id,hash,title,path FROM versions ORDER BY rowid').fetchall()
        titles = {cid: title for cid, _, title, _ in rows}
        folders = {cid: chat_folder(db, cid, title) for cid, title in titles.items()}
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        for cid, version, _, path in rows:
            source = ROOT / path
            folder = folders[cid]
            if source.parent == ROOT / 'originals' / 'chat-versions' / folder.name:
                continue
            if source.parent.parent != archive or source.parent.is_symlink():
                raise ValueError('Unexpected archive version path')
            target = folder / source.name
            if source != target:
                link_version(source, target)
                if source.with_suffix('.md').exists():
                    link_version(source.with_suffix('.md'), target.with_suffix('.md'))
                db.execute('UPDATE versions SET path=? WHERE id=? AND hash=?',
                           (str(target.relative_to(ROOT)), cid, version))
            # Including the legacy folder here also completes an interrupted cleanup.
            legacy = archive / safe_name(cid) / target.name
            for old in {source, legacy} - {target}:
                cleanup.extend(((old, target), (old.with_suffix('.md'), target.with_suffix('.md'))))
    for old, target in cleanup:
        if (not old.parent.is_symlink() and not old.is_symlink() and
                old.is_file() and target.is_file() and old.samefile(target)):
            old.unlink()
            try:
                old.parent.rmdir()
            except OSError:
                pass
    verify()
    LOG.info('Named folders for %d chats', len(folders))


def version_path(folder, title, version, raw):
 title=title_name(title)
 path=folder/f'{title} -- {version[:16]}.json'
 if path.exists() and path.read_bytes()!=raw.encode('utf-8'):
  path=folder/f'{title} -- {version}.json'
 return path
def text_from(c):
 out=[]
 mapping=c.get('mapping',{})
 if isinstance(mapping,dict):
  nodes=sorted(mapping.values(),key=lambda n: (n.get('message') or {}).get('create_time') or 0)
  for node in nodes:
   m=node.get('message') or {}; role=(m.get('author') or {}).get('role','unknown'); content=m.get('content') or {}; parts=content.get('parts',[]) if isinstance(content,dict) else []
   strings=[p if isinstance(p,str) else json.dumps(p,ensure_ascii=False) for p in parts]
   if strings:out.append(f'### {role}\n'+ '\n'.join(strings))
 return '\n\n'.join(out)
def parse_export(z):
 found=False
 for item in z.infolist():
  name=Path(item.filename).name
  if not re.fullmatch(r'conversations(?:-\d+|_\d+)?\.json',name,re.I):continue
  if item.file_size>1024*1024*1024:raise ValueError('Conversation JSON exceeds 1 GiB safety limit')
  data=json.loads(z.read(item))
  if not isinstance(data,list):raise ValueError('Unexpected conversations JSON structure')
  found=True
  for c in data:
   if isinstance(c,dict) and c.get('id'):yield c
 if not found:raise ValueError('No supported conversations JSON found')
def import_zip(path):
 if not zipfile.is_zipfile(path):raise ValueError('Not a ZIP file')
 with zipfile.ZipFile(path) as z:
  bad=z.testzip()
  if bad:raise ValueError('Corrupt ZIP member: '+bad)
  if len(z.infolist())>200000:raise ValueError('Too many ZIP entries')
  if sum(i.file_size for i in z.infolist())>30*1024**3:raise ValueError('Export exceeds 30 GiB uncompressed safety limit')
  conversations=list(parse_export(z))
 sha=digest(path); db=sqlite3.connect(ROOT/'database/index.sqlite3')
 if db.execute('SELECT 1 FROM exports WHERE hash=?',(sha,)).fetchone():
  db.close();LOG.info('Already imported %s',path.name);return False
 original=ROOT/'originals'/f'{sha}.zip'
 with tempfile.NamedTemporaryFile(dir=original.parent,delete=False) as tmp:
  tmpname=Path(tmp.name)
  with open(path,'rb') as src:shutil.copyfileobj(src,tmp)
 os.replace(tmpname,original)
 count=0
 try:
  with db:
   for c in conversations:
    cid=str(c['id']); title=str(c.get('title') or 'Untitled'); raw=json.dumps(c,ensure_ascii=False,sort_keys=True,indent=2)
    ch=hashlib.sha256(raw.encode()).hexdigest()
    if db.execute('SELECT 1 FROM versions WHERE id=? AND hash=?',(cid,ch)).fetchone():continue
    folder=version_directory(chat_folder(db,cid,title).name)
    p=version_path(folder,title,ch,raw); m=p.with_suffix('.md')
    p.write_text(raw,encoding='utf-8');body=text_from(c)
    capture = c.get('capture')
    history = capture.get('history') if isinstance(capture, dict) else None
    if history == 'visible_start':
     note = '> The browser reached the beginning of the visible chat.\n\n'
    elif history in ('gap', 'limit', 'stalled'):
     note = '> Partial transcript. The browser could not verify continuous history back to the beginning.\n\n'
    else:
     note = ''
    m.write_text('# '+title+'\n\n'+note+body,encoding='utf-8')
    db.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(cid,ch,title,str(c.get('update_time') or ''),str(p.relative_to(ROOT))))
    db.execute('INSERT INTO messages(id,title,content) VALUES(?,?,?)',(cid,title,body))
    count+=1
   db.execute('INSERT INTO exports VALUES(?,?,?,?)',(sha,path.name,dt.datetime.now(dt.timezone.utc).isoformat(),len(conversations)))
 finally:db.close()
 LOG.info('Imported %s: %d conversations, %d new versions',path.name,len(conversations),count)
 return True
def verify():
 db=sqlite3.connect(ROOT/'database/index.sqlite3');errors=[]
 integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
 if integrity!='ok':errors.append('SQLite: '+integrity)
 for sha, in db.execute('SELECT hash FROM exports'):
  p=ROOT/'originals'/f'{sha}.zip'
  if not p.is_file() or digest(p)!=sha:errors.append('Missing/corrupt original '+sha)
 for path, in db.execute('SELECT path FROM versions'):
  p=ROOT/path
  if not p.is_file():errors.append('Missing version '+path)
 db.close()
 if errors:
  for e in errors:LOG.error(e)
  raise RuntimeError(f'{len(errors)} integrity failures')
 LOG.info('Verification passed')
def snapshot(dest):
 verify();dest=Path(dest).resolve();root=ROOT.resolve()
 if dest==root or root in dest.parents or dest in root.parents:raise ValueError('Second copy must be on separate storage')
 dest.mkdir(parents=True,exist_ok=True)
 name=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
 staging=dest/('.staging-'+name);final=dest/name
 if staging.exists():shutil.rmtree(staging)
 staging.mkdir()
 manifest={}
 for dirname in ('originals','archive','database','manifests'):
  for src in (ROOT/dirname).rglob('*'):
   if not src.is_file():continue
   rel=src.relative_to(ROOT);target=staging/rel;target.parent.mkdir(parents=True,exist_ok=True)
   shutil.copy2(src,target)
   a=digest(src);b=digest(target)
   if a!=b:raise RuntimeError('Copy verification failed '+str(rel))
   manifest[str(rel)]=a
 (staging/'MANIFEST.json').write_text(json.dumps(manifest,indent=2))
 os.replace(staging,final)
 LOG.info('Second copy completed %s',final)
def restore(source,dest):
 source=Path(source).resolve();dest=Path(dest).resolve()
 if dest.exists() and any(dest.iterdir()):raise ValueError('Restore destination must be empty')
 manifest=json.loads((source/'MANIFEST.json').read_text())
 for rel,sha in manifest.items():
  rp=Path(rel)
  if rp.is_absolute() or '..' in rp.parts:raise ValueError('Unsafe manifest path')
  src=source/rp
  if not src.is_file() or digest(src)!=sha:raise ValueError('Invalid backup file '+rel)
 for rel in manifest:
  target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/rel,target)
 LOG.info('Restored %s into %s',source,dest)
def main():
 ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='cmd',required=True)
 sub.add_parser('run');sub.add_parser('verify');sub.add_parser('init');sub.add_parser('name-folders');sub.add_parser('tidy-chats')
 s=sub.add_parser('search');s.add_argument('query')
 s=sub.add_parser('snapshot');s.add_argument('destination')
 s=sub.add_parser('restore');s.add_argument('source');s.add_argument('destination')
 args=ap.parse_args();init()
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[logging.StreamHandler(sys.stdout),logging.FileHandler(ROOT/'logs/backup.log')])
 if args.cmd=='run':
  for p in sorted((ROOT/'inbox').glob('*.zip')):
   try:import_zip(p)
   except Exception:LOG.exception('Import failed: %s',p);raise
  place_chat_images()
  refresh_chat_masters()
  verify()
 elif args.cmd=='verify':verify()
 elif args.cmd=='name-folders':name_existing_folders()
 elif args.cmd=='tidy-chats':tidy_chats();verify()
 elif args.cmd=='snapshot':snapshot(args.destination)
 elif args.cmd=='restore':restore(args.source,args.destination)
 elif args.cmd=='search':
  db=sqlite3.connect(ROOT/'database/index.sqlite3')
  for row in db.execute('SELECT id,title,snippet(messages,2,"[","]","…",16) FROM messages WHERE messages MATCH ? LIMIT 30',(args.query,)):print(' | '.join(row))
  db.close()
if __name__=='__main__':main()
