"""Direct SDK example; no model is called until the owner explicitly requests one."""
import time
from .page import render_page

class Example:
    def __init__(self, host): self.host = host
    def start(self):
        self.host.capabilities.require(requires={'models':1,'history':1,'jobs':1})
        self.host.jobs.register(name='counter', version=1, handler=self.run, recover=self.recover)
        self.host.services.register(name='echo', handler=lambda values, caller: {'echo':values,'caller':caller})
    def run(self, context, values): return self.count(context, values, 0)
    def recover(self, context, values, checkpoint, interruption): return self.count(context, values, (checkpoint or {}).get('i',0))
    def count(self, context, values, start):
        for i in range(start,values['count']):
            if context.cancelled: return None
            context.checkpoint({'i':i+1});context.progress({'done':i+1,'total':values['count']});time.sleep(.1)
        return {'count':values['count']}
    def call(self, action, values):
        h=self.host
        if action=='ui':return render_page(h,self.call,values)
        if action=='models':return h.models.list()
        if action=='history':return h.history.snapshot(limit=5)
        if action=='generate':return h.models.generate(**values)
        if action=='stream':return list(h.models.stream(**values))
        if action=='echo':return h.services.call(name='host-sdk-python.echo', values=values)
        if action=='dialogue':return h.dialogues.create(**values, persona='只简短回答，不执行自主任务。',title='Python 示例独立对话')
        if action=='send':return h.dialogues.send(**values)
        if action=='continue':return h.dialogues.continue_turn(**values)
        if action=='job':return h.jobs.submit(name='counter',input={'count':20},request_id=values['request_id'],recoverable=True)
        return {'capabilities':h.capabilities.describe(),'jobs':h.jobs.list(),'dialogues':h.dialogues.list()}

def create_plugin(host):return Example(host)
