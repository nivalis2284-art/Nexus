"""Export real page descriptions against fixture data, without an Agent or credentials."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from test_followup import Engine, Page, Work

with tempfile.TemporaryDirectory(prefix='followup-page-') as directory:
    work = Work(directory)
    work.tasks['source'] = {'id': 'source', 'title': '源调研工作', 'state': 'completed'}
    engine = Engine(SimpleNamespace(work=work), Path(directory) / 'data', zone=lambda: ZoneInfo('Asia/Tokyo'))
    try:
        engine.save({'title': '关注项目', 'goal': '检查项目新版本，有变化时汇报。', 'trigger': {'kind': 'daily', 'time': '09:00'}})
        page = Page(engine)
        view = page.render({'action': 'new'})
        values = {v['name']: v['value'] for v in view['dialog']['components'] if v['type'] == 'field'}
        views = {'open': page.render({'event': 'open'}), 'new': view,
                 'kind': page.render({'action': 'kind', 'values': {**values, 'kind': 'weekly'}}),
                 'save': page.render({'action': 'save', 'values': {**values, 'title': '保留的草稿'}})}
        print(json.dumps(views, ensure_ascii=True))
    finally:
        engine.close()
