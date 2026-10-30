"""Finite jobs, explicit checkpoints and recover handlers; no Agent execution."""
import threading
from concurrent.futures import ThreadPoolExecutor
from .store import uid, now

class JobContext:
    def __init__(self, jobs, owner, job_id, attempt):
        self.jobs, self.owner, self.job_id, self.attempt = jobs, owner, job_id, attempt
        self._cancel = jobs.cancel_events[job_id]

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def checkpoint(self, value):
        return self.jobs.report(self.owner, self.job_id, self.attempt, checkpoint=value)

    def progress(self, value):
        return self.jobs.report(self.owner, self.job_id, self.attempt, progress=value)


class Jobs:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.handlers = {}
        self.cancel_events = {}
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='plugin-job')
        for job in self.store.list('job'):
            if job['status'] in ('running', 'queued', 'recovering'):
                job.update(status='interrupted', error='宿主重启，等待显式恢复处理器')
                self.store.put('job', job['id'], job['owner'], job)

    def register(self, owner, name, version=1, handler=None, recover=None, target=None, recover_target=None):
        if not name or type(version) is not int or version < 1:
            raise ValueError('作业类型和版本无效')
        if not callable(handler) and not target:
            raise ValueError('需要运行处理器或本插件 MCP 入口')
        self.handlers[(owner, name)] = {'version': version, 'handler': handler, 'recover': recover, 'target': target, 'recover_target': recover_target}
        item = self.runtime.manager.entries.get(owner)
        if item and item.status == 'ready': self.recover_owner(owner)
        return {'name': name, 'version': version, 'recoverable': bool(recover or recover_target)}

    def submit(self, owner, name, input, request_id, recoverable=False):
        with self.store.lock:
            for job in self.store.list('job', owner):
                if job['request_id'] == request_id:
                    if job['name'] != name or job['input'] != input:
                        raise ValueError('请求编号已用于其他作业')
                    return job
            handler = self.handlers.get((owner, name))
            if not handler: raise ValueError('作业类型未注册')
            if recoverable and not (handler['recover'] or handler['recover_target']):
                raise ValueError('可恢复作业必须注册 recover 入口')
            job = {'id': uid(), 'owner': owner, 'name': name, 'version': handler['version'], 'input': input,
                   'request_id': request_id, 'recoverable': recoverable, 'status': 'queued', 'checkpoint': None,
                   'progress': None, 'result': None, 'error': '', 'attempt': None, 'created_at': now()}
            self.store.put('job', job['id'], owner, job)
            self.launch(job)
            return self.store.get('job', job['id'], owner)

    def launch(self, job, recovery=False):
        handler = self.handlers.get((job['owner'], job['name']))
        if not handler or handler['version'] != job['version']:
            return
        job.update(status='recovering' if recovery else 'queued', attempt=uid(), error='')
        self.store.put('job', job['id'], job['owner'], job)
        event = self.cancel_events[job['id']] = threading.Event()
        self.pool.submit(self.run, job, handler, recovery, event)

    def run(self, job, handler, recovery, event):
        owner, key, attempt = job['owner'], job['id'], job['attempt']
        context = JobContext(self, owner, key, attempt)
        self.runtime.local.job = (owner, key, attempt)
        try:
            if event.is_set(): return
            self.report(owner, key, attempt, status='recovering' if recovery else 'running')
            local = handler['recover' if recovery else 'handler']
            target = handler['recover_target' if recovery else 'target']
            if local:
                args = (context, job['input'], job['checkpoint'], {'reason': 'interrupted'}) if recovery else (context, job['input'])
                result = local(*args)
                if not event.is_set(): self.report(owner, key, attempt, status='completed', result=result)
            elif target:
                # Remote worker acknowledges immediately, reports completion with attempt fencing.
                result = self.runtime.remote(owner, target, {'job_id': key, 'attempt': attempt, 'input': job['input'],
                                              'checkpoint': job['checkpoint'], 'recovery': recovery})
                if result.get('accepted') is not True:
                    raise ValueError('远程作业入口必须快速返回 accepted=true')
            else:
                raise ValueError('恢复入口不存在')
        except BaseException as error:
            if not event.is_set():
                self.report(owner, key, attempt, status='failed', error=str(error))

        finally:
            self.runtime.local.job = None

    def report(self, owner, job_id, attempt, **updates):
        if set(updates) - {'checkpoint', 'progress', 'status', 'result', 'error'}:
            raise ValueError('未知作业状态字段')
        if 'status' in updates and updates['status'] not in ('running', 'recovering', 'completed', 'failed'):
            raise ValueError('作业状态无效')
        with self.store.lock:
            job = self.store.get('job', job_id, owner)
            if job['attempt'] != attempt or job['status'] not in ('queued', 'running', 'recovering'):
                raise ValueError('作业尝试已失效或已终结')
            job.update(updates)
            self.store.put('job', job_id, owner, job)
            self.store.emit(owner, job_id, 'job.changed', job)
            return job

    def cancel(self, owner, job_id, pause=False):
        with self.store.lock:
            job = self.store.get('job', job_id, owner)
            if job['status'] in ('completed', 'cancelled'): return job
            self.cancel_events.setdefault(job_id, threading.Event()).set()
            job.update(status='paused' if pause else 'cancelled', error='插件停用' if pause else '')
            self.store.put('job', job_id, owner, job)
            self.store.emit(owner, job_id, 'job.changed', job)
            for op in self.store.list('operation', owner):
                if op['input'].get('job_id') == job_id and op['status'] in ('pending','running'):
                    self.runtime.cancel(owner, op['id'])
            return job

    def recover_owner(self, owner):
        with self.store.lock:
            for job in self.store.list('job', owner):
                handler = self.handlers.get((owner, job['name']))
                if job['status'] in ('interrupted', 'paused') and job['recoverable'] and handler and handler['version'] == job['version'] and (handler['recover'] or handler['recover_target']):
                    self.launch(job, True)

    def retry(self, owner, job_id):
        job = self.store.get('job', job_id, owner)
        if job['status'] not in ('failed', 'interrupted', 'paused'):
            raise ValueError('只有失败或中断作业可以重试')
        handler = self.handlers.get((owner, job['name']))
        if not handler or handler['version'] != job['version']:
            raise ValueError('处理器未加载或版本不兼容')
        self.launch(job, bool(job['recoverable']))
        return self.store.get('job', job_id, owner)

    def suspend(self, owner):
        for job in self.store.list('job', owner):
            if job['status'] in ('queued', 'running', 'recovering'):
                self.cancel(owner, job['id'], True)
        for key in list(self.handlers):
            if key[0] == owner: self.handlers.pop(key, None)

    def close(self):
        for owner, _ in list(self.handlers): self.suspend(owner)
        self.pool.shutdown(wait=False, cancel_futures=True)
