# Nexus 插件 SDK

这是可单独复制的插件开发包，包含 Python 与 Node 客户端、构建工具、接口文档和示例。Python 需要 3.12；使用 Node 服务的插件需要 Node.js 22 或更新版本。直接 Python 插件在 Nexus 的 Python 环境内运行，使用宿主提供的 `nexus.plugins.api`。

## 快速开始

复制整个 SDK 目录，在任意工作目录执行：

```powershell
python path/to/nexus-plugin-sdk/build.py path/to/nexus-plugin-sdk/examples/tool-skill
python path/to/nexus-plugin-sdk/build.py path/to/nexus-plugin-sdk/examples/host-page
```

ZIP 输出到相应示例目录。进入 Nexus 的插件管理，导入 ZIP 并启用；工具与技能示例可在主交流中使用，宿主页面示例可在插件管理中打开。

开发自己的插件时，复制示例目录到工作位置，修改 `plugin.json` 中的 ID、名称和版本，再编辑代码与 README；用上面的命令传入新的插件目录即可。ID 只能包含小写字母、数字、连字符和下划线。

完整接口、目录格式及打包范围见 [SDK.md](SDK.md)，页面组件与交互格式见 [UI.md](UI.md)。构建工具不会安装插件或改变 Nexus 数据。
