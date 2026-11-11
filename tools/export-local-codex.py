#!/usr/bin/env python3
"""Copy user and assistant text from local Codex session logs into an archive."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile


def transcript(path):
    session_id = None
    messages = []
    with path.open(encoding='utf-8') as source:
        for line in source:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get('payload') or {}
            if record.get('type') == 'session_meta':
                session_id = payload.get('id')
            if (record.get('type') != 'response_item' or payload.get('type') != 'message'
                    or payload.get('role') not in ('user', 'assistant')):
                continue
            parts = [item['text'] for item in payload.get('content', [])
                     if item.get('type') in ('input_text', 'output_text')
                     and isinstance(item.get('text'), str)]
            if parts:
                messages.append((payload['role'].title(), '\n\n'.join(parts)))
    if isinstance(session_id, str) and re.fullmatch(r'[a-f0-9-]{36}', session_id) and messages:
        text = '# Codex session\n\n' + '\n\n'.join(f'## {role}\n\n{body}' for role, body in messages) + '\n'
        return session_id, text
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source', type=Path, default=Path.home() / '.codex')
    options = parser.parse_args()
    os.umask(0o077)
    options.destination.mkdir(parents=True, exist_ok=True)
    counts = dict(written=0, unchanged=0, failed=0)
    for directory in ('archived_sessions', 'sessions'):
        for path in sorted((options.source / directory).rglob('rollout-*.jsonl')):
            if path.is_symlink():
                continue
            try:
                result = transcript(path)
                if result is None:
                    continue
                session_id, text = result
                target = options.destination / (session_id + '.md')
                if target.is_symlink():
                    raise ValueError('unsafe archive destination')
                if target.is_file() and target.read_text(encoding='utf-8') == text:
                    counts['unchanged'] += 1
                    continue
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                                 dir=options.destination, delete=False) as output:
                    temporary = Path(output.name)
                    try:
                        output.write(text)
                        output.close()
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
                counts['written'] += 1
            except (OSError, ValueError):
                counts['failed'] += 1
    print(', '.join(f'{key}={value}' for key, value in counts.items()))
    return bool(counts['failed'])


if __name__ == '__main__':
    raise SystemExit(main())
