# MCP 重构第一阶段

本阶段完成了调度规则与监听的 MCP 服务拆分、必要的代码与本地协议检查、针对性测试及文档收尾。这是限定范围的代码验收，不等同真实全量验收；未切换日常实例。

## 实现边界

- Nexus 使用官方 Python MCP SDK，支持 stdio、Streamable HTTP 和可选 Bearer token。设置页提供服务增删改、启停、重连、连接状态、工具数量和错误。
- 调度与网页监听放在独立包 `services/scheduler/`，不导入 Nexus 源码，也不读取 Nexus 数据库。该服务保存规则、下次触发时间和网页基线，通过 HTTP 发送唤醒请求。
- Nexus 只保留唤醒接收、队列与用户输入优先执行、主会话 Agent、回复或一句话沉默经历、活动记录。规则设置界面通过 MCP 转发操作；规则工具直接由 MCP 服务提供。
- Nexus 默认启动一个调度服务，主会话与独立任务复用同一个 MCP registry；单个任务结束不关闭共享连接。服务随 Nexus 关闭。
- 保留一次性、间隔、每日、每周安排，自定义间隔和每日规划时刻，以及公开网页正文变化监听。每日规划仍由同一个灵决定，允许不安排新的唤醒；思考强度沿用主会话。

## 运行和数据

启动准备流程会安装 Nexus 和调度包。单独安装与运行调度服务：

```powershell
.venv\Scripts\python.exe -m pip install --no-cache-dir -e services/scheduler
.venv\Scripts\python.exe -m nexus_scheduler --data <服务数据目录>
```

默认服务通过 stdio 接入，Nexus 将自己的 `/api/automation/wake` 地址传给服务。服务通过 `scheduler_connect(callback_url)` 取得接收地址。唤醒请求包含 `source`、`reasons`、`prompt`、`notify`、`kind`；接收成功返回 HTTP 202 和 `run_id`。

服务拥有 `scheduler_state`、`scheduler_configure`、安排和监听的保存/删除/手动触发工具。`scheduler_import` 用于一次性导入已有规则。Nexus 保留 `automation_list` 和 `automation_finish`。

首次连接时，Nexus 读取旧 `automation.sqlite3` 的规则并交给服务导入，保留 ID、原触发时间和网页基线。服务记录导入标记，重连不会重新覆盖已修改的规则。旧表留作迁移来源，不再写入；唤醒队列和活动记录继续保存在该数据库，服务数据位于 `scheduler/scheduler.sqlite3`。

如需替换默认服务，可在 `config.json` 的 `mcp.scheduler` 中填写与普通 MCP 服务相同的配置。普通服务位于 `mcp.servers`；HTTP 使用 `transport: "http"`、`url` 和可选 token，界面保存的 token 放入既有凭据文件。本阶段没有 OAuth。

用户可在设置页的「MCP 服务」配置普通服务：stdio 填写命令、参数和环境变量；HTTP 填写 URL 和可选 Bearer token。`remote` 的 token 在设置页填写，不写入示例或 `config.json`。占位配置示例：

```json
"mcp": {
  "servers": [
    {"name": "fs", "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
    {"name": "remote", "transport": "http", "url": "https://mcp.example.com/mcp"}
  ]
}
```

## 必要验证

使用临时数据目录、本地模拟服务和模拟模型响应。已验证调度服务通过 MCP 接收规则，经真实本地 HTTP 路由把唤醒交给 Nexus，以及回复/沉默记录、到期推进、单次迁移、保留活动历史和复用同一个连接。网页变化判断使用模拟页面正文，未进行真实浏览器操作。

针对性测试覆盖 MCP 连接、管理接口、调度服务、唤醒执行、共享连接生命周期、启动准备和相关 Web 路由。HTTP 测试使用官方 SDK 的本地 HTTP 服务，已验证 Bearer 接受/拒绝、工具发现与调用、结构化结果传入 Agent 工具返回；未使用 SDK 私有 transport 测试接缝。另验证了 Nexus 启动时只有一个共享调度连接，以及退出时关闭连接。

本次针对性检查结果为 84 passed, 1 skipped, 0 failed，29.64s；跳过 `test_bootstrap.py::test_archive_is_refused_when_venv_resolves_outside_root`，原因是当前环境无法创建目录符号链接。`compileall`、`node --check nexus/web/mcp.js` 和 `git diff --check` 通过；MCP 脚本 21 个 DOM 引用检查无缺失，`automation.js`/`ling.js` 语法检查通过。该结果不等同真实全量验收。

本次安装曾卡在本机 pip 缓存目录临时文件写入，已定位并改用 `--no-cache-dir`，独立包已成功安装。

启动修复：`start.bat` 原先未指定数据目录，会使用 `%LOCALAPPDATA%\Nexus`；用户实际启动时该目录返回 WinError 5，且现有日常实例的数据保存在项目的 `.nexus-data`。启动脚本现默认使用项目 `.nexus-data`，保留已设置的 `NEXUS_HOME`。已用真实 cmd 验证默认路径和自定义路径，并在隔离数据目录运行完整启动脚本，确认页面 HTTP 200、调度服务连接及 10 个工具可用；启动准备测试 8 passed、1 skipped。未修改日常数据或系统目录权限。

编译、MCP 前端脚本语法和 diff 空白检查通过。严格 Ruff 检查仍有运行循环/连接关闭处的宽泛异常捕获、关闭时忽略异常及少量写法建议，未将其扩展成第一阶段的全面风格清理；本阶段不宣称全仓 lint 通过。

## 暂未执行

没有调用真实付费模型、进行真实浏览器或全量验收，没有启用或重启日常实例。本阶段的代码验收、测试与文档收尾已完成；真实全量验收与日常实例切换尚未进行。清理时移出的两个旧接口手动验收脚本已原样归档至 `docs/参考/archive/2026-10-22-automation-manual-checks/`，仅供追溯，不能作为当前验收依据。本阶段不增加预算系统、单独思考轮次限制、复杂重试或其他假设性防御机制。
