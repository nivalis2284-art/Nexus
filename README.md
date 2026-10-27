# Nexus

Nexus 是由所有者掌握的个人 Agent 底座。当前版本收敛到一个主交流入口“灵”和即时后台工作：在交流中交代、补充和干预工作，工作独立推进，结果或需要决定的事项回到主交流。

[《01-概念与原则》](docs/愿景/01-概念与原则.md)是最高原则。当前范围与验证进度见[业务收敛与优化计划](docs/当前阶段/Nexus业务收敛与优化计划.md)。历史资料保留在 `docs/参考/`，不作为当前功能说明。

## 启动

主要面向 Windows，使用 Python 3.12。

1. 双击 `start.bat`；脚本检查虚拟环境并准备项目依赖。
2. 打开终端输出的本机地址，通常为 `http://127.0.0.1:8766`。
3. 在设置中填写模型提供商、协议、Base URL 与 API Key，选择模型与思考强度。
4. 进入“灵”直接交流。耗时工作由灵派发，点击关联工作查看只读详情；补充要求仍从主交流发送，可选择其关联工作。

通过 `start.bat` 默认使用仓库内 `.nexus-data`。可以用 `NEXUS_HOME` 或 `--home` 指定其他数据目录；不会自动迁移另一个目录。直接运行 `python -m nexus` 默认使用 `%LOCALAPPDATA%\Nexus`。同一数据目录只运行一个实例。

```powershell
.venv\Scripts\python.exe -m nexus --home .nexus-data --workspace . --no-browser
```

首次启动与依赖说明以 [pyproject.toml](pyproject.toml) 和启动脚本为准。仓库不包含可用的模型密钥。

## 日常交流与工作

- 主交流和工作均由服务端运行，关掉网页不会停止已接收的工作；退出服务会中断执行。重启后须明确继续，不恢复旧进程，不自动重放结果未知的操作。
- 主交流运行中发来的消息先保存，在下一模型请求边界送入。新话题不会取消后台工作。
- 后台最多同时执行三个工作，更多工作排队；同一 Git 项目（非 Git 则为工作目录）的修改串行。只读研究可以并行，转为可能产生副作用的工具前取得该项目写入资格。不自动创建工作树或复制项目。
- “停止当前交流”只停止主交流。后台工作分别暂停或取消，也可明确“暂停全部工作”。暂停保留现场；只补充信息不会自动恢复。
- 审批和问题有明确归属；在主交流中作答，其他消息不算批准。等待决定时仍可继续其他交流。
- 工作完成、受阻或需要决定时，由灵结合交流解释情况；完整结果、工具记录、附件和未确认操作保存在工作详情。已完成结果需要修改时，沿用同一个工作开始新一轮，保留旧结果。
- 灵可以为工作选择已配置的模型和思考强度，并在后续请求边界调整；所有者的明确指定优先。主交流模型由所有者选择。历史与工具结果独立保存，不因更换模型而重放工具。
- 所有者基本原则与交流偏好保存在设置中的一段文本，也可明确让灵修改；后续请求生效。后台工作只读取，不自行改写。

## 保留能力与本轮退出范围

保留 Anthropic Messages、OpenAI Chat Completions、OpenAI Responses 三类模型协议、流式交流、文件和命令、后台进程、附件原件及分页读取、Skill、MCP 与普通插件接入，以及权限规则、上下文裁剪和摘要压缩。

定时与重复安排、网页监听、长期交代、自由唤醒、每日回顾、静默经历、专门人设管理及静态记忆原型已退出日常业务。普通独立会话和手动新建任务退出入口；工作由主交流承接。不再运行自主活动模型执行器，也不提供独立通知中心。相关研究资料按计划独立归档，旧代码可通过 Git 历史查看。

本轮不以模型调用费用或配额限制行为。上下文窗口与协议要求仍按实际模型能力处理；这些限制不代表费用预算。

## 数据与配置

```text
config.json              模型、工作目录与运行设置
credentials.json         API 凭据
owner-instructions.txt   所有者原则与交流偏好
sessions/*.jsonl         主交流原文与工具记录
tasks.sqlite3            工作、运行、操作、请求、结果事件与主交流输入队列
attachments/             按交流和工作归属保存的原件与提取文本
model-requests.sqlite3   实际模型请求记录（按现有保留策略）
plugins.json             普通插件状态与设置
plugin-events.sqlite3    普通插件事件与订阅
plugins/<id>/            普通插件运行资料
archive/convergence-*/   本轮迁出的研究业务资料与读取说明
```

模型协议标识分别为 `anthropic_messages`、`openai_chat_completions`、`openai_responses`。思考强度可使用提供商默认或 `low / medium / high / xhigh / max`，实际支持取决于服务端。

模型配置可填写 `context_window_tokens`；本地上下文窗口按全局 `context_limit`、模型窗口、64,000 的顺序选择，仅用于请求容量管理，不会扩大服务端真实上限。

凭据、研究原文和运行记录属于所有者；导出或处理数据时保留目录和关联。不要把凭据写进 Git 或日志。

## 开发与测试

安装开发依赖后，在仓库根目录运行：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest tests -q
node tests/frontend_behavior.cjs
node tests/frontend_continuity.cjs
node tests/frontend_model_context.cjs
node tests/frontend_plugins.cjs
node tests/frontend_skills.cjs
```

浏览器冒烟使用隔离数据目录、本地模拟提供商和已安装的 Chrome，通过 Node 内置 WebSocket 操作页面，不调用真实模型，也不需要 npm 依赖（Node 22+）。Windows 默认使用 Chrome 标准安装路径，可通过 `NEXUS_CHROME` 和 `NEXUS_TEST_PYTHON` 指定实际可执行文件：

```powershell
node tests/browser_smoke.cjs
```

测试覆盖范围、通过数量及真实模型验证情况会随版本变化；请以当前测试运行结果为准。自动化测试通过不等于所有功能已经完成实际操作验收。

## 文档导航

- [Agent 开发规则](AGENTS.md)：仓库协作约定。
- [概念与原则](docs/愿景/01-概念与原则.md)：最高原则。
- [具象理想](docs/愿景/02-具象理想.md)：长期方向，不自动构成当前任务。
- [业务收敛与优化计划](docs/当前阶段/Nexus业务收敛与优化计划.md)：本轮决定、实施与验证。
- [文档索引](docs/README.md)：当前文档和历史研究入口。
