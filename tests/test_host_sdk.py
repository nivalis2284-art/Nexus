"""Host SDK checks use temporary data, local model streams and isolated plugins."""
import io
import json
import shutil
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core.config import ConfigStore
from nexus.mcp import MCPRegistry
from nexus.model.request_history import RequestHistory
from nexus.plugins.api import PluginHost
from nexus.plugins.manager import Entry, PluginManager
from nexus.session import SessionStore, Message
from nexus.plugins.pages import declarations, validate_view

ROOT = Path(__file__).resolve().parents[1]


def test_host_page_protocol_rejects_unsupported_content():
    declarations([{'path': 'index.html'}, {'id': 'new', 'renderer': 'host', 'ui_version': 1, 'action': 'ui'}])
    with pytest.raises(ValueError, match='版本'):
        declarations([{'id': 'new', 'renderer': 'host', 'ui_version': 2, 'action': 'ui'}])
    with pytest.raises(ValueError, match='重复'):
        declarations([{'id': 'new', 'renderer': 'host', 'ui_version': 1, 'action': 'ui'},
                      {'id': 'new', 'renderer': 'host', 'ui_version': 1, 'action': 'ui'}])
    for component in (
        {'id': 'x', 'type': 'html', 'text': '<b>raw</b>'},
        {'id': 'x', 'type': 'text', 'text': 'plain', 'style': 'color:red'},
        {'id': 'x', 'type': 'field', 'name': 'x', 'label': 'x', 'control': 'unsupported'},
    ):
        with pytest.raises(ValueError, match='页面格式错误'):
            validate_view({'ui_version': 1, 'components': [component]})


@pytest.mark.parametrize('language', ['python', 'node'])
def test_host_rendered_example_pages(manager, provider, language):
    if language == 'node' and not shutil.which('node'): pytest.skip('Node.js unavailable')
    configure(manager, provider[0])
    plugin_id = 'host-sdk-' + language
    source = ROOT / 'mobile-bridge/examples' / plugin_id
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        for path in source.iterdir():
            if path.is_file(): archive.write(path, path.name)
    manager.import_zip(content.getvalue()); manager.load(plugin_id)
    eventually(lambda: manager.entries[plugin_id].status != 'loading')
    assert manager.entries[plugin_id].status == 'ready', manager.entries[plugin_id].error
    view = manager.page(plugin_id, 'main', {'event': 'open'})
    assert view['ui_version'] == 1 and not provider[1]
    values = {'model': view['context']['model'], 'message': 'page draft'}
    def act(action, extra=None):
        nonlocal view
        view = manager.page(plugin_id, 'main', {'event': 'action', 'action': action,
            'request_id': action + '-' + str(time.monotonic()), 'values': {**values, **(extra or {})}, 'context': view['context']})
        return view
    assert act('edit-note')['dialog']['id'] == 'note-editor'
    assert act('save-note', {'note': ''})['errors']['note']
    assert act('save-note', {'note': 'corrected'})['notice']['tone'] == 'success'
    assert manager.entries[plugin_id].settings['example_note'] == 'corrected'
    assert 'dialog' not in view
    assert act('echo')['context']['result']['echo']['request_id']
    assert act('dialogue')['context']['dialogue_id']
    assert act('send')['context']['result']['text'] == 'independent reply'
    assert act('stream')['context']['result'][-1]['type'] == 'complete'
    assert all('MAIN SECRET HISTORY' not in json.dumps(body) for _, body in provider[1])
    manager.operate(plugin_id, 'unload')
    with pytest.raises(ValueError): manager.page(plugin_id, 'main', {'event': 'open'})


def eventually(test, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value: return value
        time.sleep(.02)
    raise AssertionError('condition timed out')


@pytest.fixture
def provider():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, body))
            if self.path.endswith('embeddings'):
                data=json.dumps({'data':[{'index':i,'embedding':[1.,0.,.5]} for i,_ in enumerate(body['input'])]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            def send(value,event=None):
                for choice in value.get('choices', []): choice.setdefault('index', 0)
                self.wfile.write(((('event: '+event+'\n') if event else '')+'data: '+json.dumps(value)+'\n\n').encode());self.wfile.flush()
            family = 'anthropic' if self.path.endswith('messages') else 'responses' if self.path.endswith('responses') else 'chat'
            if body.get('tools') and 'TOOL' in json.dumps(body) and 'tool_call_id' not in json.dumps(body) and 'tool_result' not in json.dumps(body) and 'function_call_output' not in json.dumps(body):
                if family == 'chat':
                    send({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call1','function':{'name':'echo','arguments':'{"text":"hello"}'}}]},'finish_reason':None}]})
                    send({'choices':[{'delta':{},'finish_reason':'tool_calls'}]})
                elif family == 'anthropic':
                    send({'type':'message_start','message':{'id':'m','type':'message','role':'assistant','content':[],'model':'test','usage':{'input_tokens':1,'output_tokens':0}}},'message_start')
                    send({'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':'call1','name':'echo','input':{}}},'content_block_start')
                    send({'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'{"text":"hello"}'}},'content_block_delta')
                    send({'type':'content_block_stop','index':0},'content_block_stop')
                    send({'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':1}},'message_delta')
                    send({'type':'message_stop'},'message_stop')
                else:
                    send({'type':'response.output_item.added','output_index':0,'item':{'type':'function_call','id':'f1','call_id':'call1','name':'echo','arguments':''}})
                    send({'type':'response.function_call_arguments.delta','output_index':0,'item_id':'f1','delta':'{"text":"hello"}'})
                    send({'type':'response.output_item.done','output_index':0,'item':{'type':'function_call','id':'f1','call_id':'call1','name':'echo','arguments':'{"text":"hello"}','status':'completed'}})
                    send({'type':'response.completed','response':{'id':'resp1','status':'completed','output':[{'type':'function_call','id':'f1','call_id':'call1','name':'echo','arguments':'{"text":"hello"}','status':'completed'}]}})
            elif family=='chat':
                send({'choices':[{'delta':{'content':'independent reply'},'finish_reason':None}]})
                send({'choices':[{'delta':{},'finish_reason':'stop'}]})
            elif family=='anthropic':
                send({'type':'message_start','message':{'id':'m','type':'message','role':'assistant','content':[],'model':'test','usage':{'input_tokens':1,'output_tokens':0}}},'message_start')
                send({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},'content_block_start')
                send({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'independent reply'}},'content_block_delta')
                send({'type':'content_block_stop','index':0},'content_block_stop')
                send({'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':1}},'message_delta');send({'type':'message_stop'},'message_stop')
            else:
                item={'id':'msg1','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'independent reply','annotations':[]}]}
                send({'type':'response.output_item.added','output_index':0,'item':{**item,'content':[]}})
                send({'type':'response.output_text.delta','output_index':0,'content_index':0,'item_id':'msg1','delta':'independent reply'})
                send({'type':'response.output_item.done','output_index':0,'item':item})
                send({'type':'response.completed','response':{'id':'resp1','status':'completed','output':[item]}})
            if family == 'chat':
                self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{http.server_port}/v1', requests
    http.shutdown();http.server_close();thread.join(2)


@pytest.fixture
def manager(tmp_path):
    config=ConfigStore(tmp_path/'home');config.ensure()
    sessions=SessionStore(config.sessions_dir);session=sessions.create('test');sessions.append(session,Message('user','MAIN SECRET HISTORY'))
    state={'store':config,'sessions':sessions,'ling':SimpleNamespace(session_id=session.id),'mcp':MCPRegistry(),'request_history':RequestHistory(config.home/'requests.sqlite3')}
    manager=PluginManager(state,root=tmp_path/'plugins')
    for key in ('test','other'):
        path=tmp_path/key;path.mkdir()
        manager.entries[key]=Entry(key,path,{'id':key},enabled=True,status='ready')
    yield manager
    manager.close();state['mcp'].close()


def configure(manager, url, family='openai_chat_completions'):
    config=manager.state['store']
    p=config.add_provider({'name':family,'family':family,'base_url':url,'models':[{'id':'test','max_output_tokens':64,'context_window_tokens':4096}],'api_key':'test-key'})
    return {'provider_id':p.id,'model_id':'test'}


@pytest.mark.parametrize('family',['openai_chat_completions','anthropic_messages','openai_responses'])
def test_model_stream_tool_continuation_and_isolation(manager,provider,family):
    select=configure(manager,provider[0],family);h=PluginHost(manager,'test')
    tools=[{'name':'echo','description':'Echo text','parameters':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]
    messages=[{'role':'user','content':'TOOL'}]
    result=h.models.generate(**select,system='plugin persona',messages=messages,tools=tools)
    assert result['tool_calls'][0]['id']=='call1'
    messages += [{'role':'assistant','content':result['text'],'tool_calls':result['tool_calls']},{'role':'tool','tool_call_id':'call1','name':'echo','content':'hello'}]
    reply=h.models.generate(**select,messages=messages,continuation_id=result['continuation_id'],tools=tools)
    assert reply['text']=='independent reply'
    events=list(h.models.stream(**select,messages=[{'role':'user','content':'hi'}]))
    assert events[-1]['type']=='complete'
    assert 'MAIN SECRET HISTORY' not in json.dumps(provider[1])
    assert len(manager.state['sessions'].load(manager.state['ling'].session_id).items)==1


def test_embedding_catalog_and_private_credentials(manager,provider):
    rt=manager.runtime
    item=rt.models.save_embedding({'name':'embed','base_url':provider[0],'model_id':'e','api_key':'embedding-secret'})[0]
    h=PluginHost(manager,'test')
    assert 'embedding-secret' not in json.dumps(h.models.list())
    result=h.models.embed(model_id=item['id'],input=['one','two'])
    assert result['dimensions']==3 and len(result['vectors'])==2
    h.storage.secrets.set(name='private',value='mine')
    assert h.storage.secrets.get(name='private')=='mine'
    assert PluginHost(manager,'other').storage.secrets.get(name='private') is None
    assert 'mine' not in json.dumps(manager.snapshot())


def test_history_migration_paging_delete_and_restore(manager):
    h=PluginHost(manager,'test');page=h.history.snapshot(limit=1);record=page['records'][0]
    key=record['_record_id'];session_id=page['session_id']
    assert (manager.home/'sessions'/'.pre-plugin-sdk-v1'/f'{session_id}.jsonl').exists()
    sessions=manager.state['sessions'];session=sessions.load(session_id)
    session.items[0].text='corrected';sessions.rewrite(session)
    changed=h.history.get(record_id=key)
    assert changed['_record_id']==key and changed['_record_version']!=record['_record_version']
    old=session.items.pop();sessions.rewrite(session)
    assert h.history.changes(after=page['change_cursor'])['events'][-1]['type']=='history.deleted'
    session.items.append(old);sessions.rewrite(session)
    assert h.history.changes(after=page['change_cursor'])['events'][-1]['type']=='history.restored'
    sessions.append(session,Message('user','second'))
    partial=h.history.snapshot(limit=1)
    sessions.append(session,Message('user','third'))
    with pytest.raises(ValueError,match='history_changed'):h.history.snapshot(cursor=partial['next'])
    assert h.history.get(record_id=key)['text']=='corrected'
    session=sessions.load(session_id)
    session.items[0].at=''
    sessions.rewrite(session)
    assert h.history.get(record_id=key)['at']==''
    assert h.history.get(record_id=key)['_record_version']==h.history.get(record_id=key)['_record_version']


def test_dialogue_tools_idempotency_and_history_independence(manager,provider):
    h=PluginHost(manager,'test');select=configure(manager,provider[0])
    d=h.dialogues.create(**select,persona='only plugin',title='Independent')
    tools=[{'name':'echo','description':'echo','parameters':{'type':'object','properties':{}}}]
    result=h.dialogues.send(dialogue_id=d['id'],request_id='turn1',text='TOOL',tools=tools)
    assert h.dialogues.get(dialogue_id=d['id'])['status']=='awaiting_tools'
    with pytest.raises(ValueError):h.dialogues.send(dialogue_id=d['id'],request_id='other',text='new')
    with pytest.raises(ValueError):h.dialogues.continue_turn(dialogue_id=d['id'],request_id='toolbad',results=[])
    result=h.dialogues.continue_turn(dialogue_id=d['id'],request_id='tool1',results=[{'tool_call_id':'call1','content':'done'}])
    assert result['text']=='independent reply'
    assert h.dialogues.continue_turn(dialogue_id=d['id'],request_id='tool1',results=[{'tool_call_id':'call1','content':'done'}])==result
    with pytest.raises(ValueError):PluginHost(manager,'other').dialogues.get(dialogue_id=d['id'])
    assert len(h.dialogues.history(dialogue_id=d['id'])['messages'])==4
    assert len(manager.state['sessions'].load(manager.state['ling'].session_id).items)==1


def test_dialogue_compacts_without_dropping_original_history(manager,provider):
    h=PluginHost(manager,'test');select=configure(manager,provider[0])
    d=h.dialogues.create(**select,persona='short',context_limit=1800)
    rt=manager.runtime
    doc=rt.store.get('dialogue',d['id'],'test')
    doc['messages']=[{'role':'user' if i%2==0 else 'assistant','content':('older context '+str(i)+' ')*40,'_source_id':str(i)} for i in range(12)]
    rt.store.put('dialogue',d['id'],'test',doc)
    result=h.dialogues.send(dialogue_id=d['id'],request_id='compact',text='new')
    history=h.dialogues.history(dialogue_id=d['id'])
    assert result['text'] and history['checkpoint']
    assert len(history['messages'])==14


def test_jobs_recovery_attempt_fencing_and_cancel(manager):
    h=PluginHost(manager,'test');rt=manager.runtime
    entered=threading.Event();release=threading.Event()
    def run(ctx,value):
        ctx.checkpoint({'i':2});entered.set();release.wait(3)
    def recover(ctx,value,checkpoint,interruption):
        assert checkpoint=={'i':2};return {'recovered':True}
    h.jobs.register(name='work',handler=run,recover=recover)
    job=h.jobs.submit(name='work',input={},request_id='job1',recoverable=True)
    assert entered.wait(2)
    old=rt.store.get('job',job['id'],'test')['attempt']
    rt.jobs.suspend('test');release.set()
    h.jobs.register(name='work',handler=run,recover=recover)
    eventually(lambda:rt.store.get('job',job['id'],'test')['status']=='completed')
    with pytest.raises(ValueError,match='失效'):rt.jobs.report('test',job['id'],old,status='completed',result='stale')
    assert rt.store.get('job',job['id'],'test')['result']=={'recovered':True}
    cancelled=h.jobs.submit(name='work',input={},request_id='job2',recoverable=True)
    h.jobs.cancel(job_id=cancelled['id']);rt.jobs.recover_owner('test')
    assert h.jobs.get(job_id=cancelled['id'])['status']=='cancelled'


def test_service_cycle_detection_and_bridge_owner_binding(manager):
    h=PluginHost(manager,'test');other=PluginHost(manager,'other')
    h.services.register(name='echo',handler=lambda value,caller:{'value':value,'caller':caller})
    assert other.services.call(name='test.echo',values={'x':1})['caller']['caller_plugin_id']=='other'
    h.services.register(name='cycle',handler=lambda value,caller:h.services.call(name='test.cycle'))
    # Nested direct calls must not dispatch another operation and lose the chain.
    with pytest.raises(ValueError,match='循环'):h.services.call(name='test.cycle')
    env=manager.runtime.bridge.environment('other')
    import sys
    sys.path.insert(0,str(ROOT/'mobile-bridge/sdk/v1'))
    from nexus_host import HostClient
    client=HostClient(env['NEXUS_HOST_URL'],env['NEXUS_PLUGIN_TOKEN'])
    d=manager.runtime.store.put('dialogue','private','test',{'id':'private'})
    with pytest.raises(ValueError):client.dialogues.get(dialogue_id='private')
    assert client.capabilities.describe()['capabilities']['models']==1


@pytest.mark.skipif(not shutil.which('node'),reason='Node.js unavailable')
def test_pure_node_plugin_load_calls_services_streams_and_recovers_job(manager,provider):
    select=configure(manager,provider[0])
    path=ROOT/'mobile-bridge/examples/host-sdk-node'
    bundle=io.BytesIO()
    with zipfile.ZipFile(bundle,'w') as archive:
        for source in path.iterdir():
            if source.is_file():archive.writestr(source.name,source.read_bytes())
    manager.import_zip(bundle.getvalue());manager.load('host-sdk-node')
    item=eventually(lambda:manager.entries['host-sdk-node'] if manager.entries['host-sdk-node'].status not in ('loading','unloaded') else None)
    assert item.status=='ready',item.error
    result=manager.call(item.id,'echo',{'message':'node'})
    assert result['echo']=={'message':'node'}
    events=manager.call(item.id,'stream',{**select,'messages':[{'role':'user','content':'hi'}]})
    assert events[-1]['type']=='complete'
    job=manager.call(item.id,'job',{'request_id':'node-job'})
    eventually(lambda:manager.runtime.store.get('job',job['id'],item.id)['checkpoint'])
    manager.operate(item.id,'unload')
    eventually(lambda:item.status=='unloaded')
    manager.load(item.id);eventually(lambda:item.status=='ready')
    final=eventually(lambda:(v if v['status']=='completed' else None) if (v:=manager.runtime.store.get('job',job['id'],item.id)) else None)
    assert final['result']['recovered'] is True


def test_restart_dispatches_registered_recover_not_original_handler(manager):
    rt=manager.runtime
    job={'id':'crash-job','owner':'test','name':'checkpointed','version':1,'input':{'x':1},'request_id':'crash',
         'recoverable':True,'status':'running','checkpoint':{'step':3},'progress':None,'result':None,'error':'','attempt':'old','created_at':'now'}
    rt.close()
    rt.store.put('job',job['id'],'test',job)
    manager._host_runtime=None
    h=PluginHost(manager,'test')
    assert h.jobs.get(job_id=job['id'])['status']=='interrupted'
    def forbidden(*args):raise AssertionError('must not rerun original handler')
    h.jobs.register(name='checkpointed',handler=forbidden,recover=lambda ctx,value,checkpoint,interruption:{'step':checkpoint['step'],'input':value})
    result=eventually(lambda:(r if r['status']=='completed' else None) if (r:=h.jobs.get(job_id=job['id'])) else None)
    assert result['result']=={'step':3,'input':{'x':1}}


def test_unload_cancels_model_operation_and_revokes_bridge(manager,provider):
    select=configure(manager,provider[0]);rt=manager.runtime
    # The queue is fenced on unload, whether generation has begun or not.
    operation=rt.start('test','models.generate',{**select,'messages':[{'role':'user','content':'hi'}]})['operation_id']
    old_token=rt.bridge.environment('test')['NEXUS_PLUGIN_TOKEN']
    manager.operate('test','unload')
    eventually(lambda:manager.entries['test'].status=='unloaded')
    assert old_token not in rt.bridge.tokens.values()
    assert rt.store.get('operation',operation,'test')['status'] in ('completed','cancelled','failed')



def test_job_child_operations_are_cancelled_and_stale_attempts_rejected(manager):
    h=PluginHost(manager,'test');rt=manager.runtime
    entered, release = threading.Event(), threading.Event()
    def run(ctx, value):
        rt.local.job = ('test', ctx.job_id, ctx.attempt)
        # A nested service call is cancellable through the job, not the Agent task system.
        return h.services.call(name='test.block')
    def block(value, caller):
        entered.set();release.wait(3);return {'done':True}
    h.services.register(name='block',handler=block)
    h.jobs.register(name='child',handler=run)
    job=h.jobs.submit(name='child',input={},request_id='child')
    assert entered.wait(2)
    current=h.jobs.get(job_id=job['id'])
    h.jobs.cancel(job_id=job['id'])
    release.set()
    op=eventually(lambda:next((o for o in rt.store.list('operation','test') if o['input'].get('job_id')==job['id'] and o['status']=='cancelled'),None))
    with pytest.raises(ValueError,match='失效'):
        rt.start('test','models.generate',{'job_id':job['id'],'job_attempt':current['attempt']})
