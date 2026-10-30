"""Nexus Host SDK 1.1.0. Python 3.12 standard library; works outside the host."""
import contextvars
import json
import os
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SDK_VERSION = '1.1.0'
_context = contextvars.ContextVar('nexus_service_context', default=None)
_job = contextvars.ContextVar('nexus_job_context', default=None)

class Namespace:
    def __init__(self, client, path=''):
        self.client, self.path = client, path
    def __getattr__(self, name):
        return Namespace(self.client, self.path + ('.' if self.path else '') + name)
    def __call__(self, **params):
        if self.path == 'models.stream': return self.client.stream(**params)
        return self.client.call(self.path, params)

class HostClient(Namespace):
    def __init__(self, url=None, token=None):
        self.url = url or os.environ['NEXUS_HOST_URL']
        self.token = token or os.environ['NEXUS_PLUGIN_TOKEN']
        super().__init__(self)

    def request(self, method, params):
        if method == 'services.call' and _context.get(): params = {**params, 'context_id': _context.get()}
        request = Request(self.url, json.dumps({'method':method,'params':params}).encode(), headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        try:
            with urlopen(request, timeout=30) as response: return json.load(response)['result']
        except HTTPError as error:
            value = json.load(error)
            raise ValueError(value.get('error',{}).get('message','Host request failed')) from None

    def call(self, method, params=None):
        params = params or {}
        if method in ('models.generate','models.embed','dialogues.send','dialogues.continue','dialogues.continue_turn','services.call') and _job.get():
            params = {**params, 'job_id': _job.get()[0], 'job_attempt': _job.get()[1]}
        if method not in ('models.generate','models.embed','dialogues.send','dialogues.continue','dialogues.continue_turn','services.call'):
            return self.request(method,params)
        if method == 'services.call' and _context.get(): params = {**params, 'context_id': _context.get()}
        key = self.request('operations.start',{'method':method,'params':params})['operation_id']
        try:
            while True:
                op = self.request('operations.get',{'operation_id':key})
                if op['status']=='completed': return op['result']
                if op['status'] not in ('pending','running'): raise ValueError(op['error'] or op['status'])
                time.sleep(.05)
        except BaseException:
            try:self.request('operations.cancel',{'operation_id':key})
            except Exception:pass
            raise

    def stream(self, **params):
        if _job.get(): params = {**params, 'job_id': _job.get()[0], 'job_attempt': _job.get()[1]}
        key = self.request('operations.start',{'method':'models.generate','params':params})['operation_id']
        cursor=0
        try:
            while True:
                page=self.request('operations.events',{'operation_id':key,'after':cursor});cursor=page['cursor']
                for event in page['events']:
                    if event['type']=='model.event':yield event['data']
                    if event['type']=='operation.done':
                        op=self.request('operations.get',{'operation_id':key})
                        if op['status']!='completed':raise ValueError(op['error'])
                        yield {'type':'complete','result':op['result']};return
                time.sleep(.05)
        finally:
            try:self.request('operations.cancel',{'operation_id':key})
            except Exception:pass


def serve(tools, handlers, initialize=None):
    """Small stdio MCP adapter, including caller context propagation for service cycles."""
    import sys
    from concurrent.futures import ThreadPoolExecutor
    lock=threading.Lock()
    def respond(message):
        if 'id' not in message:return
        try:
            method=message['method'];params=message.get('params') or {}
            if method=='initialize':
                if initialize:initialize()
                result={'protocolVersion':params.get('protocolVersion','2025-06-18'),'serverInfo':{'name':'nexus-sdk-python','version':SDK_VERSION},'capabilities':{'tools':{}}}
            elif method=='tools/list':result={'tools':tools}
            elif method=='ping':result={}
            elif method=='tools/call':
                args=params.get('arguments') or {};token=_context.set(args.pop('_host_context',None))
                job_token=_job.set((args['job_id'],args['attempt']) if 'job_id' in args and 'attempt' in args else None)
                try:value=handlers[params['name']](args)
                finally:
                    _context.reset(token)
                    _job.reset(job_token)
                result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'isError':False}
            else:raise ValueError('Unsupported method')
            reply={'jsonrpc':'2.0','id':message['id'],'result':result}
        except Exception as error:
            reply={'jsonrpc':'2.0','id':message['id'],'result':{'content':[{'type':'text','text':str(error)}],'isError':True}}
        with lock:print(json.dumps(reply,ensure_ascii=False),flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for line in sys.stdin:
            try:pool.submit(respond,json.loads(line))
            except ValueError:pass
