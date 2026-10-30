"""Reconciled current history and durable change cursors; JSONL remains authoritative."""
import base64
import json
from .store import uid

class History:
    def __init__(self, runtime):
        self.runtime = runtime
        self.sessions = runtime.manager.state.get('sessions')
        if self.sessions:
            self.sessions.identity_enabled = True
            self.sessions.on_change = self.sync

    def session_id(self):
        return self.runtime.manager.query('', 'ling.identity')['session_id']

    @staticmethod
    def record(item):
        return {'kind': item.kind, 'text': item.text, 'at': item.at, **item.data}

    def sync(self, session):
        current = {m.data['_record_id']: self.record(m) for m in session.items if '_record_id' in m.data}
        store = self.runtime.store
        with store.lock, store.db:
            old_rows = store.db.execute("SELECT id,body FROM records WHERE kind='history_record' AND owner=?", (session.id,)).fetchall()
            previous = {r['id']: json.loads(r['body']) for r in old_rows}
            for key, record in current.items():
                old = previous.get(key)
                if old and old.get('present') and old['version'] == record['_record_version']:
                    continue
                kind = 'saved' if old is None else 'restored' if not old['present'] else 'updated'
                value = {'id': key, 'version': record['_record_version'], 'present': True, 'session_id': session.id}
                store.db.execute('INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body', ('history_record', key, session.id, json.dumps(value)))
                store.db.execute('INSERT INTO events(owner,subject,type,body,at) VALUES(?,?,?,?,datetime(\'now\'))', ('@history', session.id, 'history.' + ('compacted' if record['kind'] == 'checkpoint' else kind), json.dumps({**value, 'kind': record['kind'], 'through_record_id': session.items[int(record['through'])].data.get('_record_id') if record['kind'] == 'checkpoint' and record.get('through') is not None else None})))
            for key, old in previous.items():
                if key not in current and old['present']:
                    old['present'] = False
                    store.db.execute("UPDATE records SET body=? WHERE kind='history_record' AND id=?", (json.dumps(old), key))
                    store.db.execute('INSERT INTO events(owner,subject,type,body,at) VALUES(?,?,?,?,datetime(\'now\'))', ('@history', session.id, 'history.deleted', json.dumps(old)))
            store.changed.notify_all()

    @staticmethod
    def public(record):
        return {k: v for k, v in record.items() if k not in ('thinking', 'native')}

    def load(self):
        if not self.sessions:
            raise ValueError('历史服务尚未初始化')
        with self.sessions._lock:
            session = self.sessions.load(self.session_id())
            if session is None:
                raise ValueError('主交流不存在')
            self.sync(session)
            return session

    def snapshot(self, cursor=None, limit=100):
        with self.sessions._lock:
            session = self.load()
            from ...runtime.history import history_version
            version = history_version(session)
            offset = 0
            if cursor:
                expected, offset = json.loads(base64.urlsafe_b64decode(cursor))
                if expected != version:
                    raise ValueError('history_changed: 请重新读取快照')
            limit = max(1, min(500, int(limit)))
            records = [self.public(self.record(m)) for m in session.items[offset:offset+limit]]
            with self.runtime.store.lock:
                head = self.runtime.store.db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE owner='@history' AND subject=?", (session.id,)).fetchone()[0]
            end = offset + len(records)
            next_cursor = base64.urlsafe_b64encode(json.dumps([version, end]).encode()).decode() if end < len(session.items) else None
            return {'session_id': session.id, 'version': version, 'records': records, 'next': next_cursor, 'change_cursor': head}

    def get(self, record_id):
        with self.sessions._lock:
            session = self.load()
            for item in session.items:
                if item.data['_record_id'] == record_id:
                    return self.public(self.record(item))
        raise ValueError('记录不存在或已删除')

    def changes(self, after=0, limit=100):
        self.load()
        events = self.runtime.store.events('@history', after, self.session_id(), limit)
        return {'events': events, 'cursor': events[-1]['seq'] if events else int(after)}
