# 持续性简化交付记录

日期：2026-10-22。范围：已批准的持续性简化、必要修复与代码验证；之后研究 Dot，报告另见 [DOT-REFERENCE.md](DOT-REFERENCE.md)。

## 目标与结果

让后台任务能够持续推进，并把完成和挂起交给实际工作判断。去掉以填写过程记录代替完成判断的手续，复用现有任务库、运行控制、交互和 MCP。此次没有重建持续性框架，也没有增加长期目标、长期记忆或任务并行引擎。

| 事项 | 本次交付行为 |
|---|---|
| 完成任务 | `task_finish(summary)` 即可；证据引用和未解决事项可选，不要求先建步骤、逐项验证或补齐成功证据 |
| 历史记录 | 原有步骤、证据、尝试和操作记录继续保存；历史中的未完成步骤或结果未知记录不再自动阻止完成 |
| 重试 | 删除根据相同调用失败次数强行封路的逻辑；不必先补写“新依据”才能再次尝试 |
| 持续推进 | 手动任务与灵委派任务都在普通模型回复后继续，直到完成、明确挂起、取消、等待交互或实际运行错误 |
| 固定次数 | 后台任务取消旧 40 步上限、4 次续跑及 3 次无进度记录限制；普通会话、自动唤醒和临时子代理没有借此改成无限执行 |
| 主动挂起 | 新增 `task_pause(reason)`，复用既有挂起状态，理由进入记录、任务状态和通知 |
| 补充信息 | 挂起任务收到补充只保存；任务页、灵的工作工具、旧请求中的 `resume:true` 都不会由“发消息”隐含恢复 |
| 恢复 | 用户明确恢复，或既有一次性安排触发恢复；运行中的补充在下一模型请求边界送入 |
| 真实等待 | 仍按原有机制等待审批或问题回答，不把普通记录中的 `blocked` 字段当成执行禁令 |
| 重启 | 沿用中断记录与用户恢复行为，不自动重放旧输入或旧进程 |

模型仍应如实说明实际结果与限制。放宽的是系统规定的过程手续，并不把“任务标记完成”变成对结果正确性的独立证明。

## 主要代码位置

- `nexus/runtime/assembly.py`：统一两类持久任务续跑，去掉固定续跑门槛，处理完成和挂起后的执行退出。
- `nexus/runtime/callbacks.py`：简化完成，移除失败次数门槛，加入主动挂起；续跑用明确的运行时提示，不再重复原始用户请求。
- `nexus/core/agent.py`：允许后台任务不设工具循环步数上限；普通有限步数调用保持原行为。
- `nexus/runtime/input.py`、`nexus/ling.py`、`nexus/web/tasks.js`：统一补充消息与恢复操作的语义，显示结果摘要和挂起说明。
- `nexus/task/store.py`、`nexus/tools/tasks.py`：复用现有状态存储和工具注册；没有数据库结构迁移。

## 本轮修复的具体问题

1. 普通文字回复被当作执行轮次结束，手动任务直接挂起；灵委派任务还会被续跑计数截断。现在两者持续执行到明确结束条件。
2. 旧续跑通过再次复制最后一条用户请求构造上下文，可能把已做过的动作再次当作新要求。现在发送注明“继续已有任务”的运行时提示。
3. 模型在同一响应内先完成或挂起、再请求写文件时，后续调用可能继续进入审批或执行。现在在模型请求、审批和执行边界读取既有任务状态并退出；不会在完成后多执行同批次写操作。
4. 挂起任务可被旧消息接口的默认恢复行为意外启动。现在所有消息入口均只补充，不启动。
5. 已完成任务页面可能继续展示过时的下一步。现在展示结果摘要，并隐藏已完成任务的旧下一步。

第三项属于实际控制流缺陷的修复，使用原有任务状态判断；没有引入新的权限层、风险评分、自动审批或防御框架。保留的校验包括状态有效性、明确引用的证据必须存在，以及真实待处理交互不能被完成调用跳过。

## 验证

整合运行：**166 passed，1 skipped，34.38 秒**。跳过项是当前环境不支持目录符号链接的既有启动测试。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_agent_loop.py tests/test_agent_unlimited.py tests/test_task_terminal_state.py tests/test_agent_task_hooks.py tests/test_task_completion_gate.py tests/test_task_tools.py tests/test_task_todo.py tests/test_task_runtime.py tests/test_task_controller.py tests/test_task_store.py tests/test_task_scheduler.py tests/test_task_confirmation_start.py tests/test_task_interactions.py tests/test_task_http.py tests/test_ling.py tests/test_continuity_input.py tests/test_continuity_store.py tests/test_continuity_runtime.py tests/test_continuity_capabilities.py tests/test_interaction_lifecycle.py tests/test_permission_edit.py tests/test_automation_basics.py tests/test_scheduler_bridge.py tests/test_bootstrap.py tests/test_tool_contracts.py
```

这些检查覆盖：无步骤/无强制证据完成、历史保留、连续失败不需补写手续、两种来源任务超过旧续跑上限、后台实际超过 40 次模型请求、显式取消、主动挂起理由、挂起消息只保存、完成/挂起后同批次工具停止，以及已有审批、任务安排、通知、MCP 共用连接和服务启停链路。

另外通过：Python 编译检查、修改代码及新增测试的 Ruff F 类检查、`node --check nexus/web/tasks.js`、`node tests/frontend_continuity.cjs`、`node tests/frontend_behavior.cjs` 和 `git diff --check`。前端检查属于静态行为检查，并非浏览器实操。

按本次授权范围，没有进行真实业务模型调用、浏览器全量验收或长期真实运行。日常实例未重启或切换；新代码在之后重新启动项目时生效。自动化检查说明所覆盖代码链路成立，长期任务的实际模型行为仍需要日常使用观察。

## 执行说明

已尝试按要求并行使用多个 DeepSeek high 子代理。提供商出现 504 超时，文档代理报错，其他代理在等待期间没有落地改动。为及时止损，关闭这些代理，由主代理完成实现、整合验证和报告；没有把未完成的子代理工作记作交付成果。

未改写历史归档、愿景或用户数据。工作区已有的 `.codex-remote-attachments/` 和 `tmpsg31j6vy/` 未纳入本次变更。
