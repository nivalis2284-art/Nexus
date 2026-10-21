# LingNexus · 持续性阶段 独立交付验收报告

**验收对象：** `H:\文档\LingNexus\Nexus`，提交 `6b289ae`（`feat: add continuous Ling interaction and independent work supervision`）
**需求基线：** 灵的第一阶段 · 持续性 PRD、交付记录与研究（同目录 [PRD.md](PRD.md)、[RESEARCH.md](RESEARCH.md)、[PROGRESS.md](PROGRESS.md)）
**架构基线：** `6b289ae`（本报告所有源码行号、实测结果均针对此提交；之后的产品文档整理未改动 `nexus/` `tests/`）
**验收日期：** 2026-10-20（Asia/Hong_Kong）
**验收方式：** 独立源码审查 + 全量自动化回归 + 真实浏览器端到端操作 + 真实模型端点联调 + 缺陷复现
**验收性质：** 只读审查。未修改任何正式业务代码；新增文件仅本报告与同目录 [`acceptance/`](acceptance/)。
**修订：** 2026-10-20 第二轮——按归档 PRD / RESEARCH / PROGRESS、`AGENTS.md`、`docs/参考/` 的暂缓与待定决定重新定性，纠正误判与越界项，见 §10 修订记录。
**修复状态：** §4 的 A–F 六项已于 2026-10-20 修复并逐项复验，见 [`docs/PROGRESS.md`](../2026-10-21-phase1-baseline/PROGRESS.md)。本报告保留**验收当时的原始结论与证据**，不追改为"已通过"——是否通过由所有者依据复验结果判断。

**归档说明：** 本轮验收期间产品文档已整理：持续性阶段的 PRD / RESEARCH / PROGRESS 归档到本目录，[`docs/PRD.md`](../2026-10-21-phase1-baseline/PRD.md) 改为自主性可行性研究。本报告与它们放在一起，作为持续性阶段的收口结论，对应 [`ROADMAP.md`](../2026-10-21-phase1-baseline/ROADMAP.md) 中的「持续性收口条件：独立验收报告给出通过或缺陷」。

---

## 0. 结论

### 总体验收结论：**未通过（需修复后复验）**

判定的唯一依据是归档 PRD §6 的验收标准。§6.1、§6.3 的核心体验**已用真实模型 + 真实浏览器证实成立**；不通过的原因只来自 §6.4 / §6.5 / §6.2 中几条**已复现的行为错误**，与"缺功能"无关。

**必须先修的 4 项（均在 PRD 明文要求内，均已实测复现）：**

| # | 缺陷 | 违反条款 | 级别 |
|---|---|---|---|
| A | 任意网页可操纵本地 API：无来源校验、无 `Host` 校验，可改权限/改工作目录/让灵写文件 | §6.5「仍遵守安全与授权边界」 | **高** |
| B | 通知面板双开时点 A 的「允许」实际批准 B | §6.5「不得超出已有授权」 | **高** |
| C | 等待审批时点「挂起」，该审批永久不可作答，任务无法完成也无法恢复 | §6.2「暂停恢复后进度保留」、§6.5「不会造成状态混乱」 | **高** |
| D | 审批框提示"允许 / 拒绝，或说明"，实为仅 4 个整串关键词算允许，附条件的允许被当作拒绝 | §6.4「需要授权时主动请求处理」 | **中** |

**另有 1 项建议一并修（影响 §6.2 但触发面窄）：** 暂停期间的 `resume` 可能丢失队列，任务停在 `queued` 无人认领。

**结论的性质：** 这不是"持续性没做出来"。持续性最难的"边干活边聊天、执行细节不淹没主体、完成/待决策时主动反馈"三件事**是真实成立的**。不通过是因为在这套已经跑通的机制上，**授权的方向、授权的对象、授权的失效**三处存在真实错误——恰恰是 PRD §6.5 最看重的地方。

### 本轮纠正的误判（详见 §10）

| 原判定 | 新判定 | 理由摘要 |
|---|---|---|
| P1-2「给挂起任务补要求自动恢复」= 缺陷 | **误判，撤回** | 旧任务页「发送到挂起任务即继续」是 PROGRESS 第 28 行**明确保留的兼容行为**；实测走的就是这条路径。经灵补充要求用的是 `resume: False`，本身正确 |
| 缺失沙箱分级 = 缺陷 | **明确暂缓，不计失败** | `docs/参考/暂缓项.md` 一、沙箱分级已暂缓；但**来源校验不等于沙箱分级**，A 项仍成立 |
| 单 Worker 串行 = 未达"多任务并行" | **不需要，不计失败** | PRD §5.2 / RESEARCH 已确认任务执行串行，并存与排队即可 |
| 测试 1 failed = 产品缺陷 | **环境问题，不计失败** | 测试自身未指定编码 + 本机缺少声明的依赖 |
| 数小时稳定性未做 | **未验证，非失败** | 本轮最长约 8 分钟，属未覆盖而非已证失败 |
| 删除任务可抹掉未解决操作证据 | **风险，保留说明** | 确有影响，但**不建议**因此增设禁止删除的产品限制；需先由所有者确认边界 |

---

## 1. 验收环境与可复现性

| 项 | 值 |
|---|---|
| OS | Windows，屏幕 2048×1152 @125% |
| Python | 3.12.10（`.venv`） |
| 浏览器 | Chrome（Playwright `channel: chrome`，headless，1360×900 与 390×844 两档） |
| 模型 | 本机已配置的 `openai_responses` 端点，模型 `deepseek-v4.1-flash`（**真实调用，非桩**） |
| 数据隔离 | 每个场景使用独立的临时 `NEXUS_HOME` + 独立 workspace（验收结束后已删除）；未触碰 `%LOCALAPPDATA%\Nexus` 里的日常配置、会话与凭据 |
| 证据 | [`acceptance/`](acceptance/)：保留关键界面截图与缺陷修复复验截图。逐步运行日志和 store 探测输出为控制篇幅已从工作区移出，原文仍由提交 `e4cd813` 追溯。 |

**证据说明：** 截图来自本轮真实操作，临时工作目录与测试驱动脚本在验收结束时清理。报告引用的逐步日志可只读追溯，例如 `git show e4cd813:docs/archive/2026-10-20-ling-continuity/acceptance/run28.log`；无需恢复整个工作区。旧结论和缺陷状态不因证据精简而改写。

---

## 2. PRD 逐项覆盖

图例：**✅ 通过**｜**⚠️ 部分**｜**❌ 未通过**｜**➖ 不在本阶段范围（不计失败）**｜**❔ 未验证（不计失败）**

### §6.1 持续交互 — 全部通过

| 验收项 | 结论 | 实测证据 |
|---|---|---|
| 交付长时间复杂工作后仍能正常交流 | ✅ | 委派 `work_start` 后立即返回任务 id，随后发送"2 的 10 次方"得到 `1024`，任务同期 `running`（`run.log`） |
| 工作未完成时可提无关新问题并正常响应 | ✅ | 同上；`/api/ling` 显示 `running:true, queue:1` 时仍返回 `1024` |
| 新交流不会使原工作无故暂停/取消/重开 | ✅ | 无关交流后任务仍 `running`，最终 `completed`，产物落盘 |
| 日常交流不因等工作结果长时间阻塞 | ✅ | 委派后下一轮回复数秒完成；任务其后约 3 分钟独立跑完 |
| 多轮交流后仍能查询此前工作状态 | ✅ | 后续 `work_read` 正确返回 `state:completed`、6 步全 `verified`、3 条证据 |

> 这是本阶段最扎实的部分，与 PRD §2.4 的目标体验一致。

### §6.2 工作的持续性 — 4 项通过，1 项有边界缺陷，1 项未验证

| 验收项 | 结论 | 实测证据 |
|---|---|---|
| 工作有可查询的状态、目标和必要进度 | ✅ | `/api/tasks/{id}` 返回 `state/steps/evidence/decisions/work_summary/blocked/next_step` |
| 工作能独立推进，不依赖聊天流 | ✅ | 关闭页面不影响；由服务端 Worker 执行 |
| 通过已有方式暂停/恢复时进度能保留 | ⚠️ | 常规路径通过（`run16.log`：挂起后 `progress_version` 不变，恢复后继续）。但**等待审批时挂起会让审批永久失效**（缺陷 C）；**暂停中 resume 可能丢队列**（缺陷 E） |
| 中断/失败/等待用户/完成有明确反馈 | ✅ | 硬杀进程后 running→`interrupted`（含"已执行操作未回滚，请核查"）、pending→`held`，UI 给出「继续待恢复输入」（`50-held.png`、`run6.log`） |
| 系统不会仅因一轮回复结束就误判完成 | ✅ | `task_finish` 被服务端拒绝 4 次（缺 verification 证据、步骤未 verified）后才接受；每轮结束写「本轮结束；任务完成状态需独立验证」 |
| 数小时长时间运行的稳定性 | ❔ | 本轮最长约 8 分钟；**未验证，不作为失败依据** |
| 多个重工作同时并行推进 | ➖ | PRD §5.2 与 RESEARCH 已确认任务模型执行**串行**，并存/排队即可；不要求并行 |

### §6.3 主体上下文保护 — 全部通过

| 验收项 | 结论 | 实测证据 |
|---|---|---|
| 大量工具调用与中间日志不无条件进入主体上下文 | ✅ | 任务侧 `task_messages` 142 条，灵侧会话 41 条 29 KB；灵仅拿到 `WorkService.context_text()` 的摘要投影（≤15 活跃 + 5 近期，字段截断 700 字符） |
| 灵仍能获知目标、重要状态、关键结果 | ✅ | `work_read` 正常返回；灵准确复述状态、步骤、证据数量 |
| 用户要细节时能重新获取执行记录与证据 | ✅ | `work_read(section=evidence/messages, offset/limit/text_offset)` 分页；任务页可见完整轨迹 |
| 多轮执行不因中间细节反复继承而挤占空间 | ✅ | 同左；任务上下文未出现在灵 prompt 中 |
| 精简/隔离不导致已保存事实无故丢失 | ✅ | 证据与消息在 SQLite 完整，灵侧摘要与任务侧原始记录一致 |

### §6.4 主动反馈 — 5 项通过，1 项有缺陷，1 项未验证

| 验收项 | 结论 | 实测证据 |
|---|---|---|
| 完成后主动发送简短完成通知 | ✅ | 完成即产生 1 条 `completed` 通知并页内展开（`70-feed-pending.png`）；刷新/重启后仍在，不重复 |
| 需要决策或授权时主动请求处理 | ❌ | 通知本身正确触发（`71-action-panel.png`），但**批准对象可能错误**（缺陷 B），且**审批答复语义错误**（缺陷 D） |
| 无法自行解决时主动说明 | ❔ | `blocked` 路径存在（`store.py:275`），本轮真实模型未自然触发一次"自认受阻"，**未验证** |
| 普通错误自行处理时不反复打断 | ✅ | 任务内重复工具失败只进任务轨迹；同一待决决策只通知一次（`event_seq` 去重） |
| 用户能识别通知对应哪项工作 | ✅ | 通知带 `task_id`、标题、「查看关联任务」 |
| 处理通知后能继续原交流 | ✅ | 批准后草稿仍在输入框，历史与视图未被打断（`run3.log`、`run10.log`） |

### §6.5 自主判断与用户控制 — 4 项通过，2 项未通过

| 验收项 | 结论 | 实测证据 |
|---|---|---|
| 不改变要求前提下自主调整方案 | ✅ | 观测到模型自行改用 `todo_write`、自行调整步骤拆分 |
| 普通执行决策不需逐项确认 | ✅ | `yolo` 无审批；`ask` 仅写/命令类触发确认 |
| 用户可以询问和干预进行中的工作 | ✅ | `work_list` / `work_read` / `work_message` / `work_control` 实测可用 |
| 暂停、恢复、取消、调整要求不造成不必要的数据丢失或状态混乱 | ❌ | 等待审批时挂起 → 审批永久失效（C）；暂停中 resume → 永久 `queued`（E）。注：「调整要求」在旧任务页触发恢复是**既定兼容行为**，不计缺陷 |
| 超出已有授权或涉及重要安全风险的操作仍遵守授权边界 | ❌ | 来源校验缺失使"谁在调用"不受控（A）；双面板批准错对象（B）；旧授权可跨目标复用（F，仅 API 路径可达） |
| 不把自主调整误解为可改用户目标 | ✅ | 未见模型擅自改目标；`LING_INSTRUCTIONS` 明确禁止 |

### §6.6 兼容、可靠性与交付验证 — 5 项通过，1 项部分

| 验收项 | 结论 | 实测证据 |
|---|---|---|
| 现有 Coding Agent 功能与关键交互流程可用 | ✅ | `browser_smoke.cjs` 通过（六格式附件、下载、图片、审批、重启持久化、离线业务图） |
| 旧会话/任务/配置/附件/历史无未确认破坏 | ✅ | 旧会话可建可删；灵会话被正确保护（删除返回 409）；v1→v2 迁移为纯增量 |
| 代表性复杂任务做真实模型 + 真实界面联合验证 | ✅（本轮补做） | §3 全部为真实模型 + 真实浏览器 |
| 关键状态变化/恢复/通知/控制有回归测试 | ⚠️ | 406 passed，但**既有测试系统性地绕开了本轮缺陷路径**：调度测试只填 `timezone="UTC"`；`test_web_maintenance.py` 总是"等服务回 `waiting_user` 就立刻作答"；`continuity_browser.cjs` 只开单面板。"全绿"不等于覆盖 |
| 交付报告记录已验证/未验证/遗留 | ✅ | 交付记录诚实标注了"真实模型未验证"，本轮已补做 |
| 不以未实现的长期能力作为失败理由 | ✅ | 长期记忆、人格演化、自主性、并发并行、LSP、插件平台、计划模式、遥测均**不作为失败理由** |

### 明确暂缓、待定或不属于本轮的能力（不计失败）

以下均**未纳入缺陷**，亦不构成不通过理由：

| 能力 | 依据 | 本轮处理 |
|---|---|---|
| 沙箱分级（技术强制隔离） | `docs/参考/暂缓项.md` 一 | 暂缓，不要求。**但来源校验是另一件事**（见 A） |
| 复杂多会话管理（自动命名、搜索、导出） | 暂缓项 二 | 不要求。手动重命名保留即可 |
| 计划模式 | 暂缓项 三 | 不要求 |
| 会话格式版本化、遥测、崩溃恢复 | 暂缓项 四 | 不要求 |
| LSP | `docs/参考/待定项.md` 一 | 待定，不要求 |
| 插件平台 | 待定项 二（"插件化不是强制要求"） | 不要求；只要求可维护 |
| 任务模型并行推进 | PRD §5.2、RESEARCH | 串行是既定设计 |
| 完整长期记忆、人格演化、自主性 | PRD §5.2、§6.6 末条 | 不属于本轮 |

---

## 3. 工程验证结果

### 3.1 Python 测试 —— **环境问题，非产品缺陷**

```
.\.venv\Scripts\python.exe -m pytest tests -q
→ 406 passed, 1 failed, 1 skipped in 39.04s
```

- **`1 failed`：`test_foreground_command_observes_shared_stop_signal`，属测试自身缺陷。** 该测试用 `path.write_text(...)` 未指定 `encoding`，本机 `locale.getpreferredencoding()` 为 `cp936`，含中文的路径被写成 GBK，随后以 UTF-8 解释执行而 `SyntaxError`，marker 永不出现。证据：

  ```
  b"Path('H:\\\\\xce\xc4\xb5\xb5\\\\LingNexus\\\\..."
  utf8 fail: 'utf-8' codec can't decode byte 0xce
  gbk ok
  ```

  同一逻辑在 ASCII 路径下可稳定通过。**产品行为正确，不计产品缺陷。**
- **`1 skipped`**：Windows 符号链接权限，既有已知项。
- **首轮 `185 passed, 223 errors`**：仓库 `.venv` 未安装 `pyproject.toml` 声明的 `pypdf / python-docx / openpyxl / python-pptx / tzdata`。属**环境准备问题**；装齐依赖后即为上面的结果。
- **`pip install -e .` 在本机挂起**：build isolation 现编 `setuptools`，15 分钟无输出。`start.bat` 走的是这条路。属**安装体验问题**，不是功能缺陷，但会卡住新机器首次启动。

### 3.2 前端

```
node tests/frontend_behavior.cjs   → all checks passed
node tests/frontend_continuity.cjs → all checks passed
node --check nexus/web/{ling,app,tasks}.js → 语法通过
```

### 3.3 真实浏览器 + 真实模型

```
node tests/continuity_browser.cjs → Continuity browser passed: delegation, ordinary conversation,
                                    durable decision, message while waiting, restart/backfill,
                                    user approval, verified completion, single notification,
                                    draft/history and legacy chat.
node tests/browser_smoke.cjs      → Browser smoke passed: chat/task attachments, six file formats,
                                    original downloads, read_attachment, refresh/restart, images,
                                    approval and offline map.
```

（`browser_smoke.cjs` 需 `PYTHONPATH` 指向仓库根，否则 `attachment_fixtures.py` 报 `No module named 'nexus'`——测试脚本可复现性小问题，不计产品缺陷。）

---

## 4. 真实缺陷（需修复）

严重度：**高** = 造成未授权操作或不可自愈的状态错误；**中** = PRD 明文验收点上的行为错误；**低** = 次要一致性问题。

### A · 高 · 本地 API 无来源校验，任意网页可驱动灵写文件

**违反：** PRD §6.5「超出已有授权范围或涉及重要安全风险的操作，仍遵守安全与授权边界」。

**位置：** `nexus/web/server.py` 全文：`_chat`、`_settings`、`_workspace`、`/api/ling/input`、`/api/tasks` 均无 `Origin` / `Referer` / token 校验；`_read_json` 只看 `Content-Length`，不看 `Content-Type`；完全不校验 `Host`。

**触发：** 浏览器打开任意第三方页面，页面内：

```js
const T = "http://127.0.0.1:8766";
const sim = (p, b) => fetch(T + p, {method:"POST", mode:"no-cors",
  headers:{"Content-Type":"text/plain"}, body: JSON.stringify(b)});
await sim("/api/settings", {permission: "yolo"});            // 降级安全模式
await sim("/api/ling/input", {text: "请用 write_file 创建 …", request_id: "x"});
```

**实测（起点 `permission: "ask"`）：**

| 场景 | 证据 | 结果 |
|---|---|---|
| loopback 页跨源 | `run24.log` | `permission now yolo`；`文件内容 written-by-an-unrelated-web-page` |
| LAN 地址页跨源 | `run26.log` | `文件内容 pwned-from-public-origin` |
| 改工作目录 | `run25.log` | `POST /api/workspace` 200，workspace 变为 `C:\Users`；`run27.log` 变为 `C:\Windows` |
| 伪造 `Host` | `run27.log` | `Host=evil.example` 仍 200（DNS 重绑定可行） |

**影响：** 绕过 `ask` 授权模型。PRD 要求的安全边界只在"用户点确认"这一层，而"请求是否来自用户"从未校验。

**与暂缓项的区别（重要）：** 暂缓的是**沙箱分级**（技术上强制 Agent 能碰什么，属 OS 级隔离设计），本缺陷是**应用层请求来源校验缺失**——两者不是一回事。修它不需要引入沙箱，也不预设"Agent 该被允许碰什么"的边界，因此不受暂缓决定影响。

**性质：** 基线 `b037bea` 已有同类问题，非本轮新增；但本轮 `/api/ling/input` 把攻击面降到"无需任何 id"，且新增 `/api/workspace` 提供了改工作目录的原语。

**修复建议（不扩大范围）：** 两件必须一起做——
1. `Host` 只接受 `127.0.0.1:<port>` / `localhost:<port>`（挡 DNS 重绑定）；
2. 所有写操作要求 `Content-Type: application/json`（挡 `text/plain` 简单请求），并对带 `Origin` 的请求校验本机来源。

不需要 CSRF token、不需要沙箱，不改变任何产品授权语义。

---

### B · 高 · 两条通知同时展开时，批准会落到另一条上

**违反：** PRD §6.5「超出已有授权范围…仍遵守安全与授权边界」。

**位置：** `nexus/web/ling.js:827-854`（`submitNotificationAction` 读取**全局单一** `lingView.notificationDraft`）；`ling.js:785-825`（打开第二面板时不回收第一面板）。

**触发：** 两条待批准通知 A、B → 点 A「处理请求」→ 不收起 → 点 B「处理请求」→ 点 A 面板里的「允许并继续」。

**实测（`run23.log`）：**

```
STEP race {"ran":true,"alphaPanelBefore":1,"alphaPanelNow":1,"clickedIn":"ALPHA row"}
STEP outcome {"A_state":"waiting_user","A_pending":1,
              "B_state":"running","B_pending":0,"files":[]}
STEP feed [["A","pending","unread"],["B","answered","read"]]
```

点的是 A 面板的按钮，被批准的是 B。截图 `acceptance/210-race.png`。

**窗口：** 灵视图约 1s 一次重绘、普通会话视图约 5s（`ling.js:878-891`），属易误触。

**修复建议：** 打开新面板时先回收其他面板；或把 draft 改为按 `notificationId` 的 Map。补一条"两面板同时打开"的回归。

---

### C · 高 · 等待审批时「挂起」，该审批永久不可作答

**违反：** PRD §6.2「通过已有方式暂停和恢复工作时，先前的重要进度能够保留」、§6.5「暂停…不会造成状态混乱」。

**位置：** `nexus/task/store.py:508-512`（`paused` 不处理 `interactions`）、`store.py:554-559`（只有 `waiting_user` 分支同步交互版本）、`store.py:882-885`（作答要求版本严格相等）、`runtime/callbacks.py:100-101`（有 pending 交互即拒绝完成）。

**触发：** 任务处于 `waiting_user` 且有待批 → 点任务页「挂起」→ 再尝试批准。**全部是普通 UI 操作，无需竞态。**

**实测（`run28.log`，真实 Chrome + 真实模型）：**

```
STEP waiting      {"state":"waiting_user","pending":[["int_48dee7d0d2414c5c",4]],"task_cv":4}
STEP after UI pause {"state":"paused","task_cv":5,"pending":[["int_48dee7d0d2414c5c",4,"approval"]]}
STEP answer attempt 409 {"error":"task changed before this answer"}
STEP final {"state":"running","pending":1,"wait_reason":""}
STEP feed [["interaction","pending","expired","unread"],["paused","-","live","unread"]]
```

最终库态：`task running (cv 7)` / `interaction approval, status=pending, cv=4`——永远落后。

**影响：** 任务进入死锁——`interaction_answer` 永远 409；`task_finish` 永远被拒；`task_resolve_operation` 也清不掉；通知显示"expired"但交互仍是 `pending`，**既不给按钮也不消失**；任务可被反复重新启动、每轮撞同一道门。用户唯一出路是删掉整个任务。文字日志见 `run28.log`；现场截图为当时操作记录，未随精简保留（结论可完全由 `run28.log` 与库态核对）。

**同一根因的其它入口（store 层复现，`acceptance/verify_store.txt`）：** 任何使 `control_version` 前进的操作都会造成孤儿——`mark_paused`、`archive`/`unarchive`、`recover_interrupted`。

**修复建议：** `paused` 分支像 `waiting_user` 一样同步 `interactions.control_version`；或把作答校验从"严格相等"放宽为"不早于创建时版本"。前者改动更小。

---

### D · 中 · 审批框提示"或说明"，但带条件的允许被当作拒绝

**违反：** PRD §6.4「工作需要用户决策或授权时，灵能够主动请求处理」。

**位置：** `nexus/web/ling.js:436` 占位符 `"允许 / 拒绝，或说明"`；`ling.js:497` 判定 `/^(allow|yes|允许|同意)$/i` 整串匹配。`tasks.js:212` / `tasks.js:242` 同源。

**实测（`run17.log`）：**

```
STEP approval placeholder 允许 / 拒绝，或说明
STEP file after qualified approval false
STEP transcript after [["tool","The user declined this call."], …]
```

输入「允许，但请先备份原文件。」→ 工具收到 `The user declined this call.`，界面显示「已处理」。

**影响：** 用户以为批准了（还附了条件），实际被**拒绝**，且没有任何"被当作拒绝"的提示。占位符**主动引导**用户写说明，因此是界面引导出的错误。

**修复建议：** 改为明确的「允许 / 拒绝」两个按钮（占位符去掉"或说明"），或按"任一肯定词开头且无否定词"判定。

---

### E · 中 · 暂停期间的 resume 可能丢队列，任务停在 `queued`

**违反：** PRD §6.2 暂停/恢复（触发面窄，但该条款正是指这条路）。

**位置：** `nexus/runtime/controller.py:275-284`；`_enqueue`（93-98）在 `task_id == self._active` 时静默丢弃；`pause()` 等待 8 秒超时后仍返回（250-253），此时 `_active` 未清；`_execute` 的 `finally`（129-134）不再检查队列。

**实测（store 层，`acceptance/verify_store.txt`）：**

```
pause() -> pausing | controller.active: task_…
resume() -> queued | queued: []
after worker exits: queued: [] state: queued -> nobody will run it
```

**影响：** 任务永久 `queued`，没有 Worker 认领，调度器也不处理。需再次手动开始。

**说明：** 本轮浏览器实测未撞上（模型响应快，`pause()` 在 8 秒内完成）；这是**代码可复核 + store 层可复现**的潜在缺陷，复现窗口依赖模型调用耗时。据此定为「中」而非「高」，且标注为**窄触发面**。

**修复建议：** `_execute` 结束时重新检查队列；或 `resume` 时若 `_active` 仍是自己，先登记待续。

---

### F · 中 · 旧授权可在任务目标变更后继续有效（仅 API 路径可达）

**违反：** PRD §6.5「超出已有授权范围…仍遵守安全与授权边界」。

**位置：** `nexus/task/store.py:907-922`（`answered_approval` 只按 `tool + arguments` 匹配，不看版本与目标）。

**实测（`acceptance/verify_store2.txt`）：**

```
answered approval: answered answer: True
goal now: deploy to production | workspace: C:\prod | cv: 5
answered_approval(bash, deploy staging args): True
```

**范围修正（重要）：** 本轮核查确认，**任务页 UI 的 PUT 只改步骤验证状态**（`tasks.js:176-192`），不提供改目标/工作目录的入口；因此该缺陷目前**只能通过直接调用 API 或灵侧 `update_task` 触发**，常规界面操作到不了。据此从"高"下调为"中"。

**修复建议：** `answered_approval` 命中时比对 `control_version` 与目标；目标变更即视为旧授权失效，需重新请求。

---

## 5. 误判与越界项（已撤回，不计缺陷）

### 5.1 撤回：「给挂起任务补要求会自动恢复」不是缺陷

**原判定：** 我此前把「通过任务页补充要求后任务从 `paused` 变为 `running`」列为 P1-2 缺陷。

**为何是误判：** 归档 [PROGRESS.md](PROGRESS.md) 第 28 行明确写着：

> 通过灵修改已挂起工作：保存修改并保持挂起，明确恢复使用 work_control；**原任务页「发送到挂起任务即继续」行为保留**

即这是**所有者已确认要保留的兼容行为**，且明确与"经灵补充要求不自动恢复"分开表述。我复核代码也证实两者确实是分开的：

- **经灵**：`WorkService.message()` 调用 `intake(...)` 时传 `{"resume": False}`（`nexus/ling.py:190-191`）→ **保持挂起，正确**；
- **旧任务页**：`task_api.py:264-266` 在 `paused` 且 `resume` 默认为 `True` 时提交运行 → **发送即继续，即既定行为**。

**我的实测走的是哪条路：** `run16.log` 显示 `STEP amend （POST /api/tasks/{id}/messages，未带 resume）`——直接调用任务页接口，正是被保留的那条路径。因此**实测正确、定性错误**。

**结论：** 该项撤回，不再计为缺陷，也不建议修改 `resume` 默认值（改了反而破坏既定兼容行为）。判断"经灵补充要求不恢复"是否成立，见 §6 的待验证项（未单独实测）。

### 5.2 撤回：「缺沙箱分级」不是缺陷

`docs/参考/暂缓项.md` 一已明确暂缓，理由是"边界还没想清楚时引入沙箱等于用不成熟的理解固化限制"。本报告**不要求**引入沙箱，也不把 `yolo` 无中间档列为缺陷。

**但缺陷 A 不受此影响：** 暂缓的是"Agent 技术上能碰什么"，A 是"HTTP 请求是不是本机用户发出的"。前者需要产品级边界决策，后者只是应用层来源校验。二者不能混为一谈，也不能用暂缓决定豁免来源校验。

### 5.3 撤回：不因缺少下列能力判定失败

| 能力 | 依据 | 处理 |
|---|---|---|
| 多任务真正并行 | PRD §5.2、RESEARCH「任务执行串行」「不能声称同时推进」 | 串行是既定设计，并存/排队已满足；**不计失败** |
| 复杂多会话管理（自动命名/搜索/导出） | 暂缓项 二 | 不要求 |
| LSP | 待定项 一 | 不要求 |
| 插件平台 | 待定项 二 | 不要求；只要求可维护 |
| 计划模式 | 暂缓项 三 | 不要求 |
| 遥测、会话格式版本化、崩溃恢复 | 暂缓项 四 | 不要求 |
| 完整长期记忆、人格演化、自主性 | PRD §5.2、§6.6 末条 | 不属于本轮 |

---

## 6. 环境问题（不计产品缺陷）

| # | 问题 | 性质 | 建议 |
|---|---|---|---|
| 1 | `.venv` 未安装 `pyproject.toml` 声明的文档解析依赖，直接跑测试产生 223 个 error | 环境准备 | 在新机器上记录"先装依赖再跑测试"；测试可加 `importorskip` 或明确报错 |
| 2 | 1 条测试因 Windows `cp936` 默认编码失败 | 测试自身缺陷 | 该测试显式 `encoding="utf-8"` |
| 3 | `pip install -e .` 因 build isolation 现编 `setuptools` 长时间无输出 | 安装体验 | 可考虑固定 `--only-binary` 或提供锁文件；属工程改进，非功能缺陷 |
| 4 | `browser_smoke.cjs` 需外部设置 `PYTHONPATH` | 测试脚本可复现性 | 脚本内自行设置 |
| 5 | 本机 `%LOCALAPPDATA%\Nexus\config.json` 的 `workspace` 值末尾多一个引号（`H:\\文档\\LingNexus\\Nexus"`），且为 `permission: yolo` | **本机错误配置** | 建议产品在启动时校验工作目录并给出明确提示（属易用性建议，非本轮缺陷）。见 §8 风险说明 |
| 6 | 交付记录写 `407 passed`，本机复现为 `406 passed, 1 failed, 1 skipped` | 环境差异 + 测试编码问题 | 交付记录应注明 Python/编码环境；**不是造假** |

---

## 7. 待验证风险（未验证，不得视为失败）

| # | 场景 | 状态 |
|---|---|---|
| 1 | 数小时级长任务的稳定性与交流体验（PRD §2.4 的"耗时数小时"） | **未验证**——本轮最长单任务约 8 分钟 |
| 2 | 模型自认受阻（`blocked`）时主动说明的真实质量 | **未验证**——代码路径存在，真实模型未自然触发 |
| 3 | 经灵补充要求「保持挂起」的独立实测 | **未单独实测**——代码路径为 `resume: False`，语义正确，但本轮未单独跑一条专门的浏览器用例（`run16.log` 走的是旧任务页路径） |
| 4 | 断电/强制终止时外部副作用的一致性（"恰好一次"） | **未验证**——只验证了重启后标记 `interrupted` 且不重放 |
| 5 | 前端：提交在途时新输入被清空 | **未复现**（`run17.log` 中后输入文字保留），按风险处理 |
| 6 | 前端：提交在途切换视图可能串视图 | 代码可复核，未构造稳定复现 |
| 7 | 通知列表每秒重绘导致滚动位置复位 | **未实测**（需展开且通知较多） |
| 8 | `ask` 模式下 `bash` 类命令的审批展示是否足够 | 只验证了 `write_file` |
| 9 | 跨进程并发（同机开两个实例）下的进度写保护 | **未验证** |
| 10 | 第三方 MCP / Skill / 各网关组合 | **未验证** |

### 7.1 任务状态机一致性的风险（保留说明，不扩大范围）

以下在 store 层可复现，属于**状态约束不严**而非阻塞性缺陷。列出来是为了说明"已完成/已取消"并非硬性终态，但**不据此要求新增产品限制**：

| 现象 | 证据 | 说明 |
|---|---|---|
| 已完成任务仍可 `update_progress`，可再次 `command("completed")`，并产生两条 `completed` 通知 + 一条 `blocked` | `verify_store.txt` | 需同时具备 API 访问与过期版本，常规 UI 不触发；建议 `completed`/`cancelled` 拒绝再写与再完成 |
| 已取消任务可被 `mark_completed` 改为完成 | `verify_store2.txt` | 同上 |
| 交互回答在模型 transcript 中被移到末尾 | `verify_store.txt` | 顺序错误，可能影响模型因果理解；建议按存储顺序输出 |
| `claim_schedule` 不校验 `version`，旧 tick 可吞掉新排期 | `verify_store.txt` | 窗口极小，建议加版本比对 |
| `waiting_user` 任务因定时排期每秒重循环写事件 | `verify_store.txt` | 约 8.6 万行/天；只在"定时到期 + 同时在等审批"时发生 |
| 每轮正常结束写一条「paused」通知 | `acceptance/` 真实运行中观察到 2 条 | 通知噪音，影响 §6.4「不反复打断」的观感，但不构成功能失败 |

### 7.2 删除任务与操作证据（保留风险，不建议新增限制）

**现象：** 任务存在未解决的未知操作时，`DELETE /api/tasks/{id}` 会级联删除 `operations` 与 `events`（`acceptance/verify_store2.txt`：`unfinished before: 1` → `operations rows left: 0`）。

**影响：** 最需要事后核查的外部副作用记录，会随任务一起消失。

**立场：** 这是**真实的数据风险**，但**不构成要求增设"禁止删除"产品限制的理由**。删除是用户明确发起的操作，PRD 没有相关要求，`docs/参考/暂缓项.md` 一也提示了 `yolo` 下完全放手的代价。建议仅记录，交由所有者决定是否需要在删除前提示"该任务仍有未确认操作，删除后无法核查"，**不擅自增加限制**。另有一个与本项性质相近但独立的风险：

| 风险 | 现象 | 建议 |
|---|---|---|
| `jobs` 以 `job_001` 为全局主键 | 两个任务的作业记录互相覆盖（`verify_store2.txt`） | 属审计视图错误，建议主键加 `task_id` 前缀 |

---

## 8. 遗留风险

| 风险 | 说明 |
|---|---|
| 本机配置错误 | `config.json` 的 `workspace` 末尾多一个引号且为 `yolo`。任何相对路径工具调用报 `WinError 123`；灵会改用**绝对路径重试**（`run7.log`：先失败、再请求绝对路径写、被拒）。这是**本机配置问题**，不列为产品缺陷；建议产品启动时校验并提示 |
| 服务仅绑定 `127.0.0.1` | 本身正确；风险来自同机浏览器的跨源请求（缺陷 A）与 DNS 重绑定 |
| 「全绿即健康」的错觉 | 406 项通过，但本报告的高危项全部落在未覆盖路径上——这是本轮最值得记住的一条 |
| 交付记录数字不可复现 | `407 passed` 依赖特定环境（依赖齐全 + UTF-8 默认编码），后续报告应注明环境 |

---

## 9. 修复建议（按优先级，不扩大交付范围）

1. **修 A**（来源校验）：`Host` 白名单 + 写操作要求 JSON Content-Type + `Origin` 校验。不需要沙箱、不需要 CSRF 框架，不改变产品授权语义。
2. **修 B**（双面板批准错位）：打开新面板先回收其他面板，或 draft 改为按通知 id 的 Map。
3. **修 C**（挂起后审批失效）：`paused` 分支同步 `interactions.control_version`。
4. **修 D**（审批语义）：改成两个明确按钮。
5. **修 E**（resume 丢队列）：`_execute` 结束时重新检查队列。
6. **修 F**（旧授权跨目标）：`answered_approval` 比对版本与目标。
7. **修状态机约束**（§7.1）：至少让 `completed`/`cancelled` 拒绝再写与再完成。
8. **补测试**，覆盖真正会坏的路径：挂起后再作答、双面板批准、已完成后再写、暂停-恢复丢队列、跨目标授权。
9. **环境与测试可复现性**：测试显式编码；新机器先装依赖；交付记录注明环境。
10. **不要做的事**：不要为了"通过"而把旧任务页「发送即继续」改掉（那是既定行为）；不要为未确认的删除风险擅自加禁止删除的限制；不要因缺沙箱分级、并行、多会话、LSP、插件而判失败。

---

**最终结论：未通过（需修复后复验）。** 持续性核心体验（§6.1、§6.3）已真实成立并可交付观察；但 §6.4 / §6.5 / §6.2 存在 1 项高优先级安全问题（A）、2 项高优先级授权或状态问题（B、C），以及 1 项 PRD 明文验收点上的行为错误（D），必须在阶段收口前修复。修复后至少复现 A–F 六个场景，并纳入 `tests/` 自动化——它们目前都不在回归覆盖里。

---

## 10. 修订记录

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-10-20 初版 | 建立验收报告：3 个 P0、多个 P1/P2 | 本轮实测 |
| 2026-10-20 第一次修订 | 按用户要求精简：删除未引用的截图与全部临时驱动脚本，保留报告引用到的证据 | — |
| 2026-10-20 第二次修订（本版） | 按归档 PRD / RESEARCH / PROGRESS、`AGENTS.md`、`docs/参考/` 重新定性，见下 | 用户明确指出 |

**第二次修订明细：**

| 项目 | 处理 | 原因 |
|---|---|---|
| 「给挂起任务补要求自动恢复」 | **撤回**（原 P1-2 → 误判） | PROGRESS 第 28 行明确保留旧任务页「发送即继续」；经灵路径用 `resume: False`，正确 |
| 「缺沙箱分级」 | **撤回为明确暂缓** | 暂缓项 一；但缺陷 A（来源校验）与沙箱无关，保留为高 |
| 「多任务未并行」 | **撤回为不需要** | PRD §5.2、RESEARCH：串行是既定设计 |
| 「测试 1 failed」 | **改判为环境问题** | 测试自身未指定编码；依赖缺失亦为环境问题 |
| 「数小时稳定性」 | **改判为未验证** | 本轮最长 8 分钟，属未覆盖 |
| 「删除任务丢失操作证据」 | **降为风险说明**，不要求新增禁止删除限制 | 用户明确要求保留风险但不擅自加限制 |
| 「已完成仍可写 / 取消后被标记为完成 / 大量通知」 | 从"P1/P2 缺陷"降为 **§7.1 状态约束风险** | 需 API 访问或过期版本，常规 UI 不触发；属约束不严而非阻塞 |
| 缺陷 A | 保留 **高**，并说明与沙箱暂缓的关系 | 已实测四种路径 |
| 缺陷 B | 保留 **高** | 已实测，点 A 批 B |
| 缺陷 C | 保留 **高** | 已实测，真实 UI 即可触发 |
| 缺陷 D | 保留 **中** | 已实测，PRD §6.4 验收点 |
| 缺陷 E | 由"P1"调为 **中**，标注窄触发面 | store 层可复现，浏览器未撞上 |
| 缺陷 F | 由"P1"调为 **中**，限制在 API 路径 | 任务页 UI 的 PUT 只改步骤，改不了目标 |
| 「经灵补充要求保持挂起」 | 改判为 **待验证**（§7 第 3 项） | 代码语义正确，但本轮未单独实测该路径 |
