# Nexus

一个最小可用的编码 Agent。没有数据库，没有人格，没有长期记忆，只有：对话循环、模型接入、工具、权限。

## 启动

双击 `start.bat`（首次会自动建 `.venv` 并装依赖），浏览器会打开 `http://127.0.0.1:8766`。

首次使用点右下角「设置」，填 Base URL、模型名、API Key。

## 它是什么

一个循环：把消息发给模型 → 模型要工具就执行 → 结果回灌 → 直到模型不再要工具。

```
nexus/
  core/
    agent.py        主循环（重试、中止、压缩接入）
    protocol.py     三种协议各自的请求体
    effort.py       思考强度：五档 → 各协议的字段映射
    retry.py        重试策略：退避 + 抖动 + Retry-After
    compaction.py   上下文压缩：先裁剪，后摘要
    subagent.py     子代理：独立上下文 + 深度限制
    thinking.py     为什么思考内容不回传
    prompt.py       提示词编排 + 上下文预算
    permission.py   权限规则（可配置，最后匹配胜出）
    workspace.py    工作区事实（cwd / git / AGENTS.md）
    config.py       多提供商配置
  model/
    gateway.py      流式传输
    _parsers.py     三种协议的流解析
    _sse.py         SSE 分帧
  tools/
    catalog 驱动的装配（tools/__init__.py 的 TOOLS 清单）
    files.py        read / write / edit / glob / grep
    shell.py        bash（参数列表，无 shell）
    jobs.py         bash_background / job_output / job_kill / job_list
    _jobs.py        后台进程与输出环形缓冲
    todo.py         任务清单
    ask.py          向所有者提问
    subagent_tool.py 委派子任务
    web.py          webfetch / websearch（默认关闭）
    mcp_tools.py    把 MCP 声明适配成普通工具
    registry.py     工具注册与统一派发
    _paths.py       路径围栏
  session/
    store.py        JSONL 会话
  skills.py         SKILL.md 发现与解析
  mcp.py            MCP stdio 客户端（手写 JSON-RPC）
  web/
    server.py       HTTP（标准库，无框架）
    index.html      单页前端
    app.js
    app.css
```

## 多提供商

设置里可以添加任意多个提供商，每个有独立的 Base URL、协议和密钥。输入框左下角切换当前模型，按 `提供商 / 模型` 分组。

密钥按提供商分开存于 `credentials.json`（权限 0600），不进 `config.json`。

## 三种协议

`api_family` 可选：

| 值 | 端点后缀 |
|---|---|
| `anthropic_messages` | `/v1/messages` |
| `openai_chat_completions` | `/v1/chat/completions` |
| `openai_responses` | `/v1/responses` |

三者都已用真实接口验证：流式输出、工具调用、多轮回灌、文件读取、思考流。

## 思考强度

五档固定词表：`low` / `medium` / `high` / `xhigh` / `max`，另有「提供商默认」（不发字段）。

各协议落到的字段不同：

| 协议 | 字段 |
|---|---|
| `anthropic_messages` | `output_config.effort` + `thinking.budget_tokens` |
| `openai_chat_completions` | `reasoning_effort` |
| `openai_responses` | `reasoning.effort` |

实测：三种协议 × 五档 = 15 个组合全部可用，且都能流出思考内容。

思考内容在界面上可展开，但**不回传给模型**（三种协议都不保证未签名/未加密的推理文本可被接受）。

## 数据在哪

默认 `%LOCALAPPDATA%\Nexus`，或设置 `NEXUS_HOME`。

- `config.json` — 模型配置（不含密钥）
- `credentials.json` — 密钥，权限 0600
- `sessions/*.jsonl` — 每个会话一个文件
- `skills/*.md` — 可选，技能说明

删掉这个目录就是全新开始。

## 权限

设置里两档：

- `ask` — 写文件、跑命令前弹窗确认（默认）
- `yolo` — 全部放行

同一回合内确认过的同类操作会被记住。

规则也可以写进 `config.json` 的 `permissions` 字段，语义是**最后匹配胜出**，所以兜底规则要放最前、例外放后面：

```json
"permissions": [
  {"tool": "bash", "pattern": "*",     "action": "ask"},
  {"tool": "bash", "pattern": "git *", "action": "allow"},
  {"tool": "bash", "pattern": "rm *",  "action": "deny"}
]
```

不写这个字段时，行为与之前完全一致。

## 中断与重试

运行中「发送」按钮会变成停止按钮。停止是在**安全检查点**生效的：每次模型请求前、每次工具执行前。已经开始的写文件不会被从中间打断。

模型请求失败时按策略重试：

- 只重试 408/409/425/429/500/502/503/504/529 和网络层错误
- 指数退避 + 抖动，默认 3 次，上限 8 秒
- 如果响应带 `Retry-After`，优先采用服务端给的时间
- **已经输出过内容后不再重试**——重放会造成重复文本
- 400/401/403 这类重试无意义的错误直接报错

## 网络工具默认关闭

`webfetch` 和 `websearch` 默认不存在，需要在 `config.json` 里配置后才注册：

```json
"webfetch_hosts": ["docs.python.org", "github.com"],
"search_endpoint": "https://your-search.example/api"
```

没有 `webfetch_hosts` 就不会出现 `webfetch`；没有 `search_endpoint` 就不会出现 `websearch`。工具不存在比调用后失败更清楚。

`webfetch` 会拒绝非 http(s) 协议，并拒绝解析到内网/回环地址的主机（即使它在白名单里）。

## 长任务

`bash` 只适合跑完就结束的命令。任何可能超过一分钟的（装依赖、跑测试、构建、起服务）用 `bash_background`：

- 启动后 0.4 秒内返回 `job_id`，不阻塞
- `job_output` 读取输出，**只返回游标之后的新内容**，不会重复发送
- `wait_seconds` 会阻塞等待，但任务一结束就立刻返回，不是死等
- `job_kill` 停止任务，`job_list` 看有哪些在跑

输出存在有界环形缓冲里（上限 200k 字符）：超长构建不会吃掉内存，只保留最新的部分。

## 上下文压缩

两级，按顺序生效：

**第一级 · 裁剪**（每次请求前，不调模型）
工具结果超过 8192 字符时，保留头 4096 + 尾 1024，中间替换为固定标记。纯文本操作，不改语义，不引入新错误。

**第二级 · 摘要**（用量超过窗口 80% 时）
把较老的历史折叠成一份结构化检查点，保留最近 16% 的原文。检查点用固定八段模板，包在 `<compacted-summary>` 标签里。

三条防御规则：

1. 压完**必须真的变小**，否则放弃这次压缩、保留原状
2. 只在**工具调用闭合**的位置切分，绝不切开 call/result 配对
3. 摘要失败就退回裁剪，**绝不让压缩失败中断对话**

## 子代理

`subagent` 工具把一个独立任务交给子代理：它有自己的对话历史，共享同一套工具，最后只把结论返回给主代理。

深度上限默认 2（主 → 子 → 孙即止），超过会报错而不是无限递归。目前只有 `spawn`（全新上下文），没有 `fork`（继承父上下文）——后者属于"子代理该记住什么"的问题，留待长期方向确定。

## Skill

放在这两个位置，项目级优先：

```
<工作区>/.nexus/skills/<name>/SKILL.md
~/.nexus/skills/<name>/SKILL.md
```

格式是 YAML frontmatter + 正文：

```markdown
---
name: code-review
description: Review a diff for bugs and style.
when: the user asks for a review
---
正文……
```

system prompt 里**只放 name + description + when**，正文要用 `read_skill` 现取。没用到的 skill 只占一行，不占一页。

## MCP

在 `config.json` 里配置后，启动时连接 stdio 服务器，把它们的工具并进同一个工具列表：

```json
"mcp": {
  "servers": [
    {"name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
  ]
}
```

工具名加前缀 `mcp__<server>__<tool>`，不会和内置工具撞名。

两点实现说明：

- 子进程环境会**擦洗掉所有看起来像密钥的变量**（KEY/TOKEN/SECRET/PASSWORD 等），不会把 API Key 传给第三方服务器
- 有一个 MCP 服务器起不来时，只打印错误并跳过，不影响其他功能

**Windows 上的命令问题**：`npx`、`uvx` 这些在 Windows 是 `.cmd` 批处理，进程 API 不能直接执行，报错还是误导性的"找不到文件"。客户端会自动解析：能找到 node 就直接跑 `npx-cli.js`，否则交给 `cmd.exe /c`。参数始终按列表传递，不拼字符串。

已用官方 `@modelcontextprotocol/server-filesystem` 实测通过：连上后拿到 14 个工具，`list_directory`、`read_text_file` 都真实读到内容，越界路径被服务器正确拒绝并以工具错误返回。

## 待办与答复

人工确认（`ask_user` 或写文件授权）会阻塞等待。如果此时关掉浏览器、或按下停止，等待会被**立即唤醒**而不是干等到超时：

- 中止 → 所有等待被唤醒，审批按"拒绝"处理，提问按"无回答"处理
- 流断开 → 同上，并在下一个请求前就恢复可用

这条曾经是个真 bug：一次提问挂着时关掉页面，服务端会占住运行锁 10 分钟，期间所有新消息都返回 409。实测修复后恢复到 1 秒内可用。

## 测试

```
.venv\Scripts\python.exe -m pytest tests -q
```

221 项，覆盖主循环、工具、路径围栏、权限闸门、HTTP 往返、多提供商配置、五档思考强度映射、重试策略、中断、压缩、子代理、skill、MCP、后台任务、并发写保护，以及实测出来的协议兼容性怪癖：

- 流式 tool_calls 不带 `type`
- Responses 最终事件换了 item id
- 带 reasoning 时首帧 `id` 为空字符串
- finish 帧把 `reasoning_content` 发成空串
- 一条消息同时带思考和正文时，正文必须仍然显示

MCP 用 `tests/mcp_server_stub.py` 这个真实的 stdio 服务器来测，不是 mock：分页、错误结果、凭据擦洗都经过真实进程验证。


## 文档

| 文档 | 作用 |
|---|---|
| `docs/交接.md` | 接手必读：状态、怎么跑、坑在哪 |
| `docs/变更记录.md` | 每次改动一句话：目的、改了什么、什么问题、怎么修的 |
| `docs/暂缓项.md` | 目前不需要、未来确定需要的（含所有者原话） |
| `docs/待定项.md` | 目前确定不需要、未来不确定的（含所有者原话） |
