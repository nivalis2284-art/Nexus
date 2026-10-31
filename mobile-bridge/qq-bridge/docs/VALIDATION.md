# QQ 插件验证记录

日期：2026-10-29。开发范围仅为 `mobile-bridge/qq-bridge/`，发行包位于 `mobile-bridge/releases/plugins/`。

## 已验证

- Python 离线定向测试 **31 项通过**：来源及身份不可由正文指定、所有者/白名单豁免区别、群范围和全量开关、攻击/违规阈值、无效概率拒绝、失败留存、精确入队重试、绑定移除、配置变化期间暂停转发、跨会话回复拒绝、文字和图片发送形状、嵌套引用附件、语音两条转写路径、Office 页眉/链接/图片、图片上下文、原生 WSS 帧和 Gateway 事件接收、插件停用收尾。
- Node 检查 `web/app.js` 语法通过。
- 独立浏览器预览使用模拟宿主及合成消息，**7 项行为检查通过**：消息渲染、刷新保留未保存设置、保存失败保留输入并提示、拦截筛选、正文 HTML 转义、移动设置页无横向溢出、无运行时异常。
- 1440、768、390 像素宽度实测无横向溢出及浏览器运行异常；查看桌面/移动收件箱和设置页截图。沿用宿主公共样式。设计检查无失败项，有一项宿主既有暖色背景的风格提醒；未为消除该提醒修改宿主配色。
- ZIP 构建自检：文件完整、根目录清单唯一、ZIP CRC 正常、没有测试资料、凭据或数据库。
- 没有安装、扫描或加载插件到 Nexus；没有启动 Nexus。开发中的早期文件已从 `plugins/qq-bridge/` 移出。最终差异仅在 `mobile-bridge/` 内。

测试命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.venv\Scripts\python.exe -m pytest mobile-bridge/qq-bridge/tests/test_bridge.py -q -p no:cacheprovider
node --check mobile-bridge/qq-bridge/web/app.js
.venv\Scripts\python.exe mobile-bridge/qq-bridge/build.py
```

预览与截图证据保留在开发目录 `.artifacts/ui/`，不进入发行包。独立预览服务器已在验收后停止。

## 未验证与交付限制

没有提供真实 QQ、Jev 或语音服务凭据，因此没有验证 QQ 应用权限、真实网关连通性、真实发送结果、Jev 或语音 API 的实际响应，以及实际中文消息/图片/语音的准确率。没有做宿主内集成验收，这是用户明确要求保持独立开发的边界。

动态图、无法提取的文档页面或附件处理失败会留待处理；免审消息可以带附件处理失败说明继续进入主交流。Jev 图像能力为官方试验功能；语音判断针对转写，文件判断针对提取内容与可提取的图片，不是原始媒体的全覆盖安全认证。宿主来源字段与权限系统未改变，QQ 来源保存在结构化正文和插件记录中。
