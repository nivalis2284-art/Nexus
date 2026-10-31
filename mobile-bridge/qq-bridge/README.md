# Nexus QQ 通信插件 0.1.0

独立插件开发目录。源码、测试、文档及构建产物均位于 `mobile-bridge/` 下；开发不会修改、安装或加载到 Nexus 宿主。ZIP 由你决定何时导入。

## 能力

- 官方 QQ 机器人单聊、群聊；默认群聊被 @ 时触发，可选择获授权的全量消息模式。只有明确启用的群会转发。
- 输入文字、静态图片、语音及文件。输出文字与本机 PNG/JPEG 图片。图片发送工具不负责生成图片。
- 在本地记录来源、发送者、会话、QQ 消息标识、引用、卡片及原始事件，然后整理内容。
- 非白名单消息调用 TypeSafe **Jev 原生 System One API**，检查提示词注入、人设篡改及内容分类。
- 风险或任一违规分类达到阈值时由程序拦截，默认均为 **80/100，包含等于**。预审或附件处理失败时留在收件箱，默认不转发、不自动循环重试。
- 绑定的所有者和免审白名单均跳过 Jev 和插件内容拦截；白名单不等于所有者。免审显示为“未审”，不会伪装成 0% 风险。
- 通过的消息沿用同一个 Nexus 主交流入口，保存 `source=qq`、发送者和风险标签；QQ 回复使用明确指定会话的工具，不自动转发其他本地回答或思考过程。

## 准备与安装

1. 在 [QQ 官方开放平台](https://q.qq.com/) 创建机器人，取得 AppID 与 AppSecret，开通所需单聊/群聊能力。机器人具有独立身份，不接管个人 QQ 账号。
2. 准备 [TypeSafe Jev](https://docs.typesafe.ai/) API 密钥。默认模型固定为 `jev-1.14.0`，地址为 `https://api.typesafe.ai/v1`，可修改为兼容原生 System One 协议的地址。
3. 如 QQ 不提供语音转写，准备支持 `/audio/transcriptions` 的 OpenAI 兼容转写服务及模型。
4. 构建得到的 `mobile-bridge/releases/plugins/nexus-qq-bridge-0.1.0.zip` 可由你在 Nexus 插件管理中手动导入、加载。**本开发交付不执行这一步。** 插件加载后也默认不连接 QQ。
5. 在插件“连接与身份”页填写凭据、启用连接并保存。凭据输入留空保留原值，清除需使用对应按钮。凭据只写入本机插件资料，不回传页面或写入普通插件设置。
6. 用自己的 QQ 给机器人发一条消息，在收件箱核对发送者后点击“设为本人”。该条消息若被留待处理，绑定后点击“重新处理”。单聊身份和每个群的身份分别绑定；不能只填写昵称或假定各场景 OpenID 相同。
7. 从群里 @ 机器人，收件箱出现该群消息后点击“启用此群”，再按需重新处理。全量接收需群主在 QQ 中启用相应能力，且插件选择全量模式。

插件入口只依赖宿主公共作者接口 `nexus.plugins.api`，不访问宿主可变状态。使用宿主当前已有的 `httpx`、`pydantic`、Pillow（宿主 `python-pptx` 的依赖）及 `pypdf`；没有新增宿主依赖或修改宿主配置。需要 `host.chat_api_version == 1` 和 `context(scope)` 返回消息视图的现有接口。

## 多模态处理

原始媒体和整理结果分别保留。附件下载限 QQ HTTPS 媒体域名，单文件上限 25 MiB；超限或不支持的格式进入待处理，不把缺失判断当成安全。

- **静态图片**：保留原件，将正确朝向的图片转换为 JPEG、缩放或补边至 Jev 接受的 280–1568 像素范围，再送入 Jev。通过后通过插件上下文将图片块交给主交流模型。主交流模型本身需要支持图片。动态图未做逐帧预审，非白名单消息留待处理。
- **语音**：优先使用 QQ 的 `asr_refer_text`，否则下载音频并调用配置的转写服务。优先选取 QQ 提供的 WAV 地址；无 WAV 时，转写服务需支持原始音频格式。Jev 和 Nexus 接收转写文字，原始音频留在本机。转写可能遗漏语气、非语音声音及识别错误。
- **文件**：读取 UTF-8/GB18030 文字、PDF、DOCX、XLSX/XLSM、PPTX。Office 包中的文本、外部链接、嵌入图片一起参与判断；嵌入的其他文件需单独提供。PDF 提取文字与可读取的嵌入图片；无法提取内容的页面留待处理。不会执行宏、文件或链接。此路径不是文档页面视觉渲染或病毒扫描。
- **引用与卡片**：正文、作者和嵌套引用参与判断，引用中的附件也处理。原始回调的鉴权字段保留在本地原始事件，不作为引用文字送给 Jev。
- **输出图片**：提供 `qq_send_message.image_path` 发送本机 PNG/JPEG，二进制直传上限 5 MiB；大图需先缩小。未实现视频、语音或文件输出。

Jev 的图像能力在当前官方文档中标为试验能力；不能将成功调用解释为完整、可靠的图像安全审查。

## 预审策略与来源

Jev 每次回答一组固定 Noul 问题，原生返回 `noul` 概率。插件保留模型版本、各分类概率和用量，按程序阈值决定转发。超过单次文字预算的内容分段检查，逐项取最大值；不截取文件前缀后声称检查了全文，也不编造 Jev 不返回的解释。

当前分类为：提示词注入、人设篡改、色情与性侵害、暴力及恐怖煽动、诈骗与隐私侵害、仇恨骚扰、自伤鼓励。分类问题排除相应的教育、求助、防御研究与中性报道，详见 `review.py` 中的固定问题。这是本插件的明确预审范围，不等于完整覆盖平台所有运营规则。QQ 平台自身的规则不会因白名单而豁免。

来源、身份和风险标签由插件生成，消息正文不能改变绑定和阈值。QQ 群主、管理员不是 Nexus 所有者。没有设置自动把任何 QQ 发言当成所有者授权的逻辑。

宿主目前将 `chat_send(text, request_id)` 保存为普通用户消息，因此 QQ 标记保存在结构化正文中，并由插件上下文提醒模型区分。不会给 Nexus 增加原生来源字段或气泡标签。上下文压缩后的标签保留依赖主交流模型；本地完整记录仍可通过 QQ 读取工具查询。

预审属于内容判断，不是宿主工具权限隔离。非白名单输入仍可能误判或漏判；插件本身的读取工具不返回被拦截/待处理内容，宿主已有文件与命令权限机制保持其原有职责。

## 工具及发送规则

只向主交流注册：

| 工具 | 用途 |
| --- | --- |
| `qq_inbox` | 查看最近已通过或免审的消息及标签 |
| `qq_read_message` | 按插件收件编号读取通过或免审的消息 |
| `qq_send_message` | 明确选择单聊/群聊和目标 OpenID，发送文字或图片 |

`reply_to` 是插件的 `receipt_id`，不是裸 QQ 消息 ID。插件核对其与发送目标是同一会话，并为同一被回复消息分配持久化 `msg_seq`。发送结果不确定时记录为 `unknown` 并提示核对，不自动重发。

已提交到宿主但响应丢失时，重试复用完全相同的正文和 `request_id`，由宿主去重；不会因为后来改了阈值而改写一条可能已经入队的输入。停用插件不能撤销已被宿主接收的输入或已经发生的 QQ 发送。

## 本地数据与隐私

安装后，运行资料位于 Nexus 为该插件分配的数据目录 `plugins/qq-bridge/`，与本开发目录不同：

```text
credentials.json   QQ、Jev、语音服务凭据
qq.sqlite3         收件箱、预审、转发状态及发送记录
raw/*.json         收到的原始 QQ 事件（可含平台鉴权字段）
media/<receipt>/   原始附件、文档提取图片与判断用图片
```

QQ 消息、文件和图片在非白名单预审时发送给 Jev；语音在需要外部转写时发送给转写服务。白名单跳过 Jev，仍可能为了语音输入调用转写服务；主交流继续按原有配置调用其模型。资料可能含私人聊天及密钥，开发 ZIP 不包含任何运行资料。

电脑关机或插件离线时不承诺收消息；WSS 在短时断线时按官方协议恢复会话，进程重启不保证补齐离线历史。此版本不能读取个人 QQ 账号与好友已有的全部私聊。

## 开发、测试与构建

从仓库根目录运行（只读使用现有 Python 环境；不会启动 Nexus）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.venv\Scripts\python.exe -m pytest mobile-bridge/qq-bridge/tests/test_bridge.py -q -p no:cacheprovider
node --check mobile-bridge/qq-bridge/web/app.js
.venv\Scripts\python.exe mobile-bridge/qq-bridge/build.py
```

离线界面预览使用合成消息和模拟宿主，不连接 QQ/Jev，不安装插件：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Get-Location).Path
.venv\Scripts\python.exe mobile-bridge/qq-bridge/tests/preview.py
# http://127.0.0.1:8799/plugins/qq-bridge/index.html
```

## 验证边界与官方依据

本地定向验证使用模拟宿主、模拟 HTTP 和本机 WebSocket 帧；管理页通过独立预览服务检查。没有使用真实 QQ 凭据、Jev 密钥或语音服务，未安装/加载到 Nexus，尚未完成外部服务联调与中文识别准确率评估。

本轮 Python 31 项定向测试、7 项浏览器行为检查通过，详见 [验证记录](docs/VALIDATION.md)。

接口核对日期：2026-10-29。

- [QQ WSS 接入与恢复协议](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/websocket.html)
- [QQ 消息收发与频次规则](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)
- [QQ 单聊消息与身份字段](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/c2c_message_create.html)
- [QQ 群全量消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)
- [QQ 群富媒体上传](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_files.post.html)
- [Jev 原生 API](https://docs.typesafe.ai/api/system-one)、[Noul 概率](https://docs.typesafe.ai/primitives/noul)、[图像请求与限制](https://docs.typesafe.ai/images)、[模型版本](https://docs.typesafe.ai/models)
