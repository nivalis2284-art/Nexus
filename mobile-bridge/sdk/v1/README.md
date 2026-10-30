# Nexus Host SDK 1.1.0

插件按需借用宿主能力。业务数据、对话、作业和请求归属始终与主交流分开。Python 插件可直接使用 `PluginHost`；独立 Python/Node 服务使用这里的无第三方依赖客户端。

页面内容和操作也可交给宿主统一渲染，见 [页面协议 v1](UI.md)。旧 HTML 页面继续支持。

## 宿主要求与清单

要求包含 Host SDK v1 的 Nexus。旧 `api_version: 1` 与 SDK 0.2.0 聊天接口继续支持。新增能力依赖通过 `requires_host` 声明：

```json
{
  "id": "example",
  "name": "Example",
  "version": "1.0.0",
  "api_version": 1,
  "requires_host": {"models": 1, "dialogues": 1, "jobs": 1},
  "services": [{"name":"worker","command":"node","args":["{plugin}/worker.mjs"],"model_tools":[]}],
  "call_handler": {"service":"worker","tool":"page"}
}
```

无 Python 入口的插件仍可加载独立服务和页面；`call_handler` 把页面操作转发给指定 MCP 工具。已有 `context`、`event_handler` 清单入口继续支持。`model_tools: []` 防止内部管理工具自动暴露给主模型。

宿主注入 `NEXUS_HOST_URL` 和 `NEXUS_PLUGIN_TOKEN` 到声明新能力的独立服务。插件不自行填写或持久化它们。Node.js 示例要求已安装 Node.js 22+；宿主不代装语言运行时。Python 入口仍在宿主解释器中运行，插件不是进程沙箱。

## 使用

宿主内入口：

```python
class Plugin:
    def __init__(self, host): self.host = host
    def start(self):
        self.host.capabilities.require(requires={"models": 1})
    def call(self, action, values):
        return self.host.models.list()
```

独立 Python：

```python
from nexus_host import HostClient
host = HostClient()
models = host.models.list()
result = host.models.generate(
    provider_id="provider-id", model_id="model-id",
    system="只简短回答。", messages=[{"role":"user","content":"你好"}],
    purpose="example.reply", request_id="unique-request")
```

Node.js：

```javascript
import {HostClient} from './nexus-host.mjs';
const host = new HostClient();
const models = await host.models.list();
for await (const event of host.models.stream({
  provider_id: 'provider-id', model_id: 'model-id',
  messages: [{role:'user', content:'你好'}]
})) console.error(event); // MCP stdio 的 stdout 只用于协议，不用于日志
```

Python 使用关键字参数；JS 使用参数对象。以下方法对应同名语言无关协议。

## 接口契约

### capabilities

- `describe()` → `{sdk_version, capabilities}`。
- `require(requires={能力:版本})` → `{satisfied:true}`，不支持时加载失败或抛错。

能力组：models、dialogues、history、jobs、storage、services、context、events、hooks、chat、mcp，均为 v1。描述字段仅表示接口存在，不表示已配置可用的云端模型。

### models

- `list()` → 模型数组，区分 `kind=generation/embedding`。生成模型用 `provider_id + model_id`；嵌入模型使用宿主嵌入配置的 `id`。
- `generate(provider_id, model_id, messages, system='', tools=[], effort=None, purpose='generate', request_id=None, continuation_id=None)` → `{text, tool_calls, usage, finish_reason, continuation_id}`。
- `stream(...)` → text/tool/usage 等规范事件，最后 `{type:'complete', result:生成结果}`。关闭 Python 生成器或退出 JS 迭代会请求取消。
- `embed(model_id, input=[文字], purpose='embed', request_id=None)` → `{model_id,model,dimensions,vectors,usage}`。
- `cancel(operation_id)` → 请求取消；外部网络流由宿主关闭。

生成支持已有三协议，嵌入首轮支持 OpenAI 兼容接口。提供商配置、凭据由宿主管理，插件不能通过这些接口修改或读取模型密钥。未配置的模型可以出现在目录中，`configured=false`；真正调用时明确报错。

消息采用现有通用形式：user/assistant 的 content，assistant 的 `tool_calls=[{id,name,arguments:JSON字符串}]`，tool 的 `tool_call_id/name/content`。工具声明为 `{name,description,parameters:JSON Schema}`。模型只提出调用，由插件自行执行；不继承主交流提示、工具、历史或记忆。

若返回 continuation_id，将它随相应助手消息和工具结果一起提交下一次请求。宿主保存必要原生续接，仅允许原插件、相同提供商/模型/协议和匹配的助手消息使用；不向插件返回隐藏推理原文。

### dialogues / context

- `create(persona, provider_id, model_id, title='', inherit=[], context_limit=None)` → 新对话。
- `list()` / `get(dialogue_id)` → 所属插件的对话状态。
- `history(dialogue_id)` → 完整原始消息与当前检查点。
- `send(dialogue_id,request_id,text,tools=[])` → 一次生成结果。
- `continue_turn(dialogue_id,request_id,results=[{tool_call_id,content}])` → 提交全部待执行工具结果后一次生成；wire 也接受 `dialogues.continue`，Python 使用 continue_turn。
- `cancel(dialogue_id)` / `clear(dialogue_id)` / `delete(dialogue_id)`。
- `context.export(sections=['owner_instructions','main_role'])` → 显式选择的完整文本快照。

默认只有插件给定人设。inherit 可选择上述两种文本来源，不自动提取人设或持续同步。对话不会进入主交流，不使用 Agent 工作队列，也不自动执行工具。

对话串行；生成中或等待工具结果时不接收另一个普通输入。请求 ID 相同且参数相同返回已有成功结果；中断请求不自动重放。工具中断结果未知时，需要插件显式清理或处理，不伪造工具成功。

达到容量时使用抽出的共享摘要逻辑，保留完整原始历史。压缩失败或单条交换无法容纳时明确报错，不丢弃旧内容。

### history

- `snapshot(limit=100,cursor=None)` → `{session_id,version,records,next,change_cursor}`。
- `get(record_id)` → 当前原始记录。
- `changes(after=0,limit=100)` / `subscribe(after=0,limit=100)` → `{events,cursor}`，客户端按游标轮询。

记录包含 `_record_id` 和 `_record_version`，不依赖列表位置。首次读取旧文件备份到 sessions/.pre-plugin-sdk-v1 后原子增加元数据，不改正文、顺序、原有时间或检查点索引。缺失来源时间保持缺失，不补成当前时间。

变化事件为 saved、updated、deleted、restored、compacted；携带记录身份和版本，压缩事件另带覆盖边界身份。JSONL 仍为权威记录，启动及读取时协调变化登记。分页期间文件发生变化，返回 `history_changed`，应重新取快照。变化日志不保存每次修改的完整旧版本正文。

读取排除 thinking/native，提供原文及附件引用。此服务不允许插件修改或删除主交流。

### jobs

本地 Python：

```python
def run(ctx, value):
    ctx.checkpoint({'step':1})
    ctx.progress({'done':1})
    if ctx.cancelled: return
    return {'result':'ok'}

def recover(ctx, value, checkpoint, interruption):
    return {'resumed_from': checkpoint}

host.jobs.register(name='example',version=1,handler=run,recover=recover)
host.jobs.submit(name='example',input={},request_id='unique-job',recoverable=True)
```

远程注册：

```javascript
await host.jobs.register({name:'example',version:1,
 target:{service:'worker',tool:'job_start'},
 recover_target:{service:'worker',tool:'job_recover'}});
```

远程处理器收到 `{job_id,attempt,input,checkpoint,recovery}`，快速返回 `{accepted:true}`，随后后台调用：

```javascript
await host.jobs.report({job_id,attempt,checkpoint:{step:1},progress:{done:1}});
await host.jobs.report({job_id,attempt,status:'completed',result:{ok:true}});
```

使用 SDK `serve` 适配器时，Node 异步处理自动保留作业归属，模型调用携带 job_id/job_attempt。本地 Python 处理器也自动关联；Python 新建线程需要显式传递上下文（例如 copy_context().run）或明确填写归属。

管理方法：`get(job_id)`、`list()`、`cancel(job_id)`、`retry(job_id)`。四个作业执行位，与 Agent 工作执行位分开。进度、检查点、结果必须可 JSON 序列化。

声明可恢复且注册兼容 recover 入口的作业，在插件重载或宿主重启后自动调用 recover；它决定继续、重做或结束。不会恢复线程调用栈。普通中断作业需要显式重试；显式取消不恢复；版本不匹配等待处理。每次 attempt 不同，旧尝试不能更新新结果或发起新的作业模型操作。

取消是合作式的；处理器应检查取消状态并有限时间返回。停用会取消其模型操作并暂停作业，重载后按声明恢复。恢复失败不无限循环重试。

### services

- `register(name,version=1,handler=本地处理器,target=MCP入口,schema={})`。
- `list()` → 服务目录及声明契约。
- `call(name='提供插件.服务名',values={},version=1)` → 返回结果。

本地 handler 接收 `(values, {'caller_plugin_id':...})`；远程 handler 接收 `{values,caller_plugin_id}`。schema 是公开声明，业务校验由服务处理器负责。名称自动加提供插件前缀，不能覆盖其他插件的服务。

SDK serve 自动传递服务调用链以检测循环。不要手写转发时丢掉 `_host_context`。插件停用撤销服务，新调用报不可用；在途调用收尾。远程服务受现有 MCP 时限约束，本地服务应有限时间返回，长任务使用 jobs。

### storage / events / hooks / chat / mcp

- `storage.data_dir()`：插件自己的数据路径。
- `storage.settings.get()` / `save(values={})`：公共设置。
- `storage.secrets.get(name)` / `set(name,value)` / `delete(name)`：独立凭据，不进入普通设置快照。
- `storage.attachments.read(attachment_id,offset=0,limit=10000,original=False)`：主交流附件元数据、文字片段，可显式取回原文件 base64，不开放任意路径读取。
- `events.subscribe(types=[...])`：旧持久事件订阅；`events.read(after=0)`：SDK 运行事件的游标读取。
- `hooks.define(name,description)`、`subscribe(name,handler=...或target=...,key='default')`、`unsubscribe(name,key)`、`emit(name,payload)`：同步通知，沿用已有钩子语义。
- `chat.snapshot()` / `send(text,request_id)`：现有主交流；`chat.events(after=修订号)`：内存实时流。reset_required=true 时重新获取快照，流不持久重放。
- `mcp.connect(name,config)` / `call(service,tool,arguments={})`：本插件的 MCP 服务。

## 语言无关桥接

POST 注入地址，Authorization: Bearer 注入凭据，JSON 为：

```json
{"method":"capabilities.describe","params":{}}
```

成功 `{result:...}`；错误 HTTP 400、`{error:{code,message}}`，无效/已撤销凭据为 HTTP 401。凭据决定插件归属，不接受请求自行指定 owner。长操作由 SDK 调用 `operations.start({method,params})` 得到 operation_id，再读取 `operations.get/events` 或取消。独立语言实现应使用相同流程，避免同步 HTTP 的超时限制。

操作事件按 seq 分页。开始、结果和错误会持久记录；主交流实时流只在内存保留最近1000个事件。插件加载期间只允许能力查询、配置及处理器注册，运行请求需等待 ready。

## 管理与兼容

宿主插件详情提供能力依赖、服务、作业和独立对话状态；可取消、重试和查看历史，不增加第二套用户聊天入口。设置页独立管理嵌入模型；请求记录可按插件、对话、作业、用途筛选。

旧插件接口保留；旧包无须改写。SDK 示例不迁移记忆包、不接入QQ，不提供通用 Agent 执行或任意宿主工具授权。没有安装 Node.js 时，Node 示例加载会明确失败，不自动安装运行时。
