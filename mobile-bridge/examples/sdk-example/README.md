# 插件公共 UI 示例

后端与 SDK 沿用已发布的 SDK 0.2.0，页面展示当前宿主的公共控件与主题、动效跟随。

将本目录复制到 Nexus 插件目录，点击“发现插件”，加载 `sdk-example` 后打开功能页。
也可以把本目录内容打成 ZIP，让 `plugin.json` 位于 ZIP 根目录，再从插件管理导入。
本示例只读查询主交流状态，不发送消息，不修改业务资料。

页面引用 `/css/plugin-ui.css` 和 `/js/plugin-ui.js`；使用 `data-nexus-plugin`、
`plugin-page`、`card`、`btn solid`、`field`、`help` 和 `message error show`。
在宿主同源 iframe 内跟随当前配色、日夜主题和五档动效速度；独立打开时使用日间默认样式。
需要提供上述公共 UI 资源的新版 Nexus 宿主，协议仍为 `api_version: 1`，
主交流接口仍要求 `PluginHost.chat_api_version == 1`。
当前 SDK 0.2.0 ZIP 与此源码示例一致，其他插件发布包和 SDK 0.1.0 保持原样。
