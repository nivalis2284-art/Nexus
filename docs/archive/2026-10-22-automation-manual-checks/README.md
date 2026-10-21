# 自动化旧接口手动验收脚本（历史）

本目录保留 2026-10-22 清理时移出的两个手动验收脚本：`automation_acceptance.py` 与 `automation_change_acceptance.py`。它们曾用于真实模型唤醒、网页监视与变化检测的端到端探测，包含真实模型和本地浏览器页面操作。

脚本仍按旧的 `AutomationRuntime(state, ...)` 构造方式调用 `runtime.store.save_schedule`、`save_watch`、`request_check` 等接口，并依赖 `nexus.web.server.STATE`。当前实现已将调度规则迁至独立调度服务，自动化运行时只负责接收唤醒并保存活动记录，因此这两个脚本不能作为当前验收依据，也不应直接运行。

保留脚本是为了追溯当时的验收方式和行为预期；当前验收请以仓库现行自动化测试和文档为准。
