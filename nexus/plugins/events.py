"""Durable plugin inbox and host change subscriptions, without a message broker."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime


def now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


class EventStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                  id TEXT PRIMARY KEY, plugin_id TEXT NOT NULL, external_id TEXT NOT NULL,
                  type TEXT NOT NULL, text TEXT NOT NULL, data TEXT NOT NULL,
                  reply_required INTEGER NOT NULL, status TEXT NOT NULL,
                  created_at TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                  error TEXT NOT NULL DEFAULT '', UNIQUE(plugin_id,external_id));
                CREATE TABLE IF NOT EXISTS changes (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                  type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS subscriptions (
                  plugin_id TEXT PRIMARY KEY, types TEXT NOT NULL, cursor INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            db.execute("UPDATE events SET status='interrupted',error='运行中断；请检查已完成操作后再继续' WHERE status='running'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        value = dict(row)
        value['data'] = json.loads(value['data'])
        value['result'] = json.loads(value['result'])
        value['reply_required'] = bool(value['reply_required'])
        return value

    def add(self, plugin_id, payload, enabled=True):
        external_id = str(payload.get('id') or uuid.uuid4().hex)
        with self.lock, self.db() as db:
            db.execute("""INSERT OR IGNORE INTO events
                (id,plugin_id,external_id,type,text,data,reply_required,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (
                uuid.uuid4().hex, plugin_id, external_id, str(payload.get('type') or 'event'),
                str(payload.get('text') or payload.get('prompt') or ''),
                json.dumps(payload.get('data', payload), ensure_ascii=False),
                bool(payload.get('reply_required', payload.get('notify', False))),
                'pending' if enabled else 'paused', payload.get('created_at') or now_iso()))
            return self.decode(db.execute('SELECT * FROM events WHERE plugin_id=? AND external_id=?',
                                         (plugin_id, external_id)).fetchone())

    def get(self, event_id):
        with self.db() as db:
            return self.decode(db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone())

    def update(self, event_id, *, status, result=None, error=''):
        with self.lock, self.db() as db:
            db.execute('UPDATE events SET status=?,result=?,error=? WHERE id=?',
                       (status, json.dumps(result or {}, ensure_ascii=False), error, event_id))

    def pending(self, plugin_ids):
        if not plugin_ids:
            return None
        with self.db() as db:
            marks = ','.join('?' for _ in plugin_ids)
            return self.decode(db.execute(f"SELECT * FROM events WHERE status='pending' AND plugin_id IN ({marks}) ORDER BY created_at,rowid LIMIT 1", list(plugin_ids)).fetchone())

    def pause(self, plugin_id, paused):
        with self.lock, self.db() as db:
            db.execute('UPDATE events SET status=? WHERE plugin_id=? AND status=?',
                       ('paused' if paused else 'pending', plugin_id, 'pending' if paused else 'paused'))

    def list(self, plugin_id=None, limit=100):
        with self.db() as db:
            rows = db.execute('SELECT * FROM events' + (' WHERE plugin_id=?' if plugin_id else '') +
                              ' ORDER BY created_at DESC,rowid DESC LIMIT ?',
                              ((plugin_id, limit) if plugin_id else (limit,))).fetchall()
            return [self.decode(row) for row in rows]

    def publish(self, event_type, data, event_id=None):
        with self.lock, self.db() as db:
            db.execute('INSERT OR IGNORE INTO changes(id,type,data,created_at) VALUES(?,?,?,?)',
                       (event_id or uuid.uuid4().hex, event_type, json.dumps(data, ensure_ascii=False), now_iso()))

    def subscribe(self, plugin_id, types):
        with self.lock, self.db() as db:
            head = db.execute('SELECT COALESCE(MAX(seq),0) FROM changes').fetchone()[0]
            db.execute('INSERT INTO subscriptions(plugin_id,types,cursor) VALUES(?,?,?) ON CONFLICT(plugin_id) DO UPDATE SET types=excluded.types',
                       (plugin_id, json.dumps(types), head))

    def changes(self, plugin_id):
        with self.db() as db:
            subscription = db.execute('SELECT * FROM subscriptions WHERE plugin_id=?', (plugin_id,)).fetchone()
            if not subscription:
                return []
            types = json.loads(subscription['types'])
            rows = db.execute('SELECT * FROM changes WHERE seq>? ORDER BY seq LIMIT 100', (subscription['cursor'],)).fetchall()
            return [{**dict(row), 'data': json.loads(row['data']), 'subscribed': row['type'] in types}
                    for row in rows]

    def ack(self, plugin_id, seq):
        with self.lock, self.db() as db:
            db.execute('UPDATE subscriptions SET cursor=? WHERE plugin_id=?', (seq, plugin_id))

    def metadata(self, key, value=None):
        with self.lock, self.db() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)', (key, str(value)))
            row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            return row['value'] if row else None
