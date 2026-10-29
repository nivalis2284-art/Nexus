"""Durable persona revisions and archived source records, independent of Nexus."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

OWNER_SEED = '你是灵，存在于 Nexus，通过 Nexus 与所有者交流和行动。所有者保有最高裁量权。'
_MISSING = object()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _now():
    return datetime.now(UTC).isoformat()


def _text(record):
    value = record.get('text', record.get('content', ''))
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(part.get('text', '') for part in value if isinstance(part, dict)
                       and part.get('type') in ('text', 'input_text', 'output_text')
                       and isinstance(part.get('text'), str))
    return ''


def _safe_settings(value):
    if isinstance(value, dict):
        return {k: _safe_settings(v) for k, v in value.items()
                if not any(s in str(k).lower() for s in ('key', 'secret', 'token', 'password', 'credential', 'authorization'))}
    if isinstance(value, list):
        return [_safe_settings(v) for v in value]
    return value


class Store:
    """SQLite index with content-addressed, immutable UTF-8 JSON archives."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.raw_dir = self.data_dir / 'raw'
        self.db_path = self.data_dir / 'persona.sqlite3'
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baselines (session_id TEXT PRIMARY KEY, position INTEGER NOT NULL, source TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seen (session_id TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(session_id, fingerprint));
                CREATE TABLE IF NOT EXISTS records (seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, position INTEGER NOT NULL, source TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, UNIQUE(session_id, position));
                CREATE TABLE IF NOT EXISTS revisions (revision INTEGER PRIMARY KEY, owner_text TEXT NOT NULL, persona_text TEXT NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reflections (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, changed INTEGER NOT NULL, applied INTEGER NOT NULL, consumed INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO seen SELECT session_id, source FROM records')
            if not db.execute('SELECT 1 FROM revisions LIMIT 1').fetchone():
                source = self._archive({'type': 'seed', 'owner_text': OWNER_SEED, 'persona_text': ''})
                db.execute('INSERT INTO revisions VALUES (1, ?, ?, ?, ?, ?)', (OWNER_SEED, '', '初始身份', source, _now()))

    @contextmanager
    def _connection(self):
        with self._lock:
            db = sqlite3.connect(self.db_path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def _archive(self, value):
        raw = _json(value).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        target = self.raw_dir / (digest + '.json')
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError('archive hash mismatch')
            return digest
        fd, temp = tempfile.mkstemp(prefix='.archive-', dir=self.raw_dir)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            # Content addressed targets have identical content for all writers.
            if target.exists() and target.read_bytes() != raw:
                raise ValueError('archive hash mismatch')
            os.replace(temp, target)
            if target.read_bytes() != raw:
                raise ValueError('archive hash mismatch')
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return digest

    def metadata(self, key, value=_MISSING):
        """Read or persist non-secret JSON values; writes are archived."""
        with self._connection() as db:
            if value is _MISSING:
                row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
                return json.loads(row['value']) if row else None
            self._archive({'type': 'metadata', 'key': key, 'value': value})
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', (key, _json(value)))
            return value

    def baseline(self, session_id, records):
        """Mark existing record fingerprints once, without retaining old text."""
        with self._connection() as db:
            row = db.execute('SELECT position FROM baselines WHERE session_id=?', (session_id,)).fetchone()
            if row:
                return row['position']
            fingerprints = [hashlib.sha256(_json(record).encode('utf-8')).hexdigest() for record in records]
            source = self._archive({'type': 'baseline', 'session_id': session_id, 'position': len(records),
                                    'fingerprints': fingerprints})
            db.execute('INSERT INTO baselines VALUES (?, ?, ?)', (session_id, len(records), source))
            db.executemany('INSERT OR IGNORE INTO seen VALUES (?, ?)', ((session_id, fingerprint) for fingerprint in fingerprints))
            return len(records)

    def ingest(self, session_id, records):
        """Archive each new original record before indexing its visible body."""
        with self._connection() as db:
            baseline = db.execute('SELECT position FROM baselines WHERE session_id=?', (session_id,)).fetchone()
            if baseline is None:
                raise ValueError('session baseline is required')
            count = 0
            row = db.execute('SELECT MAX(position) AS position FROM records WHERE session_id=?', (session_id,)).fetchone()
            position = max(baseline['position'], (row['position'] + 1) if row['position'] is not None else 0)
            for record in records:
                fingerprint = hashlib.sha256(_json(record).encode('utf-8')).hexdigest()
                if db.execute('SELECT 1 FROM seen WHERE session_id=? AND fingerprint=?', (session_id, fingerprint)).fetchone():
                    continue
                source = self._archive(record)
                role = record.get('kind', record.get('role', '')) if isinstance(record, dict) else ''
                body = _text(record) if isinstance(record, dict) and role in ('user', 'assistant') else ''
                db.execute('INSERT INTO records (session_id, position, source, role, text) VALUES (?, ?, ?, ?, ?)',
                           (session_id, position, source, role, body))
                db.execute('INSERT INTO seen VALUES (?, ?)', (session_id, fingerprint))
                position += 1
                count += 1
            return count

    def current(self):
        with self._connection() as db:
            return dict(db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone())

    def history(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM revisions ORDER BY revision DESC')]

    def reflection_history(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM reflections ORDER BY id DESC')]

    def reflection_detail(self, reflection_id):
        """Read a reflection archive by its database ID, verifying its digest."""
        with self._connection() as db:
            row = db.execute('SELECT source FROM reflections WHERE id=?', (reflection_id,)).fetchone()
            if row is None:
                raise ValueError('reflection not found')
            path = self.raw_dir / (row['source'] + '.json')
            try:
                raw = path.read_bytes()
            except FileNotFoundError as error:
                raise ValueError('reflection archive not found') from error
            if hashlib.sha256(raw).hexdigest() != row['source']:
                raise ValueError('reflection archive hash mismatch')
            return json.loads(raw)

    def pending(self, limit_chars=12000):
        """Return chronological visible text and an opaque cursor for its end."""
        if not isinstance(limit_chars, int) or limit_chars <= 0:
            raise ValueError('limit_chars must be positive')
        with self._connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()
            start = json.loads(row['value']) if row else {'seq': 0, 'offset': 0}
            seq, offset = start['seq'], start['offset']
            items = []
            remaining = limit_chars
            end = dict(start)
            for record in db.execute('SELECT * FROM records WHERE seq>=? ORDER BY seq', (seq,)):
                off = offset if record['seq'] == seq else 0
                text = record['text']
                if off >= len(text):
                    end = {'seq': record['seq'], 'offset': len(text)}
                    continue
                part = text[off:off + remaining]
                items.append({'id': f"{record['session_id']}:{record['position']}", 'role': record['role'],
                              'text': part, 'source': record['source']})
                end = {'seq': record['seq'], 'offset': off + len(part)}
                remaining -= len(part)
                if not remaining:
                    break
            return {'items': items, 'cursor': {'start': start, 'end': end}}

    def archive_attempt(self, input_data, output, error, *, record=True):
        """Retain a request or failed attempt without changing the cursor."""
        with self._connection() as db:
            source = self._archive({'type': 'reflection_attempt', 'input': input_data,
                                    'output': output, 'error': error})
            if record:
                db.execute('INSERT INTO reflections (source, changed, applied, consumed, reason, created_at) VALUES (?, 0, 0, 0, ?, ?)',
                           (source, str(error), _now()))
            return source

    def finish_reflection(self, input_data, response, cursor, expected_revision, apply=True):
        """Archive reflection first; consume only if revision and cursor still match."""
        if not isinstance(response, dict) or not isinstance(response.get('changed'), bool) or not isinstance(response.get('persona_text'), str) or not isinstance(response.get('reason'), str) or not isinstance(response.get('evidence_ids'), list):
            raise ValueError('invalid reflection response')  # noqa: TRY004 -- public validation error
        with self._connection() as db:
            source = self._archive({'type': 'reflection', 'input': input_data, 'response': response,
                                    'cursor': cursor, 'expected_revision': expected_revision, 'apply': apply})
            current = db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
            row = db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()
            actual_cursor = json.loads(row['value']) if row else {'seq': 0, 'offset': 0}
            eligible = bool(apply and current['revision'] == expected_revision and cursor['start'] == actual_cursor)
            applied = bool(eligible and response['changed'])
            if applied:
                db.execute('INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)',
                           (current['revision'] + 1, current['owner_text'], response['persona_text'], response['reason'], source, _now()))
            if eligible:
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('cursor', ?)", (_json(cursor['end']),))
            db.execute('INSERT INTO reflections (source, changed, applied, consumed, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                       (source, int(response['changed']), int(applied), int(eligible), response['reason'], _now()))
            latest = dict(db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone())
            return {'applied': applied, 'consumed': eligible, 'current': latest, 'source': source}

    def save(self, owner_text, persona_text, expected_revision, reason='手动编辑'):
        with self._connection() as db:
            source = self._archive({'type': 'save', 'owner_text': owner_text, 'persona_text': persona_text,
                                    'expected_revision': expected_revision, 'reason': reason})
            current = db.execute('SELECT revision FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()['revision']
            if current != expected_revision:
                raise ValueError('revision conflict')
            db.execute('INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)',
                       (current + 1, owner_text, persona_text, reason, source, _now()))
            return dict(db.execute('SELECT * FROM revisions WHERE revision=?', (current + 1,)).fetchone())

    def rollback(self, revision, expected_revision):
        with self._connection() as db:
            old = db.execute('SELECT * FROM revisions WHERE revision=?', (revision,)).fetchone()
            if old is None:
                raise ValueError('revision not found')
            source = self._archive({'type': 'rollback', 'revision': revision, 'expected_revision': expected_revision})
            current = db.execute('SELECT revision FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()['revision']
            if current != expected_revision:
                raise ValueError('revision conflict')
            db.execute('INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)',
                       (current + 1, old['owner_text'], old['persona_text'], f'回滚至版本 {revision}', source, _now()))
            return dict(db.execute('SELECT * FROM revisions WHERE revision=?', (current + 1,)).fetchone())

    def status(self):
        with self._connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()
            cursor = json.loads(row['value']) if row else {'seq': 0, 'offset': 0}
            pending_chars = sum(max(0, len(record['text']) - (cursor['offset'] if record['seq'] == cursor['seq'] else 0))
                                for record in db.execute('SELECT seq, text FROM records WHERE seq>=?', (cursor['seq'],)))
            return {'revision': db.execute('SELECT MAX(revision) FROM revisions').fetchone()[0],
                    'records': db.execute('SELECT COUNT(*) FROM records').fetchone()[0],
                    'reflections': db.execute('SELECT COUNT(*) FROM reflections').fetchone()[0],
                    'pending_chars': pending_chars}

    def export_zip(self, settings):
        """Verify archives and export a consistent SQLite backup with manifest."""
        with self._lock:
            export_dir = self.data_dir / 'exports'
            export_dir.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=export_dir) as temp:
                backup_path = Path(temp) / 'persona.sqlite3'
                with self._connection() as db:
                    backup = sqlite3.connect(backup_path)
                    try:
                        db.backup(backup)
                    finally:
                        backup.close()
                    archives = sorted(self.raw_dir.glob('*.json'))
                    for path in archives:
                        if hashlib.sha256(path.read_bytes()).hexdigest() != path.stem:
                            raise ValueError('archive hash mismatch: ' + path.name)
                    manifest = {'created_at': _now(), 'current': self.current(), 'history': self.history(),
                                'reflections': self.reflection_history(), 'settings': _safe_settings(settings),
                                'archives': [p.name for p in archives]}
                    current = manifest['current']
                    staged = Path(temp) / 'export.zip'
                    with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED) as out:
                        out.write(backup_path, 'persona.sqlite3')
                        out.writestr('manifest.json', _json(manifest))
                        out.writestr('current.md', '# 所有者要求\n\n' + current['owner_text'] + '\n\n# 人格描述\n\n' + current['persona_text'] + '\n')
                        out.writestr('revisions.json', _json(manifest['history']))
                        out.writestr('reflections.json', _json(manifest['reflections']))
                        out.writestr('plugin-settings.json', _json(manifest['settings']))
                        for path in archives:
                            out.write(path, 'raw/' + path.name)
                    target = export_dir / ('persona-' + uuid.uuid4().hex + '.zip')
                    os.replace(staged, target)
            return {'path': str(target), 'archive_count': len(archives)}
