# Nexus 插件页面协议 v1

SDK 1.1.0 新增宿主渲染页面。插件提供结构、数据与操作，Nexus 生成 DOM，统一主题、控件和动效。特殊页面仍可使用原有 HTML／iframe 模式；同一插件可以同时声明两种页面。

## 声明与入口

```json
{
  "requires_host": {"ui": 1},
  "pages": [
    {"id": "main", "title": "管理", "renderer": "host", "ui_version": 1, "action": "ui"},
    {"id": "custom", "title": "特殊页面", "path": "web/index.html"}
  ]
}
```

`ui` 是页面渲染能力版本，可用 `capabilities.require` 检查，不是一个 RPC 方法命名空间。旧宿主会拒绝不支持的能力依赖；旧页面无需增加此依赖。未填写 `renderer` 的页面继续按 iframe 加载。

宿主通过 `POST /api/plugins/{plugin_id}/pages/{page_id}` 进入已声明页面，再调用清单中的 `action`。Python 接收 `call("ui", request)`；纯 Node 使用现有 `call_handler`，收到 `{action:"ui", values:request}`。页面必须在插件就绪后打开，停用后不能调用。

```json
{
  "event": "action",
  "page_id": "main",
  "action": "search",
  "arguments": {"page": 1},
  "values": {"query": "某件事情"},
  "context": {},
  "request_id": "宿主生成的操作标识"
}
```

首次打开为 `event:"open"`，没有业务 `action`。`page_id` 由宿主从页面声明确定。`values` 包含当前主页面与弹窗的全部字段，保持数字、布尔和选项值的 JSON 类型。`arguments` 来自被点击按钮，分页组件自动补入目标 `page`。

响应是完整页面描述，不是 HTML 或补丁：

```json
{
  "ui_version": 1,
  "context": {"page": 1},
  "components": [
    {"id": "query", "type": "field", "name": "query", "label": "检索", "value": ""},
    {"id": "search", "type": "button", "label": "检索", "action": "search", "variant": "primary"},
    {"id": "results", "type": "list", "items": [], "empty": "没有找到结果。"}
  ]
}
```

`context` 为 JSON 对象，随下次操作原样送回；关闭页面后释放。它适合页码、选择项、独立对话 ID，不替代插件的业务存储。弹窗是否打开由当次响应决定，无需把开关状态放入 `context`。关闭弹窗是本地交互，不调用后台。

## 组件

每个组件有稳定、非空且页面内唯一的 `id`。组件、列表项、表格行和弹窗的 ID 不能重复；字段 `name` 在主页面与弹窗之间也必须唯一。插件决定内容及行列分组，不传 CSS、class、HTML、JavaScript、像素尺寸或动画参数。未支持的类型、属性与协议版本会显示错误。

| type | 内容属性 |
| --- | --- |
| `section` | 可选 `title`、`description`，`children` 组件数组 |
| `row` / `column` | `children`；宿主负责换行及窄屏布局 |
| `text` / `badge` | `text`，可选 `tone`: `default`、`muted`、`success`、`error` |
| `code` | `text`，用于文本或格式化 JSON，宿主限制展示高度并允许滚动 |
| `field` | `name`、`label`；可选 `control`、`value`、`placeholder`、`help`、`required`、`readonly`、`disabled`、`min`、`max`、`step`、`options` |
| `button` | `label`、`action`，可选 `arguments`、`variant`、`disabled`、`confirm` |
| `list` | `items`、可选 `empty`；每项为 `{id,title,text?,meta?,actions?:按钮数组}` |
| `table` | `columns:[{key,label}]`、`rows:[{id,cells:{列key:标量值},actions?:按钮数组}]`、可选 `empty` |
| `pagination` | `page`、`total_pages`（从 1 开始）、`action`、可选 `arguments` |

- `field.control` 默认 `text`，也支持 `textarea`、`number`、`select`、`switch`。文本值为字符串；数字为空时是 `null`；开关值是布尔值；选项为 `[{label,value}]`，`value` 是 JSON 标量，宿主不把数字或布尔转换成字符串。
- 显式返回字段 `value` 会更新输入；省略 `value` 则保留仍存在字段的草稿。稳定 ID 与控件类型不变时复用元素；控件消失、类型或 ID 改变后草稿不再保留。`readonly` 对文本／数字生效为只读，对选择／开关生效为不可修改。
- `required`、数值范围和 `step` 使用浏览器校验，操作前检查全部字段。`step` 默认允许小数。插件负责业务校验。
- `button.variant` 默认 `secondary`，支持 `primary`、`danger`。`confirm` 使用宿主确认弹窗，格式为 `{title,description?,submitLabel?}`。`action:"$close"` 只关闭当前弹窗，不发起后台操作；其他 `$` 开头操作保留给宿主。
- 表格单元格按纯文本显示；对象请由插件转换成文本。行内操作通过按钮 `arguments` 携带记录 ID。

## 弹窗、错误与生命周期

响应可增加 `dialog:{id,title,description?,components}`，由宿主打开或更新弹窗；响应不带 `dialog` 则关闭。宿主提供关闭按钮、Escape、焦点进入与关闭后恢复，以及统一入退场动效。响应还支持：

```json
{
  "notice": {"text": "已保存。", "tone": "success"},
  "errors": {"字段name": "请修正此字段。"}
}
```

`notice.tone` 支持 `success`、`error`；`errors` 只能引用当前返回页面中的字段。存在字段错误时，应继续返回编辑弹窗及原输入。Python 抛异常／Node 拒绝请求时，宿主显示操作错误，保留已有页面与草稿，允许用户重试；初次打开失败提供重新读取按钮。

同一页面同时处理一个操作，处理中阻止重复提交并显示状态。稳定 ID 对应元素会复用；普通状态刷新不会重建页面。页面切换、关闭或插件停用后取消前端等待并忽略迟到响应；这不等同于撤销已提交的业务操作。可能产生副作用的操作应利用 `request_id` 做业务去重，不自动重试。

首版只在操作响应和手动刷新后更新内容；不提供后台推送、脚本表达式或实时流式传输。SDK 示例的流式按钮读取事件后统一展示。宿主直接响应自己的主题、配色、动效速度和减少动态效果偏好，插件无需同步这些设置。

## 示例与发布

Python、纯 Node 示例均使用 `view.json` 声明布局，各自的页面控制器补入动态数据并处理操作。示例备注保存到示例插件自己的设置中，与记忆插件无关；只在主动点击生成／发送时调用模型。

运行 `python mobile-bridge/dev/build_host_sdk.py` 生成 1.1.0 资料包与两个可安装示例，输出到 `mobile-bridge/releases/plugins`，不安装或启用；旧 1.0.0 包保留。Node 示例仍要求已有 Node.js 22+。
