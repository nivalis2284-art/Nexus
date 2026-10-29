"""Create a throwaway task through the real work_start path, then park it.

Used by the frontend live checks so the work page has something to render.
"""

import json
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:8766'
TARGET = sys.argv[1] if len(sys.argv) > 1 else 'completed'
PROMPT = sys.argv[2] if len(sys.argv) > 2 else '统计 tests 目录下有多少个 .py 文件'


def call(path, method='GET', body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read().decode('utf-8'))


before = {t['id'] for t in call('/api/tasks?archived=1')['tasks']}
call('/api/ling/input', 'POST', {
    'text': f'请用 work_start 工具派发一个只读后台任务：{PROMPT}。不要在主交流里直接执行。',
    'request_id': 'seed-' + str(int(time.time() * 1000)),
})

created = None
for _ in range(40):
    time.sleep(3)
    fresh = [t for t in call('/api/tasks?archived=1')['tasks'] if t['id'] not in before]
    if fresh:
        created = fresh[0]
        break
if not created:
    raise SystemExit('no task created')
print('created:', created['id'], created['state'], 'v', created['control_version'])

for _ in range(24):
    tasks = {t['id']: t for t in call('/api/tasks?archived=1')['tasks']}
    task = tasks.get(created['id'])
    if not task:
        break
    state = task['state']
    if state in ('running', 'queued', 'pausing'):
        try:
            call(f"/api/tasks/{task['id']}/commands", 'POST', {
                'operation': 'pause', 'request_id': 'seed-pause-' + task['id'],
                'expected_version': task['control_version'],
            })
        except Exception as error:
            print('pause failed:', error)
        time.sleep(2)
        continue
    if state == 'paused' and TARGET == 'completed':
        try:
            call(f"/api/tasks/{task['id']}/lifecycle", 'POST', {
                'operation': 'mark_completed', 'request_id': 'seed-done-' + task['id'],
                'expected_version': task['control_version'],
            })
            print('marked completed')
        except Exception as error:
            print('complete failed:', error)
        break
    if state in ('paused', 'completed'):
        break
    time.sleep(3)

final = {t['id']: t for t in call('/api/tasks?archived=1')['tasks']}.get(created['id'])
print('final:', final['state'] if final else 'missing')
