"""Preserve the image bytes made available inside a chat."""
import base64
import hashlib
import os
from pathlib import Path
import re
import tempfile
import stat


MAX_IMAGE_BYTES = 8 * 1024**2
ROOT = Path('/chat-files')


def image_suffix(data):
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'webp'
    if data[4:8] == b'ftyp' and data[8:12] in (b'avif', b'avis'):
        return 'avif'
    raise ValueError('unsupported_image')


def image_folder(name, chat_key):
    parent = ROOT / '.incoming'
    if parent.is_symlink():
        raise ValueError('invalid_image_destination')
    parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    occupied = {p.name.casefold() for p in parent.iterdir()}
    for path in parent.iterdir():
        marker = path / '.chat-key'
        if (not path.is_symlink() and not marker.is_symlink() and marker.is_file() and
                marker.read_text() == chat_key):
            return path
    title = name
    number = 2
    while name.casefold() in occupied:
        name = f'{title} ({number})'
        number += 1
    folder = parent / name
    legacy = parent / f'{title} -- {chat_key}'
    if legacy.exists():
        if legacy.is_symlink() or not legacy.is_dir():
            raise ValueError('invalid_image_destination')
        legacy.rename(folder)
    else:
        folder.mkdir(mode=0o750)
    (folder / '.chat-key').write_text(chat_key)
    return folder


def name_existing_image_folders():
    parent = ROOT / '.incoming'
    if parent.is_symlink() or not parent.is_dir():
        return
    for folder in list(parent.iterdir()):
        match = re.fullmatch(r'(.+) -- ([0-9a-f]{16})', folder.name)
        if match and not folder.is_symlink() and folder.is_dir() and not (folder / '.chat-key').exists():
            image_folder(*match.groups())


def save_image(message):
    cid, title, index, encoded = (message.get(key) for key in ('id', 'title', 'index', 'data'))
    if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', cid):
        raise ValueError('invalid_chat_id')
    if not isinstance(title, str) or len(title) > 1024 or type(index) is not int or not 1 <= index <= 50:
        raise ValueError('invalid_image_identity')
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ValueError('oversized_image')
    data = base64.b64decode(encoded, validate=True)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('oversized_image')
    suffix = image_suffix(data)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', title)
    name = name.encode('utf-8')[:120].decode('utf-8', 'ignore').strip(' .') or 'Untitled'
    chat_key = hashlib.sha256(cid.encode()).hexdigest()[:16]
    digest = hashlib.sha256(data).hexdigest()
    folder = image_folder(name, chat_key)
    filename = folder / f'Image {index:02d} -- {digest}.{suffix}'
    receipt = folder / '.placed' / filename.name
    if receipt.is_file() and not receipt.is_symlink():
        return {'ok': True, 'saved': False, 'bytes': len(data)}
    if filename.is_symlink():
        raise ValueError('invalid_image_destination')
    if filename.exists():
        if filename.read_bytes() != data:
            raise ValueError('image_collision')
        return {'ok': True, 'saved': False, 'bytes': len(data)}
    with tempfile.NamedTemporaryFile(dir=folder, prefix='.image-', delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            os.link(temp, filename)
        except FileExistsError:
            if filename.read_bytes() != data:
                raise ValueError('image_collision')
            return {'ok': True, 'saved': False, 'bytes': len(data)}
        finally:
            temp.unlink()
    return {'ok': True, 'saved': True, 'bytes': len(data)}


def stage_document(message):
    cid, title, source_name, filename, size = (message.get(key) for key in
                                         ('id', 'title', 'sourceName', 'filename', 'bytes'))
    if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', cid):
        raise ValueError('invalid_chat_id')
    if not isinstance(title, str) or len(title) > 1024:
        raise ValueError('invalid_chat_title')
    if (not isinstance(filename, str) or not re.fullmatch(r'[^/\\\x00-\x1f\x7f]{1,191}', filename)
            or not filename.strip() or filename in ('.', '..')
            or type(size) is not int or not 0 <= size <= 256 * 1024**2):
        raise ValueError('invalid_document')
    dot = filename.rfind('.')
    stem, suffix = (filename[:dot], filename[dot:]) if dot > 0 else (filename, '')
    def expected_name(base, extension):
        return re.escape(base) + r'(?: \(\d{1,6}\))?' + re.escape(extension)

    originals = (expected_name(stem, suffix),
                 expected_name(re.sub(r'[<>:"|?*]', '_', stem), re.sub(r'[<>:"|?*]', '_', suffix)))
    if not isinstance(source_name, str) or not any(re.fullmatch(pattern, source_name) for pattern in originals):
        raise ValueError('invalid_download_name')
    clean = re.sub(r'[<>:"|?*]', '_', filename)
    clean = clean.encode('utf-8')[:170].decode('utf-8', 'ignore').strip(' .') or 'Attachment'
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)
    name = name.encode('utf-8')[:120].decode('utf-8', 'ignore').strip(' .') or 'Untitled'
    folder = image_folder(name, hashlib.sha256(cid.encode()).hexdigest()[:16])
    source = ROOT / source_name
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError('invalid_download_size')
        os.fchmod(handle.fileno(), 0o600)
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    target = folder / f'File -- {digest} -- {clean}'
    receipts = folder / '.placed'
    receipt = receipts / target.name
    if receipts.is_symlink() or receipt.is_symlink() or target.is_symlink():
        raise ValueError('invalid_document_destination')
    if receipt.is_file():
        source.unlink()
        return {'ok': True, 'saved': False}
    try:
        os.link(source, target, follow_symlinks=False)
        saved = True
    except FileExistsError:
        if target.is_symlink() or not target.is_file():
            raise ValueError('invalid_document_destination')
        with target.open('rb') as existing:
            if hashlib.file_digest(existing, 'sha256').hexdigest() != digest:
                raise ValueError('document_collision')
        saved = False
    source.unlink()
    return {'ok': True, 'saved': saved}
