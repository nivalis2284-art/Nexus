# 全量验收与缺陷分级 · 2026-10-22（第二轮）

更新：2026-10-22。本轮为**只读全量验收**，6 个子代理按模块并行审查，主代理复核并逐条验证关键结论。结论按“现在修 / 暂时不管”两类给出；未修改任何源码。

## 本次复核与修复（2026-10-22）

以下为收到报告后的修复记录；下方原始审查内容保留，便于对照。原报告中的历史测试数量与失败次数不作为本次重新实测的结论。

- 第一部分的问题已逐项按源码核实并进行针对性修复；具体实现不机械采用原报告给出的建议。
- P1-1：本机并发测试确认问题存在。随机临时文件加短重试单独使用仍失败，因此配置读写共用进程内锁，会话读取复用现有写锁，同时使用独立临时文件、失败清理与有限的 Windows 占用重试。未增加后台重试服务。
- P1-2：开始／恢复任务时直接检查是否仍有待处理交互，暂停不能绕过；页面同步隐藏对应开始按钮。
- P1-3：每次重定向在访问下一目标前执行现有域名及私有地址检查；仅检查最终响应不能阻止已经发出的内网请求。
- P1-4：读取正文预算低于工具调度器上限并预留提示空间，分段读取给出续读位置；grep 的扫描文件大小上限独立保留，避免跟着读取展示预算缩小。
- P2-1：glob 按路径段匹配，`*` 不跨目录、`**` 可匹配零层或多层目录；匹配相对查询目录，返回工作区相对路径。grep 指定文件只搜索该文件。
- P2-2：命令解析同时支持单双引号、参数内部带引号和 Windows 双引号前的反斜杠，保留带空格路径与空参数；不调用额外 shell。
- P2-3：输出先尝试 UTF-8，失败后采用本地编码／Windows 控制台代码页；UTF-8 控制台不阻止系统编码回退。本机实际系统编码是 cp1252、控制台是 UTF-8，原报告“当前是中文系统代码页”的推断不成立；GBK 场景用受控编码测试验证。
- P2-4：后台刷新与导航分开，轮询不收起定时面板；任务步骤未变化时不重建验证下拉框。
- P2-5：灵发送成功、失败及外层表单清空均检查当前视图，切换后不覆盖另一页面的消息和草稿。
- P2-6：原报告“只有明确不是控制指令才返回 true”的修法写反；正确行为是普通消息返回 false 继续聊天，真实错误保留草稿并提示，不能当成处理成功。
- P3-1：队列读取错误保留为可见错误，并等待后继续；结束输入的状态保存失败也会释放执行占用，不重放已执行的工作。
- P3-2：交互创建在事务内取得版本；显式携带旧版本的写入会被拒绝，已暂停任务的旧运行不能创建新审批，不把过期运行强行套上新版本。
- P3-3：每次进入等待生成独立请求编号，第二次等待仍会真正改变状态，并保留后台作业。
- P3-4：人设正文与现有 Skill 解析采用一致的 UTF-8 容错读取，避免页面和灵都无法读取该条目。
- P3-5：交付失败仍推进监听检查时间并保存错误，保留旧基线以便下一次重试交付变化。
- P3-6：只限制发给灵的前后正文片段，各 12,000 字符并标注截断；完整网页仍用于变化比较与基线保存。
- 第二部分保持记录，未将其中的兼容设计、恢复机制、统计与产品取舍扩大为本轮任务。任务视图中与轮询同源的展示问题可随第一部分修正。

本次验证使用针对性 Python 测试、模拟模型与本地 HTTP 请求，以及前端假 DOM 行为测试；不进行真实模型全量验收，不重启日常实例。

上轮修复时的验证记录（未覆盖下述追查场景）：

- 存储、会话、灵、人设、任务、交互、长期交代、结果反馈、网页监听和架构的整合回归：179 项通过。
- 文件读取／搜索、命令工具、后台作业及架构回归：95 项通过。包括真实本地 `cmd` 中文输出与受控 GBK 字节输出。子代理独立复核任务／交互相关测试 140 项通过，各组之间存在重叠，不累加为独立用例数量。
- 前端 `frontend_continuity.cjs` 与 `frontend_behavior.cjs` 共 44 项通过，包含本次新增的三组行为回归。
- 静态检查与 Python 编译检查通过。没有重复运行三轮全量测试，也未使用真实付费模型。

## 用户追查后的补修（2026-10-23）

上轮“第一部分均已修复”的结论不够完整：命令输出与视图守卫各引入一处回归，文件续读和非 UTF-8 人设处理各有遗漏。具体以本节后续复核为准，而不能用上轮通过的测试数量代替这些场景的验证。

- shell 输出：单个坏字节或截断字节确实会触发整段回退，本地代码页可能把其余正确中文也变成乱码。改为逐行处理，保留成功解码的 UTF-8 中文，仅在缺少 UTF-8 依据时比较本地解码结果。编码推断仍是启发式，不宣称能识别任意混合编码。
- read_file：行数上限确实没有续读 offset；工具调度器也确实无条件覆盖了工具自报的 `truncated`。现在行数限制给出下一偏移，字节／行内截断均标记，调度器合并自身和工具的截断标记。最后一页未裁剪时仍为 false。
- 灵视图：发送期间离开再回到同一灵会话，旧视图版本会使回滚跳过，确实留下幽灵消息。发送结果现在按“是否正在显示同一灵会话”同步，立即回滚消息、恢复未被新草稿占用的输入并显示错误；仍在其他视图时不覆盖该视图。没有仅依靠清空签名等待轮询。
- 人设文件：UTF-8 替换读取确实损失 GBK 正文，保存旧文件又用严格 UTF-8 读取，形成无法页面自救的闭环。人设与 Skill 的读取、管理和保存现共用严格 UTF-8／GBK 解码；正常读取不改原文件，明确保存后转 UTF-8，保留 `when` 和自定义元数据。两种编码都不能解码时明确返回错误、原文件保持不变，需要先在本地修正文件编码，不默默用替换符改写。
- 已完成文件／命令工具、后台作业及架构针对性回归：100 项通过，包括 UTF-8 中文混一个坏字节、多字节末尾截断、混合编码分行输出及行数截断。
- 人设／Skill／HTTP／运行时与队列相关针对性回归：40 项通过；另直接检查 GBK 读取后原字节不变、页面 PUT 保存成功并保留额外元数据，以及不可解码文件报错且不覆盖。
- 前端两组行为测试共 46 项通过，新增离开后返回灵的失败恢复和新草稿保留检查；人设前端另有 10 项通过。子代理的人设／Skill 相关回归 86 项通过，与上述 Python 测试有重叠，不累加。静态 F 规则检查与编译检查通过。未进行真实模型或浏览器全量验收。

## 验收依据

| 项目 | 结果 |
| --- | --- |
| 自动化测试 | 570 passed / 1 skipped（两次全量运行各随机挂 1 个，原因见 P1-1） |
| `ruff check nexus services/scheduler/src --select F` | 全部通过 |
| `compileall` | 通过 |
| 真实启动 | 服务正常启动；`/`、`/app.js` 与 9 个主要 API 全部 200 |
| 静态文件路径穿越 | `/../`、`%2e%2e`、`..%2f`、`C:/Windows/win.ini` 全部 404 |
| 任务 API | 创建 201 → 列表（中文正确）→ 详情 → 删除，全通 |
| 前端脚本测试 | 环境无 `node`，由子代理借 Electron 的 Node 跑通 4 份 `.cjs` 基线 |

**总体判断**：实现质量良好偏上——分层清晰、工具装配清单驱动、写入普遍走“临时文件 + fsync + replace”、参数校验严格、有架构守卫测试。没有“当前配置下必然崩”的致命缺陷；最严重的一条是可稳定复现的写入竞态，其次是若干让模型判断出错的工具契约问题。

---

# 一、现在就修

## P1 严重

### P1-1 配置与会话文件写入竞态：固定 `.tmp` + `os.replace` 撞上并发读取

**位置**：[`nexus/core/config.py:197-201`](../../../../nexus/core/config.py#L197-L201)（`write_config`）、[`nexus/core/config.py:212-222`](../../../../nexus/core/config.py#L212-L222)（`write_credential`）、[`nexus/session/store.py:204-220`](../../../../nexus/session/store.py#L204-L220)（`_rewrite`）

**现象**：临时文件名固定为 `config.tmp` / `<session>.tmp`，随后 `os.replace`。CPython 打开文件不带 `FILE_SHARE_DELETE`，Windows 上目标文件只要被任何线程以读方式打开，替换就抛 `WinError 5`。而 `read_config()` 几乎每个请求都会调用，`SessionStore.load()` / `list()` 也不持锁。

**实测**：2 个读线程 + 300 次保存 → **262/300 失败**并残留 `config.tmp`；会话重命名 **270/300 失败**。

**影响**：保存设置、重命名会话、改 MCP 配置会随机 500；`sessions/` 目录残留 `.tmp`。**这也是测试套件每次随机挂 1 个的原因**——两次全量运行挂的是不同测试，报错都是 `config.tmp -> config.json` 的 `PermissionError`。

**修法**：临时文件名加随机后缀（`tempfile.mkstemp` 或 `uuid`），替换失败时短暂重试；读侧可选加重试。可参照同仓 [`nexus/task/store.py:942-974`](../../../../nexus/task/store.py#L942-L974) `request_confirmation` 在单事务内完成读写的写法。

**验证**：并发读写脚本 300 次零失败；`pytest tests -q` 连续 3 次全绿。

### P1-2 任务「等待你处理」可被“挂起 → 开始”绕过，且不需要竞态

**位置**：[`nexus/runtime/controller.py:84-91`](../../../../nexus/runtime/controller.py#L84-L91)（守卫）、[`nexus/task/store.py:589-593`](../../../../nexus/task/store.py#L589-L593)（`waiting_user` 可迁入 `paused`）、[`nexus/task/store.py:578-583`](../../../../nexus/task/store.py#L578-L583)（`paused` 可直接 `queued`）

**现象**：守卫只在 `state == "waiting_user"` 时检查 pending 交互；但 `waiting_user → paused` 合法、`paused → queued` 也合法。

**影响**：任务在用户未作答审批的情况下开跑。模型重新发起同一工具调用时匹配不到已答记录，会**再建一条相同审批**；此后 `task_finish` 因“仍有 pending 交互”被拒，用户面对两张一样的审批卡。**两次普通点击即可触发**，不需要竞态。

**修法**：把守卫从“状态是 waiting_user”改为“存在 pending 交互就拒绝 start/resume”，即 `submit` 里无条件检查 `pending_interactions(task_id)`。

**验证**：新增用例——建待答审批 → pause → submit，应仍被拒绝。

### P1-3 `webfetch` 只校验首跳，不复查重定向（SSRF 绕过）

**位置**：[`nexus/tools/web.py:128-136`](../../../../nexus/tools/web.py#L128-L136)

**现象**：白名单与私有地址检查只作用于初始 hostname，随后 `urllib.request.urlopen` 默认跟随 302/303/307，`final_url` 只拼进输出、从不复查。

**影响**：白名单站点上的任意开放重定向即可打到 `127.0.0.1:port` 或 `169.254.169.254`，正文照返给模型。需开启网络工具（`webfetch_hosts`）才注册，但一旦开启这条控制可被绕过。

**修法**：跟随重定向后（或自定义 opener 禁止自动跟随）用 `final_url` 重新做 `_host_allowed` + `_is_private` 校验，不通过则丢弃响应。

**验证**：本地起一个 302 指向 `127.0.0.1` 的服务，调用应被拒绝而非返回正文。

### P1-4 `read_file` 静默丢中段：400 KB 预算被 40 KB 裁剪截断

**位置**：[`nexus/tools/files.py:21-22`](../../../../nexus/tools/files.py#L21-L22)（`MAX_READ_LINES=2000`、`MAX_READ_BYTES=400_000`）、[`nexus/tools/files.py:93-129`](../../../../nexus/tools/files.py#L93-L129)（`_read_text`）、[`nexus/tools/registry.py:23-25`](../../../../nexus/tools/registry.py#L23-L25)（`MAX_RESULT_CHARS=40_000`，头 24k + 尾 14k）

**现象**：只有超过 400 KB 才追加“continue with offset N”提示，但结果回到调度器就被裁到 40 KB。

**实测**（80 KB / 1200 行文件）：`content` 只有 38040 字符、`returned: 1200`、`lines: 1200`、**没有**续读提示；`fn_600` 不可见、`fn_1199` 可见。

**影响**：40 KB–400 KB 的普通源文件，模型以为读完了，实际中段整段消失 → 重复添加已有函数、漏改。这是本轮**最容易在日常编码中造成实际错误**的一条。

**修法**：让 `_read_text` 的预算与 `MAX_RESULT_CHARS` 对齐（例如都取 40 KB 减去头部余量），保证一旦被裁剪就一定输出续读偏移。

**验证**：读 80 KB 文件应返回续读提示且 `returned < lines`；按偏移续读能取到第 600 行。

## P2 常见问题

### P2-1 `glob` 语义双向错误，`grep` 传文件路径却搜索父目录

**位置**：[`nexus/tools/files.py:297-312`](../../../../nexus/tools/files.py#L297-L312)（`glob_paths`）、[`nexus/tools/files.py:324`](../../../../nexus/tools/files.py#L324)（`_walk(base if base.is_dir() else base.parent)`）

**现象**：`fnmatch` 不把 `/` 当分隔符，且代码同时尝试“文件名”和“相对路径”两个候选。

**实测**：`glob **/*.py` → 只返回 `sub/inner.py`（漏掉顶层 `top.py`、`big.py`）；`glob *.py` → 反而递归返回 `sub/inner.py`；`grep(path="top.py", pattern="inner")` → 返回 `sub/inner.py:1: print('inner')`。

**影响**：模型据此判断“某文件不存在”或“某内容属于这个文件”，是最容易引发错误结论的一类。`**/*.py` 正是工具描述里的示例写法。

**修法**：`glob` 用 `pathlib.PurePath.match` / 自行按段匹配，并统一以工作区相对路径为候选；`grep` 在 `path` 是文件时只搜该文件。

**验证**：上述三条实测应分别得到“含顶层文件”“不递归”“只命中 top.py”。

### P2-2 `shell` 单引号分词会静默执行成另一条命令

**位置**：[`nexus/tools/shell.py:45-53`](../../../../nexus/tools/shell.py#L45-L53)

**现象**：`shlex.split(command, posix=False)` 后只剥成对双引号，单引号原样保留。

**影响**：`python -c 'print(1)'` 变成参数 `"'print(1)'"`，Python 收到字符串字面量 → **exit 0、无输出**，模型会把“没有输出”当成事实。同类：`--target="C:\Program Files\x"` 被拆成两个参数。

**修法**：改为按 Windows 命令行规则分词（`posix=False` 且同时处理单双引号），或在参数解析后对单引号做对称剥离；宁可报错也不要静默变形。

**验证**：`python -c 'print(1)'` 应输出 `1`；含空格路径应作为单个参数。

### P2-3 `shell` 固定按 UTF-8 解码子进程输出，中文 Windows 上乱码

**位置**：[`nexus/tools/shell.py:76-77`](../../../../nexus/tools/shell.py#L76-L77)

**现象**：`stdout/stderr` 一律 `decode("utf-8", errors="replace")`。

**影响**：`dir`、`ping`、`chkdsk` 等原生命令按 ACP/OEMCP（中文环境 936）输出，非 ASCII 全部变 `�`，中文路径与本地化错误信息丢失。项目本身就在中文 Windows 上开发，属于日常可遇。

**修法**：按子进程实际代码页解码（优先 `locale.getpreferredencoding()` / `chcp` 探测），UTF-8 失败时回退。

**验证**：执行输出中文的 cmd 命令，内容应可读。

### P2-4 前端：任务视图里「定时与监听」面板每 1.5 秒被关掉

**位置**：[`nexus/web/tasks.js:56-60`](../../../../nexus/web/tasks.js#L56-L60)（`openTask` 内 `automationOpen(false)`）、[`nexus/web/tasks.js:356-370`](../../../../nexus/web/tasks.js#L356-L370)（`armTaskPolling` 每 1500 ms 调 `openTask`）

**现象**：轮询复用了“进入任务视图”的 `openTask`，其中的导航副作用被反复执行。

**影响**：打开任意任务后点“定时与监听”，面板闪现后 ≤1.5 s 自动收起，任务视图里无法管理定时/监听。同类副作用还有 `clearResponsibilitiesView()`（[:67](../../../../nexus/web/tasks.js#L67)）。

**修法**：把轮询路径与导航路径分开——轮询只更新内容，不执行 `automationOpen(false)` / `clearResponsibilitiesView()`。

**验证**：任务视图打开定时面板，等待 3 秒仍保持展开。

### P2-5 前端：灵的发送结束后无条件覆盖当前视图（切走后串台）

**位置**：[`nexus/web/ling.js:335-341`](../../../../nexus/web/ling.js#L335-L341)（成功）、[`nexus/web/ling.js:347-353`](../../../../nexus/web/ling.js#L347-L353)（失败）

**现象**：`await` 之后直接 `state.items = lingView.items; renderTranscript();`，既不检查 `lingView.active`，也不检查 `state.viewVersion`。对照 [`nexus/web/app.js:527-528`](../../../../nexus/web/app.js#L527-L528) 的旧会话路径是有守卫的。

**影响**：在灵里带附件发送（上传耗时）期间切到别的会话或任务，等待结束后当前 transcript 被灵的对话替换；`state.items` 与 `state.sessionId` 不一致，之后该会话发送还会把消息 push 进灵的内存数组。服务端数据未丢，刷新可恢复。

**修法**：回写前比对 `state.viewVersion` 与 `lingView.active`，与 `openSession` 的守卫保持一致。

**验证**：发送后立即切视图，当前视图内容不应被替换。

### P2-6 前端：`/api/task-control` 异常会丢消息并清空输入框

**位置**：[`nexus/web/tasks.js:381-387`](../../../../nexus/web/tasks.js#L381-L387)、[`nexus/web/app.js:1330-1335`](../../../../nexus/web/app.js#L1330-L1335)

**现象**：普通聊天每条消息都先走 `/api/task-control`；catch 里只有文案匹配“不是控制指令”才返回 `false`，其余（500、网络失败、任务已删 404）都 `return true`，于是消息既不进 task-control 也不进 `/api/chat`，输入框被清空。

**影响**：一次请求失败即丢失用户刚输入的整条消息，且提示随后会被覆盖。

**修法**：只有明确“不是控制指令”才 `return false` 继续聊天；其余错误应保留草稿并提示。

**验证**：模拟 `/api/task-control` 返回 500，消息应保留在输入框。

## P3 触发低频、但后果不可恢复且修复极便宜（顺手修）

| # | 问题 | 位置 | 后果 | 修法 |
| --- | --- | --- | --- | --- |
| P3-1 | 灵输入队列线程会被一次存储异常永久杀死 | [`nexus/ling.py:305-318`](../../../../nexus/ling.py#L305-L318) | `_next()` 在 try 之外，一次 `OperationalError` 就让 `nexus-ling` 线程退出且无重启；之后输入永远停在“排队中”，`last_error` 为空 | `_next()` 包 try/except，异常时短暂退避重试 |
| P3-2 | 审批/提问用过期 `control_version` 落库，永久不可作答 | [`nexus/runtime/callbacks.py:415-418`](../../../../nexus/runtime/callbacks.py#L415-L418)、[:428-432](../../../../nexus/runtime/callbacks.py#L428-L432)、[`nexus/task/store.py:922-940`](../../../../nexus/task/store.py#L922-L940)、[:994](../../../../nexus/task/store.py#L994) | 版本号在事务外先读再传参；亚毫秒窗口撞上暂停/取消后，该审批永远 409，`task_finish` 永远被拒，只能取消再 reopen | 把版本号读取移进 `interaction_create` 的同一事务（与 `request_confirmation` 一致） |
| P3-3 | 挂起用固定的 `wait-{task_id}` 作 request_id | [`nexus/runtime/assembly.py:303-305`](../../../../nexus/runtime/assembly.py#L303-L305)、[:369-384](../../../../nexus/runtime/assembly.py#L369-L384) | 第二次挂起是空操作（状态仍 running），`finally` 因此判定为非等待 → `close_all()` **杀掉仍在运行的后台命令** | request_id 加自增序号，或改用 interaction id |
| P3-4 | 人设 `SKILL.md` 非 UTF-8 时灵每次请求都失败 | [`nexus/persona.py:94-96`](../../../../nexus/persona.py#L94-L96) | 第 94 行用容错读取，第 96 行又严格 `read_text(encoding="utf-8")`；GBK 保存的条目会让 `before_model` 抛 `UnicodeDecodeError`，灵不回复、`/api/persona` 500，页面上也无法修 | 第 96 行加 `errors="replace"`，与 `skills._read_skill_file` 一致 |
| P3-5 | 网页监听回调失败时不推进 `next_check_at` | [`services/scheduler/src/nexus_scheduler/runtime.py:187-190`](../../../../services/scheduler/src/nexus_scheduler/runtime.py#L187-L190) | 交付失败直接 `return`，跳过 `record_watch`；调度循环 1 s 一次 → 持续重读目标站点，且 `error` 仍为空，界面看不出异常 | 失败时也推进 `next_check_at` 并记录错误 |
| P3-6 | 网页正文无长度上限进入唤醒载荷 | [`services/scheduler/src/nexus_scheduler/webpage.py`](../../../../services/scheduler/src/nexus_scheduler/webpage.py)（`_normalize_text`）→ [`automation/runtime.py:157-174`](../../../../nexus/automation/runtime.py#L157-L174) | 大页面变化时整页正文进 system 提示，可能超上下文导致该 run 失败；而基线已推进，用户收不到汇报 | 对 `before/after` 截断到固定长度 |

---

# 二、暂时不管

## A. 低频触发，且失败可见或可恢复

| 问题 | 位置 | 不修的理由 |
| --- | --- | --- |
| 终态可互相覆盖（`cancelled → completed`、`completed → paused` 等） | [`nexus/task/store.py:649-686`](../../../../nexus/task/store.py#L649-L686)、[:614-617](../../../../nexus/task/store.py#L614-L617) | 前端已隐藏这些按钮，仅直接调 API 可达；无真实使用路径 |
| `claim_schedule` 不校验排期版本 | [`nexus/task/store.py:1520-1554`](../../../../nexus/task/store.py#L1520-L1554) | 窗口在一次 tick 的两条语句之间，毫秒级 |
| MCP 工具表在事件循环线程改写、agent 线程同时遍历 | [`nexus/mcp.py:240-254`](../../../../nexus/mcp.py#L240-L254)、[:490-499](../../../../nexus/mcp.py#L490-L499) | 需服务器主动发 `tools/list_changed` 且恰逢构建工具集；窗口极小 |
| 调度 MCP 子进程退出后不自动重建连接 | [`nexus/automation/service.py:38-53`](../../../../nexus/automation/service.py#L38-L53) | 页面有错误文案，设置页可手动重连；自动重连需要额外生命周期设计 |
| 非 `ValueError`/`OSError` 异常导致“零响应” | [`nexus/web/server.py:830-833`](../../../../nexus/web/server.py#L830-L833) | 自带前端不会发出非法类型；属对外接口健壮性，等真有外部调用方再补 |
| 未消费的请求体污染 keep-alive | [`nexus/web/server.py:252-253`](../../../../nexus/web/server.py#L252-L253) | 自带前端不发 DELETE body；另一触发条件是 >57 MB 的单条消息 |
| 已发 200 后再写一个 500（chunked 流） | [`nexus/web/server.py:1068-1096`](../../../../nexus/web/server.py#L1068-L1096) | 只在会话落盘失败时出现，错误已可见 |
| `RUNNING["session_id"]` 设置晚于 200 头 | [`nexus/web/server.py:1114`](../../../../nexus/web/server.py#L1114) | 毫秒级窗口，未能稳定复现 |
| `SessionStore.load()` 把 `OSError` 吞成 `None` → 会话被当成 404 | [`nexus/session/store.py:129-132`](../../../../nexus/session/store.py#L129-L132) | 只在读盘异常时误报；修 P1-1 后概率进一步降低 |

## B. 无关紧要（体验瑕疵，不影响正确性）

| 问题 | 位置 | 说明 |
| --- | --- | --- |
| 会话标题的悬浮提示被覆盖 | [`nexus/web/app.js:126-127`](../../../../nexus/web/app.js#L126-L127) | 连续两次赋值 `label.title`，第二行覆盖第一行；只是看不到完整标题 |
| 任务视图底部统计显示上一个会话的轮数/token | [`nexus/web/app.js:499-506`](../../../../nexus/web/app.js#L499-L506) | 纯展示 |
| 删除会话失败无提示 | [`nexus/web/app.js:136-147`](../../../../nexus/web/app.js#L136-L147) | 未处理的 promise rejection，按钮看起来失效 |
| 任务“验证”下拉框每 1.5 s 被重建 | [`nexus/web/tasks.js:91`](../../../../nexus/web/tasks.js#L91) | 需在 1.5 s 内选完；与 P2-4 同源，修 P2-4 时可一并缓解 |
| 排队中的灵消息在下一次轮询从记录里消失 | [`nexus/web/ling.js:171-186`](../../../../nexus/web/ling.js#L171-L186) | 服务端写入后重新出现；需前后端合并乐观消息才能彻底解决 |
| 多步轮次的 usage 只上报最后一步 | [`nexus/core/agent.py`](../../../../nexus/core/agent.py)（`result.usage = {}` 每步重置） | 实时面板与刷新后数值不同，属统计口径 |
| 唤醒材料在同一请求里注入两次 | [`nexus/web/server.py:1170-1171`](../../../../nexus/web/server.py#L1170-L1171) 与 [:1217-1230](../../../../nexus/web/server.py#L1217-L1230) | 只多花 token |
| 子代理在 `tool_start` 时清空已写文本 | [`nexus/core/subagent.py:111-118`](../../../../nexus/core/subagent.py#L111-L118) | 取决于模型是否“先写报告再调工具” |

## C. 修复成本大于收益

| 问题 | 位置 | 理由 |
| --- | --- | --- |
| Anthropic 开启 thinking 却不回传 thinking 块 | [`nexus/core/effort.py:86-93`](../../../../nexus/core/effort.py#L86-L93)、[`nexus/core/thinking.py`](../../../../nexus/core/thinking.py)、[`nexus/core/protocol.py:145-183`](../../../../nexus/core/protocol.py#L145-L183) | 需在解析器保留签名、在 agent 与协议层原样回传，涉及三层改动；且**当前实例是 `openai_responses` + `thinking: false` + `effort: "off"`，不受影响**。等真正切到 Anthropic 端点时与下一条一起做 |
| 裁剪后首条消息可能是 assistant | [`nexus/core/prompt.py:150-164`](../../../../nexus/core/prompt.py#L150-L164) | 只有 Anthropic 校验“首条必须是 user”；OpenAI 系不校验，当前配置不受影响。修法简单但应随 Anthropic 一起验证 |
| `effort` 把 `max_tokens` 抬到超过模型声明上限 | [`nexus/core/effort.py:90-93`](../../../../nexus/core/effort.py#L90-L93) | 只在 high/xhigh/max 且模型输出上限紧时超窗；现有测试 [`tests/test_config_effort.py:109-117`](../../../../tests/test_config_effort.py#L109-L117) 明确断言了这个行为，说明是当时的取舍。要改需重新设计“thinking 预算与 max_tokens”的关系，属设计变更 |
| 压缩结果不落盘，跨轮压缩等于没做 | [`nexus/core/agent.py:373-386`](../../../../nexus/core/agent.py#L373-L386) | 代价是每轮多一次摘要调用（成本），不是正确性问题；修它要把摘要消息纳入 JSONL 持久化，改动面大 |
| `jobs` 表以 `job_00N` 为主键，跨任务互相覆盖 | [`nexus/task/store.py:748-771`](../../../../nexus/task/store.py#L748-L771)、[`nexus/tools/_jobs.py:183`](../../../../nexus/tools/_jobs.py#L183) | 只影响任务页的作业审计展示，模型执行读的是 live registry；改主键需要表结构迁移 |
| 交互回答在模型 transcript 中被移到末尾 | [`nexus/task/store.py:1404-1436`](../../../../nexus/task/store.py#L1404-L1436) | 会让模型看到因果顺序错位，但“把回答放到末尾”可能是有意保证审批语义；需先确认设计意图再改 |
| 网页读取无总超时，单次挂起会阻塞整个调度循环 | [`services/scheduler/src/nexus_scheduler/runtime.py:74-76`](../../../../services/scheduler/src/nexus_scheduler/runtime.py#L74-L76) | 需真实浏览器才能复现挂起；修法是加超时并重建 executor，改动中等，等实际出现再处理 |
| 工具参数 JSON 被截断时整轮硬失败、同帧合法调用一起丢失 | [`nexus/model/_parsers.py:111-112`](../../../../nexus/model/_parsers.py#L111-L112) | 需要模型输出被截断才触发（大文件写入）；修法涉及解析器容错与 agent 的部分提交语义，风险高于收益 |

## D. 需要产品决定，不是纯技术缺陷

| 事项 | 位置 | 待定问题 |
| --- | --- | --- |
| 一次性安排“错过”语义未实现，重启即补发 | [`services/scheduler/src/nexus_scheduler/store.py:390-396`](../../../../services/scheduler/src/nexus_scheduler/store.py#L390-L396) | 现有测试就是这样断言的；“错过→等待确认”目前只落在持久任务的一次性安排。唤醒是否也该如此，需要所有者定义 |
| 重启后 unknown 操作不阻止再次派发 | [`nexus/runtime/callbacks.py:155-177`](../../../../nexus/runtime/callbacks.py#L155-L177) | 当前靠“不自动重放 + 重新审批”约束；是否要硬闸门属产品取舍 |
| 任务续跑循环无迭代上限 | [`nexus/runtime/assembly.py:290-294`](../../../../nexus/runtime/assembly.py#L290-L294) | 模型每次都正常收尾却不调 `task_finish` 时会一直续跑；这是“去掉固定轮数”的已知代价，是否设上限需产品确认 |

## E. 环境相关，当前不适用

| 事项 | 说明 |
| --- | --- |
| 每日/每周用固定 offset 计算，夏令时切换日偏 1 小时 | 代码未用 `ZoneInfo`，取的是当天固定偏移。中国/日本无 DST，本机不受影响；等真在 DST 时区使用再说 |
| 依赖 `tzdata` 但实际未使用 | [`pyproject.toml`](../../../../pyproject.toml) 声明了 `tzdata`，调度代码走 `astimezone()` 固定偏移。属冗余依赖，不影响运行 |

---

# 三、确认无问题的部分

- **XSS**：`nexus/web` 下没有任何把用户/模型/文件名/任务标题拼进 HTML 的地方；动态 DOM 一律 `createElement` + `textContent`，`innerHTML` 只用于清空和 `renderUsage`（插值全是数字）。附件下载带 `nosniff`，非图片一律 `octet-stream`。
- **补丁与编辑安全**：392 组随机 diff fuzz **0 例写错**；所有拒绝路径都在写盘前抛出；写入统一“临时文件 + fsync + replace”，失败不留半写入。
- **路径约束**：`..`、工作区外绝对路径、UNC、`\\?\`、`~`、工作区内指向外部的 junction（读+写）全部被拒。
- **CSRF / Host / Origin / Content-Type** 三道校验成立；静态文件只按固定文件名读取，不存在路径穿越。
- **后台任务**：超时能杀孙进程；父进程先退出、孙进程持有管道时也能收尾；无管道死锁。
- **SSE 分帧**：逐字节切分、CRLF/CR、BOM、多字节跨块、非法 UTF-8 均正确。
- **重试语义**：只在未产生输出时重试，不会重复执行已发生的副作用。
- **消息结构**：裁剪不会拆散 `tool_use` 与 `tool_result`。
- **重启恢复**：不自动重放已生效操作；队列唤醒有超时兜底。
- **架构**：`tests/test_architecture.py` 守着分层、工具清单一致性与 schema 合法性。

# 四、未验证

- 真实付费模型、真实 Anthropic 端点、Playwright 浏览器全量交互**未验证**。P3 的 Anthropic 两条因此是“代码事实 + 官方规则”，不是实测。
- 前端证据来自 Node VM + 假 DOM，真实浏览器中的下拉/焦点/滚动表现未实测。
- 网页监听的真实浏览器挂起条件未复现。

# 五、说明

- 本轮只读，未修改任何源码。审查过程中探针脚本产生的临时目录已清理；仓库根目录的 `pytest-of-Nivalis` 为既有残留，未改动。
- 首轮审查记录见 [`参考/archive/2026-10-22-maintenance/BUG-AUDIT-2026-10-22.md`](../2026-10-22-maintenance/BUG-AUDIT-2026-10-22.md)。
