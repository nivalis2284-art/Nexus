"""Durable plugin-owned runtime records, independent of Agent tasks."""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class Store:
    def __init__(self, home):
        home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.db = sqlite3.connect(home / 'plugin-runtime.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,owner TEXT,body TEXT NOT NULL,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT,subject TEXT,type TEXT,body TEXT,at TEXT);
            CREATE INDEX IF NOT EXISTS event_owner ON events(owner,seq);
        ''')

    def put(self, kind, key, owner, value):
        with self.lock, self.db:
            self.db.execute('INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
                            (kind, key, owner, json.dumps(value, ensure_ascii=False)))
            self.changed.notify_all()
        return value

    def get(self, kind, key, owner=None):
        with self.lock:
            row = self.db.execute('SELECT owner,body FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
            if row is None or owner is not None and row['owner'] != owner:
                raise ValueError('资源不存在或不属于当前插件')
            return json.loads(row['body'])

    def list(self, kind, owner=None):
        with self.lock:
            rows = self.db.execute('SELECT body FROM records WHERE kind=?' + (' AND owner=?' if owner is not None else ''),
                                   (kind, owner) if owner is not None else (kind,)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def delete(self, kind, key, owner):
        with self.lock, self.db:
            self.db.execute('DELETE FROM records WHERE kind=? AND id=? AND owner=?', (kind, key, owner))

    def emit(self, owner, subject, event_type, data):
        with self.lock, self.db:
            cursor = self.db.execute('INSERT INTO events(owner,subject,type,body,at) VALUES(?,?,?,?,?)',
                                     (owner, subject, event_type, json.dumps(data, ensure_ascii=False), now()))
            self.changed.notify_all()
            return cursor.lastrowid

    def events(self, owner, after=0, subject=None, limit=100):
        with self.lock:
            rows = self.db.execute('SELECT * FROM events WHERE owner=? AND seq>?' + (' AND subject=?' if subject else '') + ' ORDER BY seq LIMIT ?',
                                   (owner, int(after), subject, min(1000, int(limit))) if subject else (owner, int(after), min(1000, int(limit)))).fetchall()
            return [{**{k: row[k] for k in ('seq', 'subject', 'type', 'at')}, 'data': json.loads(row['body'])} for row in rows]
