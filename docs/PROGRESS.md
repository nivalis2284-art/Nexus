# LingNexus · 持续性验收缺陷修复记录

日期：2026-10-20（Asia/Hong_Kong）
**性质：** 对持续性阶段独立验收结论的修复与复验记录，不是新的阶段交付。
**关联：** 验收报告 [`archive/2026-10-20-ling-continuity/ACCEPTANCE.md`](archive/2026-10-20-ling-continuity/ACCEPTANCE.md)、归档 PRD / RESEARCH / 交付记录。

> 说明：持续性阶段的 PRD / RESEARCH / PROGRESS 已按产品文档整理归档到
> `docs/archive/2026-10-20-ling-continuity/`。`docs/PRD.md` 已更新为自主性第一版开发型 PRD 草案；
> 本文件仍仅记录旧持续性验收缺陷修复，不属于新自主性阶段。
> 本文件记录**持续性验收所发现缺陷的修复**，不改变当前阶段的 PRD 与验收标准。

---

## 1. 本轮做了什么

按验收报告 §4「真实缺陷」逐项修复，**没有改变产品目标、验收标准或既定兼容行为**。
所有改动都落在验收报告已列出的条目上，未新增需求。

| 缺陷 | 级别 | 状态 | 修复位置 |
|---|---|---|---|
| A 本地 API 无来源校验，任意网页可驱动灵 | 高 | **已修复** | `nexus/web/server.py` |
| B 双面板同时展开时批准错对象 | 高 | **已修复** | `nexus/web/ling.js` |
| C 等待审批时挂起 → 审批永久不可作答 | 高 | **已修复** | `nexus/task/store.py` |
| D 审批框提示"或说明"，带条件的允许被判拒绝 | 中 | **已修复** | `nexus/web/ling.js`、`nexus/web/tasks.js` |
| E 暂停期间 resume 丢队列，任务停在 queued | 中 | **已修复** | `nexus/runtime/controller.py` |
| F 旧授权在目标变更后仍可复用 | 中 | **已修复** | `nexus/task/store.py` |
| 测试编码导致 1 项失败 | 环境 | **已修复** | `tests/test_tool_contracts.py` |
| `browser_smoke.cjs` 需外部设置 `PYTHONPATH` | 环境 | **已修复** | `tests/browser_smoke.cjs` |

---

## 2. 各缺陷的修法与依据

### A · 请求来源校验（`nexus/web/server.py`）

绑定 `127.0.0.1` 不构成授权边界：任何页面都能访问 loopback，DNS 重绑定也能把恶意域名指向这里。
在路由之前依次校验三层：

- **Host**：只接受本服务实际端口的 loopback（`127.0.0.1` / `localhost` / `::1`）。重绑定域名不再匹配，请求在进入路由前被拒。
- **Origin**：存在时必须同源。浏览器在跨站写上一定会带它。
- **Content-Type**：带 body 的写操作必须是 `application/json`。浏览器无法在不触发 CORS 预检的情况下发送该类型，而 `text/plain` 正是攻击者能发的形状。

缺失 `Origin` 的请求放行——`curl` 与既有测试不发它，浏览器也无法在跨站写上省略它。

**明确不属于沙箱分级**：`docs/参考/暂缓项.md` 一暂缓的是"Agent 技术上能碰什么"的强制隔离；
本项是"请求是否来自本机用户"的应用层校验，不需要引入沙箱，也不预设权限边界。

### B · 双面板批准错位（`nexus/web/ling.js`）

单一 `notificationDraft` 改为按通知 id 的 `Map`：每个面板持有并提交**自己那条**通知的交互。
选择 Map 而非"同时只允许一个面板"，是因为后者会在用户对照两条通知时反复丢失已展开的详情，
且每秒轮询重绘时还需额外维护"当前唯一面板"。提交时优先使用调用方传入的面板状态；
无参调用仅在恰好一个面板打开时解析，多个打开则拒绝——绝不猜"最近打开的那个"。

### C · 挂起后审批失效（`nexus/task/store.py`）

根因是 `control_version` 前进后，`pending` 交互仍停在旧版本，而作答要求版本严格相等，
于是该交互**既不能作答也不能被清除**，任务无法 `finish`，唯一出路是删除任务。

抽出 `_advance_control_version()`，让**每一个**推进 `control_version` 的写入都在同一事务内
把仍存活的记录带到新版本，覆盖 `command()`、用户生命周期操作、`recover_interrupted`、
`hold_queued_at_startup`、`confirm_wait` 等全部入口。

关键区分：

- **状态变化**（挂起、恢复、重新启用、归档、重启恢复）→ 用户没改目标，旧授权**保持有效**；
- **目标变更**（goal / effective_goal / workspace）→ 旧授权**显式失效**（标记 `expired`），
  作答时返回明确提示要求重新授权，而不是静默复用或悬空。

### D · 审批语义（`ling.js` / `tasks.js`）

审批改为「拒绝」/「允许」两个明确按钮，提交 `false` / `true`。
删除"允许 / 拒绝，或说明"这类引导用户写字、但语义不明确的占位符。
`question` 类型保持自由文本不变。**未新增"允许并附条件"的语义**——那是新需求。

### E · resume 丢队列（`nexus/runtime/controller.py`）

`pause()` 等待超时后 `_active` 未清，此时 `resume()` 的入队被 `_enqueue` 静默丢弃，
worker 退出后队列为空，任务永久 `queued`。

修法：`_active` 期间**仍然绝不入队**（这是不并发执行的关键）；resume 只把持久状态改为
`queued`；唯一 worker 在 `finally` 释放 `_active` 后读一次持久状态，若仍是 `queued` 就重新入队。
无轮询、无额外锁嵌套，`request_id` 幂等与关闭语义均保持。

### F · 旧授权跨目标复用（`nexus/task/store.py`）

`answered_approval()` 只按 `tool + arguments` 匹配，不看目标。
现在目标变更会把该任务的旧审批标记 `expired`，因此只有"当前目标下取得的授权"能被复用。
另有反向用例保证**未变更目标时旧授权仍可复用**（防止修过头）。

---

## 3. 验证结果

### 3.1 自动化

```
.\.venv\Scripts\python.exe -m pytest tests -q
→ 452 passed, 1 skipped in 47.19s
```

对比修复前：`406 passed, 1 failed, 1 skipped`。新增 46 项测试，既有失败已修复。

新增测试文件：

| 文件 | 覆盖 |
|---|---|
| `tests/test_request_origin.py` | 27 项：Host/Origin/Content-Type 的接受与拒绝、GET 同样校验、循环地址拼写、附件上传仍可用且仍校验 Host |
| `tests/test_interaction_lifecycle.py` | 12 项：挂起/归档/重启恢复后审批仍可作答、目标变更使旧授权失效、未变更目标仍可复用、不可重复作答 |
| `tests/test_controller_requeue.py` | 5 项：resume 在 worker 退出后最终被执行、不并发、关闭后不收任务、重复 resume 幂等 |

前端：

```
node tests/frontend_continuity.cjs → all checks passed（含新增双面板回归）
node tests/frontend_behavior.cjs   → all checks passed
node --check nexus/web/{ling,tasks}.js → 通过
```

真实浏览器（Playwright + Chrome）：

```
node tests/continuity_browser.cjs → Continuity browser passed
node tests/browser_smoke.cjs      → Browser smoke passed（不再需要外部 PYTHONPATH）
```

### 3.2 缺陷复现验证（真实服务 + 真实模型 + 真实浏览器）

证据见 [`archive/2026-10-20-ling-continuity/acceptance/fix-verification/`](archive/2026-10-20-ling-continuity/acceptance/fix-verification/)。

| 检查 | 修复前 | 修复后实测 |
|---|---|---|
| A1 伪造 `Host` | 200，可改配置 | **403** |
| A2 `text/plain` 写请求 | 200，可改配置 | **415** |
| A3 外来 `Origin` | 200 | **403** |
| A4 攻击后 permission | 变 `yolo` | **仍为 `ask`** |
| A5 改工作目录 | 变 `C:\Windows` | **403，未变** |
| A6 真实恶意页面 | 写出文件 | **被挡，permission 仍 `ask`** |
| B 双面板点 A 的允许 | 批准了 B | **只批准 A，B 仍等待** |
| C 挂起后审批 | 409，永久失效 | **200，可作答；任务最终 `completed`** |
| D 灵侧审批 UI | 文本输入，正则白名单 | **「拒绝」/「允许」两个按钮，无文本输入** |
| D 点允许后文件 | 被判为拒绝，未创建 | **已创建** |

---

## 4. 未修复 / 未验证事项（如实记录）

| 项 | 状态 | 说明 |
|---|---|---|
| 已完成任务仍可写、可重复完成 | **未修** | 验收报告中列为 §7.1 状态约束风险（需 API 与过期版本，常规 UI 不触发），未纳入本轮必修范围 |
| `jobs` 表 `job_001` 全局主键冲突 | **未修** | 审计视图错误，非阻塞；报告已说明建议 |
| 每轮正常结束写一条 `paused` 通知 | **未修** | 通知噪音，不影响功能正确性 |
| 交互回答在 transcript 中被移到末尾 | **未修** | 报告 §7.1；建议按存储顺序输出 |
| 调度器对 `waiting_user` 任务热循环、`claim_schedule` 未校验版本 | **未修** | 触发面窄，报告 §7.1 已记录 |
| 删除任务级联清除未解决操作 | **保留风险，不设限制** | 按验收报告立场，不擅自新增禁止删除的产品限制 |
| 数小时长任务稳定性 | **未验证** | 本轮最长约 8 分钟 |
| 模型自认受阻（`blocked`）真实反馈质量 | **未验证** | 代码路径存在，真实模型未自然触发 |
| 经灵补充要求"保持挂起"的独立浏览器用例 | **未验证** | 代码路径 `resume: False` 语义正确，本轮未单独跑用例 |
| 第三方 MCP / Skill / 各网关组合 | **未验证** | — |

**未做的修改（有意）**

- 未改旧任务页「发送到挂起任务即继续」——那是交付记录明确保留的兼容行为；
- 未引入沙箱分级、CSRF token、并行执行、多会话管理、LSP、插件平台、计划模式、遥测；
- 未新增"审批可附条件"等新语义。

---

## 5. 结论

验收报告列出的 **A–F 六个真实缺陷已全部修复**，并用真实服务、真实模型与真实浏览器逐项复验；
两项环境问题（测试编码、`PYTHONPATH`）一并修复。

**暂不宣称持续性阶段验收通过。** 本轮完成的是"缺陷已修复、可重新验收"，
是否通过仍应由所有者依据归档 PRD §6 与复验结果判断；§4 列出的未验证项仍待补齐。

建议的复验门槛：`pytest tests -q` 全绿 + 上述 A–F 六项复现脚本 + 一次真实长任务（≥1 小时）的运行观察。
