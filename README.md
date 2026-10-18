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
    agent.py        主循环
    protocol.py     三种协议各自的请求体
    effort.py       思考强度：五档 → 各协议的字段映射
    thinking.py     为什么思考内容不回传
    prompt.py       提示词编排 + 上下文预算
    permission.py   权限规则
    workspace.py    工作区事实（cwd / git / AGENTS.md）
    config.py       多提供商配置
  model/
    gateway.py      流式传输
    _parsers.py     三种协议的流解析
    _sse.py         SSE 分帧
  tools/
    files.py        read / write / edit / glob / grep
    shell.py        bash（参数列表，无 shell）
    registry.py     工具注册与统一派发
    _paths.py       路径围栏
  session/
    store.py        JSONL 会话
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

## 测试

```
.venv\Scripts\python.exe -m pytest tests -q
```

36 项，覆盖主循环、工具、路径围栏、权限闸门、HTTP 往返、多提供商配置、五档思考强度映射，以及四类实测出来的协议兼容性怪癖：

- 流式 tool_calls 不带 `type`
- Responses 最终事件换了 item id
- 带 reasoning 时首帧 `id` 为空字符串
- finish 帧把 `reasoning_content` 发成空串
