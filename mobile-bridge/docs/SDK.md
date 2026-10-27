# 第三方插件 SDK 0.2.0

SDK ZIP 提供无第三方依赖的主交流适配和可安装示例，插件标识为 `sdk-example`。加载示例后可从页面查询主交流。解压 ZIP 开发时修改插件 ID、名称与版本，重新打包时将清单放在 ZIP 根目录或唯一的一层插件文件夹中；不要打包环境、缓存或凭据。

## 宿主要求

SDK 和手机桥接插件要求宿主提供 `PluginHost.chat_api_version == 1` 的直接主交流接口。清单的 `api_version: 1` 保持不变，不能单独用于判断此能力；旧宿主不受支持，也没有 HTTP 回退。入口在宿主解释器中运行，插件不是进程沙箱。[插件接入说明](../../docs/参考/理论/插件系统/接入说明.md)列出宿主的其他能力。

宿主调用工厂与可选 `start()`；插件在 `close()` 中释放自己的监听器、连接和线程。宿主在等待在途调用结束之前关闭该插件的主交流订阅。`host.background()` 适用于有限工作，不适合永久监听，否则会阻碍停用。

## 主交流适配

宿主直接提供 `chat_snapshot()`、`chat_send(text, request_id)` 和 `chat_subscribe()`；SDK 封装这些能力，不直接访问私有管理器或 Web 接口。发送复用宿主 `Application.submit_input` 的处理逻辑，同一消息的重试必须复用 `request_id`，新消息使用新编号。

订阅支持 `get(timeout)`：返回事件字典，超时抛出 `queue.Empty`，关闭后返回 `None`；`close()` 可重复调用，也可使用 `with`。事件流覆盖 `ling` 和 `task`，调用方根据 `scope` 和 `owner_id` 筛选。事件含 `type`、`revision` 和 `live`；`delta.text` 为正文增量，`message_id` 用于关联记录，另有 `thinking`、`done`、`error`、`refresh` 等类型。

先订阅再读取快照；流不重放历史，断线后重新订阅并读取新快照。快照至少包含 `session_id`、`items`、`running`、`queue_count`、`live`、`live_revision`、`main_requests`、`tasks`；按 `message_id` 合并已保存的 `items` 和 `live`，避免重复显示。使用修订号协调快照与订阅事件。

## SDK 用法

`ChatClient(host)` 检查宿主版本，`snapshot()`、`send(text, request_id)` 和 `open_events()` 分别包装三个直接接口。`open_events()` 返回订阅对象，不是 HTTP 响应；使用完毕关闭订阅。ZIP 中的 `backend.py` 是可安装示例；手机接入插件 0.2.0 展示长驻监听与清理。

## 手机桥接协议 v1

手机插件对安卓端仍使用原有协议，APK 保持 0.1.0：

| 请求 | 鉴权 | 响应 |
| --- | --- | --- |
| `POST /pair`，`{code, device_name}` | 一次性电脑配对码 | `{token}` |
| `GET /chat` | `Authorization: Bearer <token>` | 主交流快照 |
| `POST /messages`，`{text, request_id}` | Bearer | 宿主接收状态，成功通常为 202；重试可能带 `duplicate` |
| `GET /events` | Bearer | SSE：初次 `snapshot` 事件，后续 `runtime` 事件 |

插件 HTTP 不向网页开放 CORS；手机连接仅用于可信局域网。宿主侧适配和手机外部协议的验证状态见[验证记录](VALIDATION.md)。

`live_revision` 是开始读取主交流快照前的保守水位；`live.revision` 可能更新。订阅方仍需处理水位之后的事件，即使部分内容已经出现在快照中，以免漏掉读取期间完成的回复。

缓冲事件可能已反映在快照中。按消息标识应用事件完整的 `live` 状态或去重，避免无条件追加 `delta.text`。允许新增字段与事件类型，消费者忽略不使用的部分。
