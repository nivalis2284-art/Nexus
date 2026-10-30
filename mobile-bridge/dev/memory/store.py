"""Authoritative particles and immutable selected originals, with a rebuildable vec0 index."""
import json
import sqlite3
import threading
from pathlib import Path
import sqlite_vec
from models import DEFAULTS, digest, now, uid, validate

class Store:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.home / 'memory.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources(source_key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,batch_id TEXT,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,status TEXT NOT NULL,records TEXT,selection TEXT,error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS originals(id TEXT PRIMARY KEY,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS segments(id TEXT PRIMARY KEY,record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS original_segments(original_id TEXT REFERENCES originals(id),segment_id TEXT REFERENCES segments(id),position INTEGER,PRIMARY KEY(original_id,segment_id));
        CREATE TABLE IF NOT EXISTS particles(id TEXT PRIMARY KEY,document TEXT NOT NULL,version TEXT NOT NULL,embedding_status TEXT NOT NULL DEFAULT 'pending',embedding_error TEXT NOT NULL DEFAULT '');
        ''')
        with self.db:
            self.db.execute("UPDATE batches SET status=CASE WHEN selection IS NULL THEN 'pending' ELSE 'selected' END WHERE status='processing'")

    def getmeta(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def setmeta(self, key, value):
        self.db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        with self.lock:
            return {**DEFAULTS, **self.getmeta('settings', {})}

    def revision(self):
        with self.lock:
            return self.getmeta('revision', 0)

    def bump(self):
        self.setmeta('revision', self.getmeta('revision', 0) + 1)

    def ingest(self, session_id, records, baseline=False):
        """Deduplicate original record versions, assigning plugin-owned immutable segment IDs."""
        with self.lock, self.db:
            initialized = self.getmeta('initialized', False)
            if initialized:
                open_calls, first_open = set(), None
                for index, record in enumerate(records):
                    calls = [c['id'] for c in record.get('tool_calls', []) if c.get('id')]
                    if calls and not open_calls:
                        first_open = index
                    open_calls.update(calls)
                    if record.get('kind') == 'tool':
                        open_calls.discard(record.get('tool_call_id'))
                    if not open_calls:
                        first_open = None
                if open_calls:
                    records = records[:first_open]
            occurrences, pending = {}, []
            for record in records:
                kind = record.get('kind')
                if kind not in ('user', 'assistant', 'tool'):
                    continue
                clean = {k: record[k] for k in ('kind', 'text', 'at', 'ling_input_id', 'message_id', 'tool_call_id', 'parent_message_id', 'name', 'arguments', 'tool_calls', 'attachments') if k in record}
                native = record.get({'user': 'ling_input_id', 'assistant': 'message_id', 'tool': 'tool_call_id'}[kind])
                fingerprint = digest(clean)
                if native is not None:
                    identity = [session_id, kind, native, record.get('parent_message_id') if kind == 'tool' else None]
                else:
                    occurrences[fingerprint] = occurrences.get(fingerprint, 0) + 1
                    identity = [session_id, kind, fingerprint, occurrences[fingerprint]]
                key = digest([identity, fingerprint])
                if self.db.execute('SELECT 1 FROM sources WHERE source_key=?', (key,)).fetchone():
                    continue
                state = 'baseline' if not initialized else 'pending'
                self.db.execute('INSERT INTO sources VALUES(?,?,NULL,?)', (key, fingerprint, state))
                if initialized:
                    pending.append({'segment_id': key, **clean})
            if not initialized:
                self.setmeta('initialized', True)
                self.setmeta('baseline_at', now())
            if pending:
                # Never split an assistant tool call from its results. An unfinished exchange stays in one snapshot.
                batch_id = uid()
                self.db.execute('INSERT INTO batches VALUES(?,?,?,NULL,?,?)', (batch_id, 'pending', json.dumps(pending, ensure_ascii=False), '', now()))
                self.db.executemany('UPDATE sources SET batch_id=? WHERE source_key=?', [(batch_id, r['segment_id']) for r in pending])
            return {'captured': len(pending), 'baseline': not initialized}

    def split_batch(self, batch_id, groups):
        with self.lock, self.db:
            self.db.execute('DELETE FROM batches WHERE id=?', (batch_id,))
            for records in groups:
                key = uid()
                self.db.execute('INSERT INTO batches VALUES(?,?,?,NULL,?,?)', (key, 'pending', json.dumps(records, ensure_ascii=False), '', now()))
                self.db.executemany('UPDATE sources SET batch_id=? WHERE source_key=?', [(key, r['segment_id']) for r in records])

    def batch(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM batches WHERE status IN ('pending','selected') ORDER BY created_at,rowid LIMIT 1").fetchone()
            return dict(row) if row else None

    def commit(self, batch_id, particles, records):
        with self.lock, self.db:
            batch = self.db.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()
            if not batch or batch[0] == 'done':
                return
            by_id = {r['segment_id']: r for r in records}
            for p in particles:
                validate(p)
                refs = p['provenance']['segment_ids']
                if not refs or any(ref not in by_id for ref in refs):
                    raise ValueError('粒子原文引用不存在')
                original_id = p['provenance']['source_id']
                self.db.execute('INSERT INTO originals VALUES(?,?)', (original_id, now()))
                for index, ref in enumerate(refs):
                    self.db.execute('INSERT OR IGNORE INTO segments VALUES(?,?)', (ref, json.dumps(by_id[ref], ensure_ascii=False)))
                    self.db.execute('INSERT INTO original_segments VALUES(?,?,?)', (original_id, ref, index))
                self.db.execute('INSERT INTO particles(id,document,version) VALUES(?,?,?)', (p['id'], json.dumps(p, ensure_ascii=False), digest(p['content'])))
            self.db.execute("UPDATE batches SET status='done',records=NULL,selection=NULL,error='' WHERE id=?", (batch_id,))
            self.db.execute("UPDATE sources SET status='done' WHERE batch_id=?", (batch_id,))
            self.bump()

    def get(self, particle_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM particles WHERE id=?', (particle_id,)).fetchone()
            if row is None:
                raise ValueError('粒子不存在')
            return {**json.loads(row['document']), 'embedding_status': row['embedding_status'], 'embedding_error': row['embedding_error']}

    def expand(self, particle_id):
        p = self.get(particle_id)
        with self.lock:
            rows = self.db.execute('SELECT s.record FROM original_segments o JOIN segments s ON s.id=o.segment_id WHERE original_id=? ORDER BY position', (p['provenance']['source_id'],)).fetchall()
            return {'particle_id': particle_id, 'status': 'available' if rows else 'unavailable', 'segments': [json.loads(r[0]) for r in rows]}

    def list(self, offset=0, limit=30, query=''):
        with self.lock:
            where, args = (' WHERE instr(json_extract(document,\'$.content\'),?)>0', [query]) if query else ('', [])
            total = self.db.execute('SELECT count(*) FROM particles' + where, args).fetchone()[0]
            rows = self.db.execute('SELECT id FROM particles' + where + ' ORDER BY rowid DESC LIMIT ? OFFSET ?', [*args, max(1, min(100, limit)), max(0, offset)]).fetchall()
            return {'total': total, 'particles': [self.get(r[0]) for r in rows], 'offset': offset}

    def edit(self, particle_id, content, event_time):
        with self.lock, self.db:
            self.get(particle_id)
            p = json.loads(self.db.execute('SELECT document FROM particles WHERE id=?', (particle_id,)).fetchone()[0])
            p.update(content=content.strip(), event_time=event_time, updated_at=now(), manually_edited=True)
            validate(p)
            rowid = self.db.execute('SELECT rowid FROM particles WHERE id=?', (particle_id,)).fetchone()[0]
            self.drop_vector(rowid)
            self.db.execute("UPDATE particles SET document=?,version=?,embedding_status='pending',embedding_error='' WHERE id=?", (json.dumps(p, ensure_ascii=False), digest(p['content']), particle_id))
            self.bump()
            return self.get(particle_id)

    def drop_vector(self, rowid):
        if self.getmeta('index'):
            self.db.execute('DELETE FROM vector_index WHERE rowid=?', (rowid,))

    def delete(self, particle_id):
        with self.lock, self.db:
            row = self.db.execute('SELECT rowid FROM particles WHERE id=?', (particle_id,)).fetchone()
            if row:
                self.drop_vector(row[0])
                self.db.execute('DELETE FROM particles WHERE id=?', (particle_id,))
                self.bump()
            return {'deleted': bool(row)}

    def cleanup(self, execute=False):
        with self.lock, self.db:
            used = [json.loads(r[0])['provenance']['source_id'] for r in self.db.execute('SELECT document FROM particles')]
            all_ids = [r[0] for r in self.db.execute('SELECT id FROM originals')]
            unused = set(all_ids) - set(used)
            if execute:
                for key in unused:
                    self.db.execute('DELETE FROM original_segments WHERE original_id=?', (key,))
                    self.db.execute('DELETE FROM originals WHERE id=?', (key,))
                self.db.execute('DELETE FROM segments WHERE id NOT IN (SELECT segment_id FROM original_segments)')
            return {'unreferenced_originals': len(unused), 'cleaned': execute}

    def reset_index(self):
        self.db.execute('DROP TABLE IF EXISTS vector_index')
        self.setmeta('index', None)
        self.db.execute("UPDATE particles SET embedding_status='pending',embedding_error=''")
        self.bump()

    def put_vector(self, particle_id, version, vector, model_key):
        with self.lock, self.db:
            row = self.db.execute('SELECT rowid,version FROM particles WHERE id=?', (particle_id,)).fetchone()
            if not row or row['version'] != version:
                return
            info = self.getmeta('index')
            if not info:
                self.db.execute(f'CREATE VIRTUAL TABLE vector_index USING vec0(embedding float[{len(vector)}] distance_metric=cosine)')
                self.setmeta('index', {'model_key': model_key, 'dimensions': len(vector)})
            elif info != {'model_key': model_key, 'dimensions': len(vector)}:
                raise ValueError('嵌入模型或维度变化，请重建索引')
            self.drop_vector(row['rowid'])
            self.db.execute('INSERT INTO vector_index(rowid,embedding) VALUES(?,?)', (row['rowid'], sqlite_vec.serialize_float32(vector)))
            self.db.execute("UPDATE particles SET embedding_status='ready',embedding_error='' WHERE id=?", (particle_id,))
            self.bump()

    def nearest(self, vector, limit, threshold, model_key):
        with self.lock:
            info = self.getmeta('index')
            if not info:
                return []
            if info != {'model_key': model_key, 'dimensions': len(vector)}:
                raise ValueError('查询嵌入模型与索引不一致')
            rows = self.db.execute('SELECT p.id,v.distance FROM vector_index v JOIN particles p ON p.rowid=v.rowid WHERE v.embedding MATCH ? AND k=? ORDER BY v.distance', (sqlite_vec.serialize_float32(vector), limit)).fetchall()
            return [{**self.get(r['id']), 'similarity': 1-r['distance']} for r in rows if 1-r['distance'] >= threshold]

    def status(self):
        with self.lock:
            return {'particles': self.db.execute('SELECT count(*) FROM particles').fetchone()[0],
                    'baseline_at': self.getmeta('baseline_at'), 'revision': self.revision(),
                    'batches': [dict(r) for r in self.db.execute('SELECT id,status,error,created_at FROM batches ORDER BY rowid DESC LIMIT 50')],
                    'embeddings': {r[0]: r[1] for r in self.db.execute('SELECT embedding_status,count(*) FROM particles GROUP BY embedding_status')},
                    'cleanup': self.cleanup(), 'index': self.getmeta('index')}
