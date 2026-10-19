# Nexus 通用附件上传与文档读取交付记录

更新日期：2026-10-20（Asia/Tokyo）。需求：[PRD.md](PRD.md)。上一轮维护的需求及全部验证证据已归档至 [2026-10-20-maintenance](../2026-10-20-maintenance/PROGRESS.md)。本轮以用户确认的“通用文件保存 + 常见文档直接读取”为范围。

状态：**实现及自动化/隔离网页验证完成**，真实模型与文档复杂内容限制见文末。用户已授权提交并推送本轮交付，实际提交与远端状态以 Git 记录为准。

## 使用结果

- 网页“＋ 附件”支持选择任意格式文件、拖拽和粘贴；发送前显示文件名/大小，可移除，上传时显示处理状态，支持只发送附件。
- 普通会话和持久任务都保存附件。历史消息显示下载链接及可读、未提取到文字、仅保存原件或解析失败等状态；刷新与服务重启后保留。删除会话/任务清理所属文件，挂起/归档不删除。
- 已发送附件通过标识和原件路径进入模型上下文，正文由 `read_attachment` 工具分页读取，不在每条请求中无条件注入整份文档。分页返回行号、总行数和 next_offset；超长单行拆成多个读取行，避免后半段无法读取。
- 同名文件使用独立 ID 保存，不覆盖；附件按 sessions/tasks 和所属 ID 隔离。文件名只作为净化后的显示/原件名称，不参与任意目录定位。读取工具仅允许当前会话/任务实际发送过的附件。
- 上传或消息提交失败保留当前页面草稿，已成功上传的文件重试时复用 ID；上传期间切换会话不把原消息误发到新会话。草稿不保证跨页面刷新保存，与原图片草稿语义一致。
- 普通会话保留旧图片消息的视觉发送和历史显示；任务图片在绑定模型支持图片时构造标准图片内容，未声明图片能力时拒绝发送并保留草稿。普通文件内容读取不依赖模型的图片能力。
- 待执行任务仍由用户手动开始，向其发送文件不会自行启动；暂停/恢复及原有权限保持原有控制边界。

## 格式与参数

| 格式 | 已实现读取内容 | 限制 |
|---|---|---|
| 文本/代码/Markdown/CSV/JSON 等 | UTF-8、带 BOM 的 UTF-16、GB18030 | 非文本数据不能保证解析，保留原件 |
| PDF | 文本层与页码 | 无 OCR、视觉版面复原或密码输入功能；空白/扫描、加密、损坏分别反馈 |
| DOCX | 正文段落、表格 | 未实现完整排版复原、插图视觉分析、修订/脚注等完整 Office 对象覆盖 |
| XLSX/XLSM | 工作表名、单元格地址与值、公式源码 | 不重新计算公式，不执行宏；不承诺图表/绘图/外部链接的完整语义读取 |
| PPTX | 幻灯片编号、文字、表格、分组内文字、备注 | 不渲染幻灯片，不承诺图表和插图的视觉理解 |
| PNG/JPEG/WebP/GIF | 使用现有模型图片能力 | 对应模型须声明并实际支持图片 |
| 旧 DOC/XLS/PPT、压缩包及其他格式 | 原件保存及路径，按现有工具能力处理 | 不声称已解析；读取工具返回明确不可直接读取结果 |

每次最多 8 个附件，普通文件单个上限 **25 MiB**，图片单张上限 **5 MiB**。提取文本缓存最多 **2,000,000 字符**，达到上限时在 UI 和工具返回中说明；原件不截断。读取工具默认 200 行，可指定 1–1000 行，每次文字输出约 30,000 字符以内。

## 实现位置与依赖

| 位置 | 职责 |
|---|---|
| `nexus/attachments.py` | 原件/元数据/文本存储、文件名处理、格式提取、分页和消息附件清单 |
| `nexus/web/attachments.py` | 会话和任务共用的二进制上传、原件下载接口，校验所属对象 |
| `nexus/tools/attachments.py` | 只读 `read_attachment` 工具，区分提取成功和不可直接读取 |
| `nexus/web/app.js`、`tasks.js`、`index.html`、`app.css` | 选择/拖拽/粘贴、上传队列、发送引用、历史下载与状态；补修初始化响应覆盖用户已选任务的竞态 |
| `nexus/web/server.py`、`task_api.py` | 消息引用校验、存储及删除清理，普通图片兼容 |
| `nexus/runtime/assembly.py`、`nexus/task/store.py`、`nexus/tools/__init__.py` | 向 Agent 装配附件工具和授权 ID，重建任务附件上下文 |
| `pyproject.toml`、`nexus/bootstrap.py` | 新文档依赖与启动导入检查；现有虚拟环境缺少解析依赖时按项目配置安装 |

存储位于 Nexus home 下的 `attachments/<sessions或tasks>/<所属ID>/<附件ID>/`，原件、`meta.json` 和可选 `text.txt` 分开保存。JSONL/SQLite 消息中只增加附件引用和元数据字段，无数据库 schema 迁移；旧消息继续可读。

新增生产依赖为 `pypdf>=6,<7`、`python-docx>=1.2,<2`、`openpyxl>=3.1,<4`、`python-pptx>=1,<2`，均用于当前明确要求的文档读取。安装和测试使用现有 `.venv`，未修改用户 home/凭据或日常会话。

技术核对使用了官方文档：[pypdf 文本提取与 OCR 边界](https://pypdf.readthedocs.io/en/latest/user/extract-text.html)、[python-docx 正文迭代](https://python-docx.readthedocs.io/en/latest/api/document.html)、[openpyxl 加载参数](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html)、[openpyxl 只读模式关闭要求](https://openpyxl.readthedocs.io/en/stable/optimized.html)、[python-pptx 文字](https://python-pptx.readthedocs.io/en/stable/user/text.html)与[备注](https://python-pptx.readthedocs.io/en/stable/api/slides.html)。没有引入通用文档框架、云服务或自动执行附件机制。

## 验证证据

- Python 全量：`.venv/Scripts/python.exe -m pytest tests -q` → **370 passed, 1 skipped in 31.19s**。唯一跳过为既有 Windows 符号链接权限场景，未绕过必要检查。
- 前端行为：`node tests/frontend_behavior.cjs` 全部通过，新增通用文件上传后只发送引用、上传失败保留草稿、任务只发附件；保留原图片、流式工具、轮询、切换和输入保护检查。
- 静态与语法：`ruff check nexus tests --select F --output-format concise` 通过；前端语法检查与 `git diff --check` 通过。不声称全规则 lint 已通过。
- 文件测试：实际生成 PDF、DOCX、XLSX、PPTX，断言正文/表格/单元格/公式/备注；覆盖中文文本编码、同名不覆盖、跨对象引用拒绝、分页与超长行、文本截断提示、损坏/加密/无文本文件原件保留、超限和中断上传清理。
- HTTP 与真实 Agent 循环：验证原件下载逐字节一致、删除清理、任务附件元数据重开库保留、错误归属不写入消息；本地假模型实际调用 `read_attachment`，下一次请求收到正文中的证据值。
- 真实 Edge：`tests/browser_smoke.cjs` 在临时 home/workspace、本地假模型和假密钥下完成六种格式上传、普通会话和任务只发送附件及读取、原件下载、页面刷新、Nexus 进程退出/重启后继续查看和下载；重启不执行挂起任务。保留旧图片、审批、任务和离线业务图检查。最终脚本输出 `Browser smoke passed`，退出码 **0**。

复核浏览器命令：

```powershell
$env:NEXUS_PLAYWRIGHT_MODULE='C:\Users\Nivalis\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\playwright'
$env:NEXUS_TEST_PYTHON=(Resolve-Path '.venv/Scripts/python.exe').Path
$env:NEXUS_BROWSER_CHANNEL='msedge'
node tests/browser_smoke.cjs
```

截图留在本地忽略目录 `.artifacts/attachments-chat-smoke.png`、`.artifacts/attachments-task-smoke.png`。测试会清理临时数据和进程，不把旧截图当作本轮通过依据。网页复核曾暴露初始化竞态，已修复；重启下载测试曾把相对 URL 直接传入测试请求器，改为页面实际绝对链接后复核。

## 验收边界

需求中的普通会话/任务上传与存储、只发送附件、支持格式读取、原件下载、同名和归属处理、重开保留、异常反馈及现有功能回归均有上述证据。PRD 保留需求原貌，不代替用户最终验收。

没有调用真实付费模型，不将假模型的确定性响应等同任意模型都能正确理解文档。未对所有复杂 PDF/Office 文档、极端大小压缩文档、解析过程中断电和所有第三方工具组合逐一实测。OCR、旧二进制 Office 直接解析、图表/插图视觉理解、全文无损版面复原和文档编辑不在本轮已交付能力中。其他文件能否进一步处理仍取决于本机现有工具与授权。
