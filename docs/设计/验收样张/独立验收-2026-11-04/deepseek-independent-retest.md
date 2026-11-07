# Nexus 前端补充独立复测：手机插件列表

日期：2026-11-04。原始独立审查保留于 [deepseek-independent-review.md](deepseek-independent-review.md)，本文件只记录该 P2 修复的定向复测，不覆写原报告。使用同一独立测试上下文，不读取既有验收结论，未改产品或既有测试。

## 结论

**该 P2 缺陷关闭。** 390×844 和 390×600 下，三个插件以同宽、单列排列，列表没有局部横向裁切；第二插件可选择，详情与放大弹层保持正确对象。1440px 仍为两列，820px 仍保留原来的横向 240px 卡片轨道，没有发现修复带来的布局回归。

## 代码身份

`nexus/web/css/app.css` 的 SHA256 为 `be37f8a8b107383c32c86f81d65ba321700f29dfb216d3f1ab998e4c1ab0d065`，与 `frontend-independent-retest-baseline.json` 一致。预览副本 `docs/设计/预览资源/css/app.css` 也为同一 SHA256。新增测试 `tests/test_frontend_audit.py::test_mobile_plugins_are_full_width_rows` 的 SHA256 为 `f86278516db6c8e4da2712a98f88b0fd8de613f6abb4a06ce0b8bf657ba47041`，与重测清单一致。

原重测清单之外的四份指纹仍为已验证值：`dialogs.js` = `7b3c28b1…3bc`，`context.js` = `2cdadd71…9e6`，`settings.js` = `78abd8bf…9ea`，`ling.css` = `e9105928…809`。

## 测试方法

启动新的隔离服务，独立临时数据目录与端口，服务在复测结束后已停止：

```powershell
$out='C:\Users\34375\.codex\visualizations\2026\11\03\01a1a0db-cd87-7532-8dea-5c9bfe1cb9a1\retest'
$env:TMP=$out; $env:TEMP=$out
.venv\Scripts\python.exe -m nexus --home (Join-Path $out 'data') --port 8798 --no-browser
```

独立浏览器复测命令：

```powershell
$env:PYTHONPATH='C:\Python312\Lib\site-packages'
.venv\Scripts\python.exe C:\Users\34375\.codex\visualizations\2026\11\03\01a1a0db-cd87-7532-8dea-5c9bfe1cb9a1\probe_retest_plugins.py
```

新增既有测试的独立执行命令：

```powershell
$env:PYTHONPATH='C:\Python312\Lib\site-packages'
.venv\Scripts\python.exe -m pytest tests/test_frontend_audit.py::test_mobile_plugins_are_full_width_rows -q
```

结果：**1 passed，0 failed，0 skipped**（4.14 秒）。独立探针无页面脚本错误。

## 手机实测尺寸

三插件均使用较长名称，覆盖 cool/jade × day/night × 390×844/390×600 共 8 组。所有组别的读数**完全相同**：

| 测量项 | 修复后实测 |
|---|---|
| `.plug-list` 可视宽 / 内容宽 | **338px / 338px** |
| 列表横向范围 | x=26 到 x=364（视口 390px） |
| 计算样式横向溢出 | `visible` |
| 网格自动流 | `row` |
| 单列宽 | `338px` |
| 三张卡宽 / x | 均 **338px / x=26** |
| 三张卡顶部 | 427.625 / 519.625 / 611.625 px（顺序单列） |
| 页面宽 / 视口宽 | 390 / 390px |

修复前同一位置为列表 338px、内容 436px，第二卡 x=252..462；本次独立复测未再出现该越界。最短的 390×600 视口下三张卡仍完整收在 338px 单列内，后续内容可继续垂直阅读。

## 第二插件与详情交互

在 390×844 点击 `Plugin B with longer name`：

- 选中项为 `b`，详情标题为 `Plugin B with longer name`。
- 放大插件管理后，弹窗标题仍为该插件；弹窗为 x=16、y=128.94、宽 358、高 586.125，完整收在 390×844 内。
- Esc 关闭放大弹窗后，列表内详情仍在，焦点回到 `plug-expand`。

## 桌面与 820px 回归检查

1440×1000：`.plug-layout` 两列为 **333.094px / 538.906px**；`.plug-list` 为 `row` 单列轨道，三张卡同宽 333.09375px、纵向排列。这是桌面既有的左列表/右详情双列结构。

820×1000：`.plug-list` 仍为 `grid-auto-flow:column`，列为 **240px × 3**，内容宽 **768px**、可视宽 **568px**、`overflow-x:auto`；`.plug-layout` 为单列 568px。修复没有把桌面布局或 820px 原有横向轨道改成手机的单列。

## 证据

- [修复后手机截图](C:/Users/34375/.codex/visualizations/2026/11/03/01a1a0db-cd87-7532-8dea-5c9bfe1cb9a1/retest/mobile-fixed.png)
- [独立复测脚本](C:/Users/34375/.codex/visualizations/2026/11/03/01a1a0db-cd87-7532-8dea-5c9bfe1cb9a1/probe_retest_plugins.py)
- 修复位置：[app.css](H:/文档/LingNexus/Nexus/nexus/web/css/app.css:794)；420px 的 210px 旧覆盖已删除。

## 未验证范围

本轮只复测插件列表修复。原报告未验证的真实模型流式网络、真实附件、第三方已安装插件、真实宿主长介绍、逐项 iframe 主题交互、真机触屏与无障碍扫描均仍未覆盖；不能从本次通过推断这些范围已通过。存在名称更极端的无空格长串、运行时变更插件数量等未验证条件。

未安装依赖、提交、推送或还原修改；未读取旧验收通过结论，也未改动产品源码和现有测试。
