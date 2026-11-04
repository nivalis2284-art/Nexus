# Nexus 插件 SDK

这是插件开发规范和客户端的统一来源。当前 SDK 为 **1.2.0**，新增公共 `work` v1。`api_version` 仍为 1，各能力分别按 `requires_host` 声明版本。SDK 可单独复制，插件可在任意目录开发并在自己的目录生成 ZIP。

## 目录与接入方式

Python 插件直接使用宿主传入的 `host`，无需复制 SDK。独立 Python/Node 服务使用本目录的 [nexus_host.py](nexus_host.py) 或 [nexus-host.mjs](nexus-host.mjs)；它们是同一 SDK 的两种语言实现。各插件源码不维护 SDK 副本，[build.py](build.py) 在打包时按源码引用加入共享客户端。Python 要求 3.12，Node 要求 22+，客户端无需第三方依赖。

## 最小插件

在插件目录的 `plugin.json` 填写：

```json
{"id":"hello","name":"Hello","version":"0.1.0","api_version":1,"entry":"backend.py:create_plugin","requires_host":{"models":1}}
```

同目录创建 `backend.py`：

```python
class Plugin:
    def __init__(self, host):
        self.host = host

    def call(self, action, values):
        if action == 'models':
            return self.host.models.list()
        raise ValueError('未知操作：' + action)


def create_plugin(host):
    return Plugin(host)
```

宿主调用工厂，然后调用可选 `start()`；停用时调用可选 `close()`。在 `close()` 释放线程、连接和订阅。长驻循环使用可停止线程；`host.background()` 用于有限工作。Python 入口在宿主解释器执行。工具声明从公开入口 `nexus.plugins.api` 导入 `ToolSpec`、`BaseModel`、`Field`、`EmptyArgs`，通过插件实例的 `tools()` 返回工具声明。

## 提供 Agent 工具和 Skill

插件可以提供工具与使用这些工具的 Skill，无需修改宿主源码。主交流与后台 Agent 在每次准备模型请求时加载已启用插件的工具和技能；停用后，后续请求不再加载它们，已写入对话的工具结果和技能正文仍保留在历史中。

最小工具示例：

```python
from nexus.plugins.api import EmptyArgs, ToolSpec


class Plugin:
    def __init__(self, host):
        self.host = host

    def tools(self, scope):
        return [ToolSpec("example_status", "Read the example plugin's status.",
                         EmptyArgs, self.status, read_only=True)]

    def status(self, context, arguments):
        return {"content": "Example plugin is ready."}


def create_plugin(host):
    return Plugin(host)
```

使用带插件前缀的工具名，避免重名。与现有工具重名时加载请求会报错；确需替换时在清单中显式声明 `"overrides": ["工具名"]`。`read_only` 应按实际行为填写；工具仍使用宿主现有的工具执行和权限流程。

技能放在插件根目录的 `skills/<技能名>/SKILL.md`，也支持 `skills/<技能名>.md`。例如 `skills/example-workflow/SKILL.md`：

```markdown
---
name: example-workflow
description: 查看示例插件状态并根据实际结果答复。
when: 用户询问示例插件是否就绪时
---
调用 example_status，依据工具返回的真实状态回答，不要猜测。
```

`name` 使用小写字母、数字和连字符，`description` 必填。模型请求只加入名称、描述与可选的 `when`；正文通过 `read_skill(name="example-workflow")` 按需读取。技能名与已有技能冲突时，工作区、用户和宿主基础技能优先；插件之间按加载目录的既有顺序先到者优先。建议使用插件名作为技能名前缀。

纯 Skill 插件只需 `plugin.json`、`README.md` 和 `skills/`，无需声明 `entry`。`skills/` 中的参考资料和脚本会随包保留，但不会自动执行或注入模型上下文；运行脚本仍需适用的工具和权限。开发临时文件不要放进 `skills/`，该目录会完整打包。

## 插件介绍

新插件根目录须提供非空的 UTF-8 `README.md`，介绍用途、使用方法、配置要求和实际限制。统一打包工具会检查并将它加入 ZIP；宿主仍接受缺少 README 的历史插件。

插件管理中“查看介绍”无需加载插件即可打开，正文以 Markdown 显示在网页内弹窗中，支持标题、列表、表格、代码块、链接和图片。相对链接及图片按插件根目录解析，所引用的资源需随包发布；可放在 `docs/` 或 `web/`。缺少介绍时显示“此插件未提供介绍文档”。

## 页面与独立服务

功能页通过网页内大弹窗打开，保留管理页的选中项与滚动位置。关闭弹窗会释放页面和未保存的输入，再打开重新初始化；普通管理刷新不重载已打开的页面。插件停用或卸载会关闭其功能页，关闭页面本身不撤销已提交的后台操作。

新页面优先使用宿主渲染，清单 `pages` 指定 `renderer: "host"`、`ui_version: 1`、`action: "ui"`。协议见 [UI.md](UI.md)，可运行示例见 [examples/host-page](examples/host-page/README.md)。已有 HTML 页面用 `path: "web/index.html"`，引用 `/css/plugin-ui.css` 与 `/js/plugin-ui.js`，根元素加 `data-nexus-plugin`；HTML 页面可按该约定接入。

独立服务通过清单 `services` 声明命令、参数和 `model_tools`，通过 `requires_host` 声明能力依赖。内部管理服务使用 `model_tools: []`。纯服务插件可省略 `entry`，用 `call_handler: {"service":"worker","tool":"page"}` 接收页面操作。第三方依赖通过 `dependencies` 声明。

宿主注入 `NEXUS_HOST_URL`、`NEXUS_PLUGIN_TOKEN`，客户端直接使用，不自行保存凭据：

```python
from nexus_host import HostClient
host = HostClient()
models = host.models.list()
```

```javascript
import {HostClient} from './nexus-host.mjs';
const host = new HostClient();
const models = await host.models.list();
```

Python 使用关键字参数，Node 使用参数对象。客户端导出 `serve` 处理 MCP stdio，并保留服务调用链与作业归属。stdout 仅用于 MCP 协议，日志写 stderr。服务示例从构建 ZIP 运行，客户端由打包器加入。

## 主交流、设置与运行资料

宿主内直接调用 `host.chat_snapshot()`、`host.chat_send(text, request_id)`、`host.chat_subscribe()`，要求 `host.chat_api_version == 1`。发送重试复用请求编号；先订阅再读取快照；重连后重新订阅并取快照，按消息 ID 与修订号协调快照和增量，避免重复追加。

订阅 `get(timeout)` 超时抛出 `queue.Empty`，关闭后返回 `None`；使用完 `close()`。普通设置使用 `host.settings` / `host.save_settings(values)` 或 `storage.settings`，密钥使用 `storage.secrets`，运行资料写入 `host.data_dir`。开发源码目录与 Nexus 数据目录内的插件安装、运行资料分开。

## 打包与验证

执行 `python <SDK路径>/build.py <插件目录路径>`，ZIP 输出到插件目录，清单位于 ZIP 根目录。包含顶层源码、清单、README/LICENSE、web/docs/skills 及实际引用的共享客户端；根目录的测试、环境、缓存、APK、运行资料不入包。发布前更新插件自己的版本号。仓库内也可沿用 `python plugin-dev/build.py <插件目录名>`。

按实际改动运行插件定向测试。完成 ZIP 不代表已经安装、加载或通过真实云端与硬件验收。

## 接口契约

### capabilities

- `describe()` → `{sdk_version, capabilities}`。
- `require(requires={能力:版本})` → `{satisfied:true}`，不支持时加载失败或抛错。

能力组：models、dialogues、history、jobs、storage、services、context、events、hooks、chat、mcp、work，均为 v1；ui v1 表示宿主渲染页面。描述字段仅表示接口存在，不表示已配置可用的云端模型。

SDK 版本标识这一组客户端与文档；能力版本决定插件实际兼容性。不要求旧插件升级清单，也不按整个 SDK 版本拒绝插件。导入时和 `require` 调用使用同一能力检查，错误包含缺失能力或实际版本。

需要 `work` 的插件必须运行在提供 `work v1` 的 Nexus 上。复制新版 SDK 或插件 ZIP 不会升级宿主；其他电脑须先更新实际启动的 Nexus 源码或发行包并完全重启，保留各自的数据与配置，再导入插件。

### work：标准后台 Agent 工作

声明 `requires_host: {"work": 1}`。Python 使用 `host.work.start(...)`，Node 使用 `host.work.start({...})`。这些工作使用现有 TaskStore/TaskController、工具、Skill、项目写入协调与权限审批，不使用插件独立模型对话或 jobs 执行器。

- `defaults()` → 当前 `workspace/provider_id/model_id/effort` 和模型目录；创建长期安排时保存这些值，避免以后切换主交流项目或模型影响安排。
- `list()` → 现有顶层工作摘要，可用于选取事件源。
- `get(task_id,messages=False)` → 工作状态、摘要、待决定事项及可选完整消息；插件工作增加 `plugin_id/plugin_source`。读取不局限于本插件，修改与汇报限定本插件所属工作。
- `events(after=None)` → `{events,cursor}`。省略 after 只返回当前通知游标，不回放历史；传入游标后读取之后的工作通知，最多 1000 条，按返回游标继续。通知包含 `id/task_id/kind/text/source` 等现有字段。重连是否补处理由插件明确决定。
- `start(request_id,goal,title='',context='',constraints='',workspace=None,provider_id=None,model_id=None,effort=None,read_only=False,report=True,source={})` → 接收后的工作摘要。未指定的项目与模型采用调用当时设置。请求标识按插件隔离，同一标识和相同参数不重复创建／运行；参数变化报错。source 为插件自己的来源信息。不自动带入主交流历史或附件，所需背景由插件显式提供。
- `control(task_id,operation,request_id)` → 操作后的摘要。operation 为 pause/resume/cancel，只能控制本插件发起的工作。恢复不代替待决审批。start、control、report 的 request_id 均为非空字符串，最长 200 字符，按插件与操作类别隔离；重试复用编号，新操作使用新编号。
- `report(text,request_id,task_id=None,source={})` → 主交流回报的接收结果。提供 task_id 时校验工作归属，正文与插件来源作为 work_report 进入主交流，不伪装为用户消息；同一请求标识不重复回报。启动前错误也可省略 task_id 并显式提供来源。

`report=False` 只抑制常规完成时自动唤醒主交流，适合插件决定是否汇报的关注任务；审批、受阻与中断等仍使用原有通知。插件可以随后调用 work.report。停用插件会请求暂停其仍在运行／排队／等待决定的工作；保存的工作与实际已发生操作不删除，不自动重放。

Python SDK 的 stdio 适配器明确使用 UTF-8 读写，Windows 系统编码不影响中文页面与工具结果。

#### 选择执行方式

| 需求 | 接口 |
| --- | --- |
| 插件自己完成一次模型生成，或自行管理工具循环 | models / dialogues |
| 运行插件代码、保存进度与检查点 | jobs |
| 让 Nexus 的灵使用现有工具与审批独立完成工作 | work |
| 转发用户实际发给主交流的消息 | chat.send |

时间计算、关注目标、业务条件、外部数据接入和通知时机由插件实现；work 不解析这些业务，也不提供定时器。新插件在已有能力范围内组合即可，无须修改 Nexus。

宿主内 Python 插件原有 `host.query('tasks.*')` 和 `host.command('task.pause/resume/cancel', ...)` 保留兼容；它们返回旧格式。`host.command('work.*')` 仍依赖当前工具调用的工作监督上下文，不是后台服务的创建入口。新插件统一使用 work 的工作摘要与来源归属，独立服务通过同名 SDK 接口调用。读取仍复用现有 TaskStore，控制仍复用 TaskController，没有第二套任务或事件存储。

#### 独立于业务插件的用法

以下代码用于已声明 `requires_host: {"work": 1}`、已就绪的插件或独立服务，无需主交流工具调用上下文：

```python
# 宿主内直接使用传入的 host；独立服务使用 HostClient()。
host.capabilities.require(requires={'work': 1})
cursor = host.work.events()['cursor']
work = host.work.start(
    request_id='project-review-001', goal='检查项目最近的改动，给出建议。',
    report=False, source={'kind': 'project-review'},
)
state = host.work.get(task_id=work['id'], messages=True)
# 后续检查使用保存的 cursor，处理后保存返回的新 cursor。
events = host.work.events(after=cursor)
# 插件决定需要回报时调用；工作没有完成时无需提前汇报。
# host.work.report(task_id=work['id'], text='检查结果……', request_id='review-report-001')
```

```javascript
const host = new HostClient();
await host.capabilities.require({requires: {work: 1}});
const work = await host.work.start({
  request_id: 'project-review-001', goal: '检查项目最近的改动，给出建议。',
  report: false, source: {kind: 'project-review'},
});
const state = await host.work.get({task_id: work.id, messages: true});
```

start 返回已接收的工作摘要，执行由宿主后台推进，不保证返回时已完成。插件自行保存工作 ID 和事件游标；不通过发送伪造用户消息触发工作，不调用宿主私有状态。

### models

- `list()` → 生成模型数组，`kind=generation`，使用 `provider_id + model_id` 选择模型。
- `generate(provider_id, model_id, messages, system='', tools=[], effort=None, purpose='generate', request_id=None, continuation_id=None)` → `{text, tool_calls, usage, finish_reason, continuation_id}`。
- `stream(...)` → text/tool/usage 等规范事件，最后 `{type:'complete', result:生成结果}`。关闭 Python 生成器或退出 JS 迭代会请求取消。
- `cancel(operation_id)` → 请求取消；外部网络流由宿主关闭。

生成支持已有三协议。提供商配置、凭据由宿主管理，插件不能通过这些接口修改或读取模型密钥。未配置的模型可以出现在目录中，`configured=false`；真正调用时明确报错。

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

操作事件按 seq 分页。开始、结果和错误会持久记录；主交流实时流只在内存保留最近1000个事件。插件加载期间允许能力查询、配置、处理器注册及 work 的只读查询，创建与控制工作需等待 ready。

## 管理与兼容

宿主插件详情提供能力依赖、服务、作业和独立对话状态；可取消、重试和查看历史，不增加第二套用户聊天入口。请求记录可按插件、对话、作业、用途筛选。

旧插件接口保留；旧包无须改写。SDK 示例不迁移记忆包、不接入QQ。models/dialogues 不自动执行工具；work 复用标准 Agent 执行与当前权限，不额外授予任意宿主工具权限。没有安装 Node.js 时，Node 示例加载会明确失败，不自动安装运行时。
