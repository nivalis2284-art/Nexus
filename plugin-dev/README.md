# Nexus 插件开发

这里用于后续插件开发，每个插件单独一个子目录。旧的 `mobile-bridge/` 保留为历史验证产物，不自动迁移、修复或重新打包；新开发或重构某个插件时，再建立对应子目录。

新插件根目录须提供非空的 UTF-8 `README.md`，介绍用途、使用方法、配置要求与实际限制。打包时会检查，安装后可在插件管理中通过“查看介绍”弹窗阅读；已有插件缺少介绍不影响安装和加载。

统一 SDK 入口为 [SDK.md](SDK.md)，页面协议见 [UI.md](UI.md)。根目录 `nexus_host.py` 与 `nexus-host.mjs` 是同一 SDK 的 Python、Node 客户端，各插件源码不维护副本；发布包按实际引用加入客户端。

```text
plugin-dev/
  README.md
  SDK.md
  UI.md
  nexus_host.py
  nexus-host.mjs
  build.py
  <插件ID>/                 开始开发后创建
    plugin.json
    backend.py 或 worker.mjs
    web/ 或 view.json
    tests/
    README.md
    nexus-<插件ID>-<版本>.zip
```

在仓库根目录执行 `.venv\Scripts\python.exe plugin-dev/build.py <插件目录名>`，ZIP 输出到该插件自己的目录。发布前更新 `plugin.json` 版本；构建只制作 ZIP，由所有者决定何时导入和加载。
