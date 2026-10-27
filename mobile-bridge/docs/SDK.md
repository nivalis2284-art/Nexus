# 第三方插件 SDK 0.1.0

此 SDK 是针对现有 Nexus 插件 API v1 与主交流 HTTP 接口整理的最小第三方适配层，不是对宿主新增的稳定 API 承诺。验证基线为 Nexus `b73ff10`；后续宿主升级时应重新验证 HTTP 契约。

SDK ZIP 可直接在插件管理导入，插件标识 `sdk-example`。加载后打开示例页，「查询主交流」会显示主交流标识与运行状态。它不发送模型请求，也不修改用户配置。

## 包内容

- `nexus_plugin_sdk.py`：无第三方依赖的 `ChatClient` 与公开 `PluginHost` 类型协议。
- `backend.py`、`plugin.json`、`web/index.html`：可安装的最小示例。
- `README.md`：本说明。

使用源码开发时解压包，修改插件 ID、名称和版本，把程序与清单重新压成 ZIP；清单位于 ZIP 根目录，或仅包含一层插件文件夹。不要把环境、缓存、凭据打入包中。

## 宿主公开能力

入口 `backend.py:create_plugin` 接收宿主提供的 `PluginHost` 对象。此项目只使用其公开的 `base_url`、`data_dir`、`settings` 和 `save_settings()`。本地 Python 入口在宿主解释器内运行；额外依赖的独立服务可由宿主清单服务机制接入。插件不是进程沙箱。

宿主依次调用工厂与可选 `start()`；停用时调用 `close()`。需在 `close()` 中释放监听服务、连接与线程。持续运行的监听服务不应使用 `host.background()` 永久占用租约，否则宿主会等待租约结束才调用清理。

## 主交流适配

宿主公开插件 facade 尚未提供主交流提交和 SSE 订阅方法。本 SDK 使用 `host.base_url` 指定的本机 HTTP 地址，通过现有接口实现：

| 功能 | 接口 |
| --- | --- |
| 读取主交流 | `GET /api/ling` |
| 发送文字 | `POST /api/ling/input`，JSON `{text, request_id}` |
| 实时更新 | `GET /api/stream`，SSE，`data:` 行为 JSON，注释行为心跳 |

```python
from .nexus_plugin_sdk import ChatClient

class Plugin:
    def __init__(self, host):
        self.chat = ChatClient(host.base_url)

    def call(self, action, values):
        if action == 'snapshot':
            return self.chat.snapshot()
        if action == 'send':
            return self.chat.send(values['text'], values['request_id'])
        raise ValueError('未知操作')

def create_plugin(host):
    return Plugin(host)
```

`ChatClient` 仅接受本机 HTTP origin 和上述三条路由。`NexusHTTPError.status/payload` 保留宿主校验失败，便于调用方解释未配置模型、请求冲突等情况。发送重试必须复用原 `request_id`，新消息使用新 ID。

`open_events()` 返回调用者负责关闭的 HTTP 响应。先订阅，再获取快照，避免订阅前丢失更新。宿主 SSE 不是持久事件日志；断线后重新获取快照，不假定支持 Last-Event-ID 重播。`snapshot.items` 包含原有记录；`live` 包含当前部分输出，需按 `message_id` 合并，避免重复显示。

手机插件独立实现相同适配契约，使用可主动关闭的 HTTP 连接管理长流；SDK 的简单阻塞适配器不承担插件服务的停用与设备管理。

## 手机桥接协议 v1

| 请求 | 鉴权 | 响应 |
| --- | --- | --- |
| `POST /pair`，`{code, device_name}` | 一次性电脑配对码 | `{token}` |
| `GET /chat` | `Authorization: Bearer <token>` | Nexus 主交流快照 |
| `POST /messages`，`{text, request_id}` | Bearer | 宿主接收状态，成功通常为 202；重试可能带 `duplicate` |
| `GET /events` | Bearer | SSE：初次 `snapshot` 事件，后续 `runtime` 事件 |

首版没有通用代理、任意命令执行、模型密钥读取或远程审批接口。插件 HTTP 仅接受原生客户端请求，不向网页开放 CORS。

## 可行性结论

局域网手机聊天可以作为纯第三方插件实现；没有修改宿主源码、直接访问 `_manager`/全局 `STATE`，也没有重新实现模型会话。当前缺口是主交流 HTTP 契约尚未成为插件 facade 中的专用接口，因此兼容性需按宿主版本测试。
