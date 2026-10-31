"""Durable QQ receipts and review outcomes, separate from the owner's chat."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / 'qq.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS messages (
                  id TEXT PRIMARY KEY, received_at TEXT NOT NULL, status TEXT NOT NULL,
                  envelope TEXT NOT NULL, prepared TEXT NOT NULL DEFAULT '{}',
                  review TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                  input_id INTEGER, forwarded_text TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS sends (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                  conversation TEXT NOT NULL, reply_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                  status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}');
            ''')
            db.execute("UPDATE messages SET status='held',error='预审中断，可在收件箱重新处理' WHERE status='reviewing'")
            db.execute("UPDATE sends SET status='unknown' WHERE status='sending'")

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
        for key in ('envelope', 'prepared', 'review'):
            value[key] = json.loads(value[key])
        return value

    def add(self, app_id, envelope):
        identity = [app_id, envelope['conversation'], envelope['message_id'], envelope['message_index']]
        ident = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO messages(id,received_at,status,envelope) VALUES(?,?,?,?)',
                (ident, now(), 'pending', json.dumps(envelope, ensure_ascii=False)))
        return self.get(ident)

    def get(self, ident):
        with self.db() as db:
            return self.decode(db.execute('SELECT * FROM messages WHERE id=?', (ident,)).fetchone())

    def update(self, ident, **values):
        allowed = {'status', 'prepared', 'review', 'error', 'input_id', 'forwarded_text'}
        if not values or not set(values) <= allowed:
            raise ValueError('无效的消息更新字段')
        for key in ('prepared', 'review'):
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        with self.db() as db:
            db.execute('UPDATE messages SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                       [*values.values(), ident])

    def next(self):
        with self.db() as db:
            return self.decode(db.execute(
                "SELECT * FROM messages WHERE status IN ('pending','allowed') ORDER BY received_at,rowid LIMIT 1").fetchone())

    def list(self, limit=60):
        with self.db() as db:
            return [self.decode(row) for row in db.execute(
                'SELECT * FROM messages ORDER BY received_at DESC,rowid DESC LIMIT ?', (limit,))]

    def forwarded(self):
        with self.db() as db:
            return [self.decode(row) for row in db.execute(
                "SELECT * FROM messages WHERE forwarded_text!=''")]

    def reserve_send(self, conversation, reply_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            key = json.dumps(conversation, sort_keys=True)
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM sends WHERE conversation=? AND reply_id=?',
                                  (key, reply_id)).fetchone()[0]
            ident = db.execute('INSERT INTO sends(created_at,conversation,reply_id,sequence,status) VALUES(?,?,?,?,?)',
                              (now(), key, reply_id, sequence, 'sending')).lastrowid
        return ident, sequence

    def finish_send(self, ident, status, result):
        with self.db() as db:
            db.execute('UPDATE sends SET status=?,result=? WHERE id=?',
                       (status, json.dumps(result, ensure_ascii=False), ident))
