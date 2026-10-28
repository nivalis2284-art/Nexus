"""Small, model-independent memory store.

Raw input is content-addressed and retained separately from the searchable
index. Deactivating a note does not remove its source archive.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def _now():
    return datetime.now(UTC).isoformat(timespec='microseconds')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _visible_text(message):
    content = message.get('content')
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return ''.join(block.get('text', '') for block in content
                   if isinstance(block, dict) and block.get('type') in ('text', 'input_text', 'output_text')
                   and isinstance(block.get('text'), str))


def _chunks(value, size=1000, overlap=100):
    start = 0
    while start < len(value):
        end = min(start + size, len(value))
        yield value[start:end]
        if end == len(value):
            break
        start = end - overlap


_CJK = re.compile(r'[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]+')
_WORD = re.compile(r'[^\W_]+', re.UNICODE)


def _tokens(value):
    """Words and CJK character n-grams, usable without a tokenizer model."""
    value = value.casefold()
    result = []
    # Remove CJK runs before word tokenization so unsegmented text does not
    # become a single enormous term. Single characters support short queries.
    for match in _CJK.finditer(value):
        part = match.group()
        result.extend(part)
        result.extend(part[i:i + 2] for i in range(len(part) - 1))
    result.extend(_WORD.findall(_CJK.sub(' ', value)))
    return Counter(result)


def _vector(value):
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError('vector must be a non-empty list of finite numbers')
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError('vector must contain finite numbers')  # noqa: TRY004 -- public validation errors.
        try:
            number = float(item)
        except (OverflowError, ValueError) as error:
            raise ValueError('vector must contain finite numbers') from error
        if not math.isfinite(number):
            raise ValueError('vector must contain finite numbers')
        result.append(number)
    return result


def _cosine(left, right):
    # Scaling first keeps even very large finite inputs numerically stable.
    lscale = max(map(abs, left))
    rscale = max(map(abs, right))
    if not lscale or not rscale:
        return 0.0
    left = [v / lscale for v in left]
    right = [v / rscale for v in right]
    return sum(a * b for a, b in zip(left, right)) / (math.hypot(*left) * math.hypot(*right))


class Store:
    """SQLite index and immutable JSON archives under ``data_dir``."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / 'raw'
        self.exports_dir = self.data_dir / 'exports'
        self.db_path = self.data_dir / 'memory.sqlite3'
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES sources(id),
                    kind TEXT NOT NULL, owner_id TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, role TEXT NOT NULL,
                    owner_id TEXT NOT NULL, message_hash TEXT NOT NULL,
                    chunk_no INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunk_sources (
                    chunk_id TEXT NOT NULL REFERENCES chunks(id),
                    source_id TEXT NOT NULL REFERENCES sources(id), message_no INTEGER NOT NULL,
                    PRIMARY KEY (chunk_id, source_id, message_no)
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, key TEXT UNIQUE, text TEXT NOT NULL,
                    source_id TEXT REFERENCES sources(id), tags TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS embedding_models (
                    model_id TEXT PRIMARY KEY, dimension INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vectors (
                    model_id TEXT NOT NULL REFERENCES embedding_models(model_id),
                    record_id TEXT NOT NULL, vector TEXT NOT NULL,
                    PRIMARY KEY (model_id, record_id)
                );
                CREATE INDEX IF NOT EXISTS chunk_sources_source ON chunk_sources(source_id);
                CREATE INDEX IF NOT EXISTS observations_source ON observations(source_id);
            ''')

    @contextmanager
    def _connection(self):
        # Every operation owns its connection and transaction. The lock also
        # coordinates archives and exports with the corresponding database state.
        with self._lock:
            db = sqlite3.connect(self.db_path, timeout=30)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                with db:
                    yield db
            finally:
                db.close()

    def archive(self, payload, kind='context', owner_id=''):
        """Save canonical UTF-8 JSON before recording its observation."""
        raw = _json(payload).encode('utf-8')
        source_id = _digest(raw)
        target = self.raw_dir / (source_id + '.json')
        kind, owner_id = str(kind), str(owner_id or '')
        with self._lock:
            if not target.exists():
                fd, temporary = tempfile.mkstemp(prefix='.archive-', dir=self.raw_dir)
                try:
                    with os.fdopen(fd, 'wb') as out:
                        out.write(raw)
                        out.flush()
                        os.fsync(out.fileno())
                    try:
                        # Linking a completed file never replaces an existing
                        # archive, including when two Store instances race.
                        os.link(temporary, target)
                    except FileExistsError:
                        pass
                finally:
                    os.unlink(temporary)
            if target.read_bytes() != raw:
                raise ValueError('archive content does not match its identifier')
            now = _now()
            with self._connection() as db:
                db.execute('INSERT OR IGNORE INTO sources VALUES (?, ?, ?, ?)',
                           (source_id, kind, owner_id, now))
                db.execute('INSERT INTO observations (source_id, kind, owner_id, created_at) VALUES (?, ?, ?, ?)',
                           (source_id, kind, owner_id, now))
        return source_id

    def ingest(self, scope):
        """Archive the full scope, then index only visible user/assistant text."""
        owner_id = str((scope.get('owner_id') or scope.get('owner') or '') if isinstance(scope, dict) else '')
        source_id = self.archive(scope, kind='context', owner_id=owner_id)
        messages = scope.get('messages', []) if isinstance(scope, dict) else []
        if not isinstance(messages, list):
            return source_id
        with self._connection() as db:
            for message_no, message in enumerate(messages):
                if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
                    continue
                text = _visible_text(message)
                if not text.strip():
                    continue
                message_hash = _digest(text.encode('utf-8'))
                role = message['role']
                for chunk_no, chunk in enumerate(_chunks(text)):
                    if not chunk.strip():
                        continue
                    identity = _json([owner_id, role, message_hash, chunk_no]).encode('utf-8')
                    chunk_id = 'chunk-' + _digest(identity)
                    db.execute('INSERT OR IGNORE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)',
                               (chunk_id, chunk, role, owner_id, message_hash, chunk_no, _now()))
                    db.execute('INSERT OR IGNORE INTO chunk_sources VALUES (?, ?, ?)',
                               (chunk_id, source_id, message_no))
        return source_id

    @staticmethod
    def _note(row):
        if row is None:
            return None
        note = dict(row)
        note['kind'] = 'note'
        note['role'] = ''
        note['content'] = note['text']
        note['key'] = note['key'] or ''
        note['source_id'] = note['source_id'] or ''
        note['tags'] = json.loads(note['tags'])
        note.setdefault('owner_id', '')
        return note

    def upsert(self, content, key=None, source_id='', tags=None, note_id=''):
        if not isinstance(content, str) or not content.strip():
            raise ValueError('content must be non-empty text')
        if key is not None and not isinstance(key, str):
            raise ValueError('key must be text')
        if not isinstance(note_id, str):
            raise ValueError('note_id must be text')  # noqa: TRY004 -- public validation errors.
        if tags is None:
            tags = []
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError('tags must be a list of text values')
        if not source_id:
            source_id = self.archive({'content': content, 'key': key, 'tags': tags, 'note_id': note_id}, kind='note')
        now = _now()
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM sources WHERE id=?', (source_id,)).fetchone():
                raise ValueError('source archive not found')
            if note_id:
                previous = db.execute('SELECT * FROM notes WHERE id=? AND deleted_at IS NULL', (note_id,)).fetchone()
                if previous is None:
                    raise ValueError('active note not found')
            else:
                previous = db.execute('SELECT * FROM notes WHERE key=?', (key,)).fetchone() if key else None
            if previous:
                if key is None:
                    key = previous['key']
                note_id = previous['id']
                if key and db.execute('SELECT 1 FROM notes WHERE key=? AND id<>?', (key, note_id)).fetchone():
                    raise ValueError('note key is already in use')
                db.execute('UPDATE notes SET key=?, text=?, source_id=?, tags=?, updated_at=?, deleted_at=NULL WHERE id=?',
                           (key or None, content, source_id, _json(tags), now, note_id))
                if previous['text'] != content or previous['deleted_at'] is not None:
                    db.execute('DELETE FROM vectors WHERE record_id=?', (note_id,))
            else:
                note_id = 'note-' + uuid.uuid4().hex
                db.execute('INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                           (note_id, key or None, content, source_id, _json(tags), now, now))
            return self._note(db.execute('''SELECT n.*, COALESCE(s.owner_id, '') AS owner_id
                FROM notes n LEFT JOIN sources s ON s.id=n.source_id WHERE n.id=?''', (note_id,)).fetchone())

    def forget(self, note_id):
        """Deactivate a note; original archives are retained for migration."""
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
            if not row or row['deleted_at'] is not None:
                return {'id': note_id, 'forgotten': False}
            now = _now()
            db.execute('UPDATE notes SET deleted_at=?, updated_at=? WHERE id=?', (now, now, note_id))
            db.execute('DELETE FROM vectors WHERE record_id=?', (note_id,))
            result = self._note(db.execute('''SELECT n.*, COALESCE(s.owner_id, '') AS owner_id
                FROM notes n LEFT JOIN sources s ON s.id=n.source_id WHERE n.id=?''', (note_id,)).fetchone())
            result['forgotten'] = True
            return result

    def list_notes(self, limit=100, offset=0):
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError('limit and offset must be non-negative integers')
        with self._connection() as db:
            return [self._note(row) for row in db.execute(
                '''SELECT n.*, COALESCE(s.owner_id, '') AS owner_id FROM notes n
                LEFT JOIN sources s ON s.id=n.source_id WHERE n.deleted_at IS NULL
                ORDER BY n.updated_at DESC, n.id LIMIT ? OFFSET ?''',
                (limit, offset))]

    def get(self, note_id):
        with self._connection() as db:
            return self._note(db.execute('''SELECT n.*, COALESCE(s.owner_id, '') AS owner_id FROM notes n
                                         LEFT JOIN sources s ON s.id=n.source_id
                                         WHERE n.id=? AND n.deleted_at IS NULL''',
                                         (note_id,)).fetchone())

    @staticmethod
    def _records(db):
        notes = [Store._note(row) for row in db.execute('''SELECT n.*, COALESCE(s.owner_id, '') AS owner_id
            FROM notes n LEFT JOIN sources s ON s.id=n.source_id WHERE n.deleted_at IS NULL''')]
        chunks = []
        for row in db.execute('SELECT * FROM chunks'):
            item = dict(row)
            item['kind'] = 'chunk'
            item['sources'] = [dict(source) for source in db.execute(
                'SELECT source_id, message_no FROM chunk_sources WHERE chunk_id=? ORDER BY source_id, message_no',
                (item['id'],))]
            item['source_id'] = item['sources'][0]['source_id'] if item['sources'] else ''
            chunks.append(item)
        return notes + chunks

    def search(self, query, limit=6, query_vector=None, model_id='', exclude_message_hashes=None):
        if not isinstance(query, str):
            raise ValueError('query must be text')  # noqa: TRY004 -- public validation errors.
        if not isinstance(limit, int) or limit < 0:
            raise ValueError('limit must be a non-negative integer')
        if query_vector is not None and not model_id:
            raise ValueError('model_id is required for vector search')
        vector = _vector(query_vector) if query_vector is not None else None
        if not limit:
            return []
        with self._connection() as db:
            records = self._records(db)
            excluded = set(exclude_message_hashes or ())
            if excluded:
                records = [item for item in records
                           if item['kind'] != 'chunk' or item['message_hash'] not in excluded]
            by_id = {item['id']: item for item in records}
            terms = _tokens(query)
            lexical = []
            if terms and records:
                documents = [_tokens(item['text']) for item in records]
                frequency = Counter(term for doc in documents for term in terms if term in doc)
                average = sum(sum(doc.values()) for doc in documents) / len(documents) or 1
                for item, doc in zip(records, documents):
                    size = sum(doc.values())
                    score = 0.0
                    for term, count in terms.items():
                        tf = doc.get(term, 0)
                        if tf:
                            inverse = math.log(1 + (len(documents) - frequency[term] + .5) / (frequency[term] + .5))
                            score += inverse * (tf * 2.2 / (tf + 1.2 * (.25 + .75 * size / average))) * min(count, 2)
                    if score:
                        if query.strip().casefold() in item['text'].casefold():
                            score *= 1.25
                        if item['kind'] == 'note':
                            age_days = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(item['updated_at'])).total_seconds() / 86400)
                            score *= 1 + .1 / (1 + age_days / 30)
                        lexical.append((item['id'], score))
                lexical.sort(key=lambda entry: (-entry[1], entry[0]))

            semantic = []
            if vector is not None:
                model = db.execute('SELECT dimension FROM embedding_models WHERE model_id=?', (model_id,)).fetchone()
                if model and len(vector) != model['dimension']:
                    raise ValueError('query vector dimension does not match model')
                for row in db.execute('SELECT record_id, vector FROM vectors WHERE model_id=?', (model_id,)):
                    if row['record_id'] not in by_id:
                        continue
                    score = _cosine(vector, json.loads(row['vector']))
                    if score > 0:
                        semantic.append((row['record_id'], score))
                semantic.sort(key=lambda entry: (-entry[1], entry[0]))

            if vector is None:
                ranked = lexical
            else:
                combined = Counter()
                for ranking in (lexical, semantic):
                    for rank, (record_id, _) in enumerate(ranking, 1):
                        combined[record_id] += 1 / (60 + rank)
                ranked = sorted(combined.items(), key=lambda entry: (-entry[1], entry[0]))
            return [{**by_id[record_id], 'score': score} for record_id, score in ranked[:limit]]

    def pending(self, model_id, limit=100):
        if not isinstance(model_id, str) or not model_id:
            raise ValueError('model_id is required')
        if not isinstance(limit, int) or limit < 0:
            raise ValueError('limit must be a non-negative integer')
        with self._connection() as db:
            return [dict(row) for row in db.execute('''
                SELECT records.id, records.text FROM (
                    SELECT id, text, created_at FROM notes WHERE deleted_at IS NULL
                    UNION ALL SELECT id, text, created_at FROM chunks
                ) records LEFT JOIN vectors v ON v.record_id=records.id AND v.model_id=?
                WHERE v.record_id IS NULL ORDER BY records.created_at, records.id LIMIT ?
            ''', (model_id, limit))]

    def save_vectors(self, model_id, pairs):
        """Save (id, vector) pairs or (id, vector, expected_text) triples.

        A triple is skipped if the note changed or the record disappeared while
        its embedding was being computed. A write lock makes that comparison
        atomic with the insertion and concurrent note edits.
        """
        if not isinstance(model_id, str) or not model_id:
            raise ValueError('model_id is required')
        values = []
        for item in pairs:
            if not isinstance(item, (list, tuple)) or len(item) not in (2, 3):
                raise ValueError('each embedding must be an id/vector pair or id/vector/text triple')
            record_id, vector = item[:2]
            expected = item[2] if len(item) == 3 else None
            if len(item) == 3 and not isinstance(expected, str):
                raise ValueError('expected text must be text')
            values.append((record_id, _vector(vector), expected))
        if not values:
            return 0
        dimensions = {len(vector) for _, vector, _ in values}
        if len(dimensions) != 1:
            raise ValueError('vectors must have consistent dimensions')
        dimension = dimensions.pop()
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            model = db.execute('SELECT dimension FROM embedding_models WHERE model_id=?', (model_id,)).fetchone()
            if model and model['dimension'] != dimension:
                raise ValueError('vector dimension does not match model')
            current = []
            for record_id, vector, expected in values:
                row = db.execute('''SELECT text FROM chunks WHERE id=? UNION ALL
                    SELECT text FROM notes WHERE id=? AND deleted_at IS NULL''', (record_id, record_id)).fetchone()
                if expected is not None and (not row or row['text'] != expected):
                    continue
                if not row:
                    raise ValueError('active record not found: ' + str(record_id))
                current.append((record_id, vector))
            if not current:
                return 0
            if not model:
                db.execute('INSERT INTO embedding_models VALUES (?, ?, ?)', (model_id, dimension, _now()))
            db.executemany('INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)',
                           [(model_id, record_id, _json(vector)) for record_id, vector in current])
        return len(current)

    def clear_vectors(self, model_id):
        """Remove one model's vector index so all active records can be reindexed."""
        if not isinstance(model_id, str) or not model_id:
            raise ValueError('model_id is required')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('DELETE FROM vectors WHERE model_id=?', (model_id,)).rowcount
            db.execute('DELETE FROM embedding_models WHERE model_id=?', (model_id,))
            return count

    def stats(self):
        with self._connection() as db:
            count = lambda sql: db.execute(sql).fetchone()[0]
            return {
                'sources': count('SELECT count(*) FROM sources'),
                'observations': count('SELECT count(*) FROM observations'),
                'chunks': count('SELECT count(*) FROM chunks'),
                'notes': count('SELECT count(*) FROM notes WHERE deleted_at IS NULL'),
                'deleted_notes': count('SELECT count(*) FROM notes WHERE deleted_at IS NOT NULL'),
                'vectors': count('SELECT count(*) FROM vectors'),
                'models': [dict(row) for row in db.execute('SELECT * FROM embedding_models ORDER BY model_id')],
            }

    def export_zip(self):
        """Write a consistent database backup, all raw sources, and portable notes."""
        with self._lock:
            self.exports_dir.mkdir(parents=True, exist_ok=True)
            name = datetime.now(UTC).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid.uuid4().hex[:8] + '.zip'
            target = self.exports_dir / name
            with tempfile.TemporaryDirectory(prefix='.export-', dir=self.exports_dir) as temporary:
                directory = Path(temporary)
                snapshot = directory / 'memory.sqlite3'
                with self._connection() as db:
                    backup = sqlite3.connect(snapshot)
                    try:
                        db.backup(backup)
                    finally:
                        backup.close()
                with sqlite3.connect(snapshot) as db:
                    db.row_factory = sqlite3.Row
                    notes = [self._note(row) for row in db.execute('''SELECT n.*, COALESCE(s.owner_id, '') AS owner_id
                        FROM notes n LEFT JOIN sources s ON s.id=n.source_id ORDER BY n.created_at, n.id''')]
                    models = [dict(row) for row in db.execute('SELECT * FROM embedding_models ORDER BY model_id')]
                    sources = [dict(row) for row in db.execute('SELECT * FROM sources ORDER BY id')]
                    observations = db.execute('SELECT count(*) FROM observations').fetchone()[0]
                raw_files = sorted(self.raw_dir.glob('*.json'))
                raw_names = {path.stem for path in raw_files}
                if any(source['id'] not in raw_names for source in sources):
                    raise ValueError('source archive missing from export')
                manifest = {
                    'format': 'nexus-temporary-memory', 'version': 1, 'exported_at': _now(),
                    'database': 'memory.sqlite3', 'notes': 'notes.json',
                    'raw_files': ['raw/' + path.name for path in raw_files],
                    'sources': sources, 'observations': observations, 'embedding_models': models,
                    'retention': 'Raw archives are retained when a searchable note is deactivated.',
                }
                archive = directory / 'export.zip'
                with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as out:
                    out.write(snapshot, 'memory.sqlite3')
                    out.writestr('manifest.json', _json(manifest).encode('utf-8'))
                    out.writestr('notes.json', _json(notes).encode('utf-8'))
                    for path in raw_files:
                        raw = path.read_bytes()
                        if _digest(raw) != path.stem:
                            raise ValueError('source archive content does not match its identifier')
                        out.writestr('raw/' + path.name, raw)
                os.replace(archive, target)
            return target
