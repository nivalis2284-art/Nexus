# Nexus 插件 SDK

这是可单独复制的插件开发包，当前版本 **1.2.0**，包含 Python 与 Node 客户端、构建工具、接口文档和示例。Python 需要 3.12；使用 Node 服务的插件需要 Node.js 22 或更新版本。直接 Python 插件在 Nexus 的 Python 环境内运行，使用宿主提供的 `nexus.plugins.api`。

## 快速开始

复制整个 SDK 目录，在任意工作目录执行：

```powershell
python path/to/nexus-plugin-sdk/build.py path/to/nexus-plugin-sdk/examples/tool-skill
python path/to/nexus-plugin-sdk/build.py path/to/nexus-plugin-sdk/examples/host-page
```

ZIP 输出到相应示例目录。进入 Nexus 的插件管理，导入 ZIP 并启用；工具与技能示例可在主交流中使用，宿主页面示例可在插件管理中打开。

开发自己的插件时，复制示例目录到工作位置，修改 `plugin.json` 中的 ID、名称和版本，再编辑代码与 README；用上面的命令传入新的插件目录即可。ID 只能包含小写字母、数字、连字符和下划线。

完整接口、目录格式及打包范围见 [SDK.md](SDK.md)，页面组件与交互格式见 [UI.md](UI.md)。构建工具不会安装插件或改变 Nexus 数据。

宿主 `work` v1 能力可从插件启动标准后台 Agent 工作、读取工作事件、停止所属工作，并把需要说明的结果送回主交流。它复用 Nexus 的工具和审批机制；与插件自行执行工具的独立模型对话及有限 jobs 不同。需要此接口的插件声明 `requires_host: {"work": 1}`。

这是公共插件能力，不绑定“约定与关注”；业务调度与判断留在各插件。Python、Node 用法与旧接口关系见 [work 接口契约](SDK.md#work标准后台-agent-工作)。

**跨电脑部署**：先更新实际运行的 Nexus 宿主到提供 `work v1` 的版本，完全重启，再导入使用它的插件。只更新 SDK 文件或导入 ZIP 无法给旧宿主增加能力。SDK 1.2.0 不改变旧插件的 `api_version: 1`，未声明新能力的旧插件继续按原接口工作。
