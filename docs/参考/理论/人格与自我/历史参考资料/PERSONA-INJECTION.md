# 人设注入、Skill 与编辑说明

本文说明灵的人设栏目、文件型人设条目、普通 Skill 管理，以及对应的 HTTP 和聊天接口。人设与 Skill 位于启动时的个人数据目录，不属于当前工作区项目。

## 人设的组成

侧栏「人设与自我」使用一份完整文档：

```text
document = {
  sections: [{id, title, content}],
  entries: [{id=name, name, description, body, enabled}]
}
```

`sections` 按数组顺序全文注入。首次打开时提供两个空栏目：「核心设定」和「回复原则」；可以新增、改名、编辑正文、删除及调整顺序。栏目 `id` 在改名后保持不变，新栏目由服务端分配。

`entries` 是文件型人设条目。`name` 是英文 slug（小写字母、数字和连字符），也是文件夹名和条目 `id`。`description` 是条目摘要，不能为空；`body` 是详细正文，可以留空；`enabled` 决定条目是否参与人设目录。条目的正文只保存在 `persona-skills/<name>/SKILL.md`，不复制到 `persona.json`。`persona.json` 只保存栏目、条目顺序、`name` 和启用状态，避免同一正文出现两份。

条目改名时，服务端将原文件夹移动到新的 slug 文件夹，`id` 随文件夹名变为新的 slug；已有条目的正文随之保留。删除条目不保留其文件夹。若新名称与现有文件夹冲突，保存会被拒绝。

## 注入范围

| 场景 | 是否注入人设 | 是否提供修改入口 |
| --- | --- | --- |
| 灵的聊天 | 是 | 仅当用户在聊天中明确要求修改时使用 `persona_manage` |
| 定时、自由、每日计划或监听触发的唤醒 | 是 | 否，自动唤醒不提供修改工具 |
| 普通会话 | 否 | 否 |
| 独立任务 | 否 | 否 |
| 子代理 | 否 | 否 |

每次灵请求都会重新读取栏目和文件型条目：所有栏目正文进入系统上下文；启用条目的 `name` 和 `description` 进入目录；停用条目不进入。详细 `body` 不常驻，模型需要具体背景时通过 `read_skill(name="<name>", category="persona")` 读取，停用条目不能通过该入口取回正文。

人设与灵的角色说明、项目指令、普通 Skill 目录和任务状态一起组装，不替代实际工具结果。普通会话、独立任务和子代理继续使用各自的上下文。

这里没有自动反思、自动改写或定时迭代流程。一次对话、任务或唤醒结束，不会自动生成人设内容。修改只来自用户在编辑界面的保存，或用户明确要求后的 `persona_manage` 操作。

## 普通 Skill 的独立界面

侧栏「技能」单独管理普通 Skill，与人设条目分开。列表显示每个 Skill 的生效来源：个人、当前项目或内置。同名时项目技能覆盖个人技能，个人技能覆盖内置技能；被覆盖的项仍显示，并标明“被更高优先级同名技能覆盖”。

个人和项目 Skill 可以新建、编辑、改名和删除。个人 Skill 写入 `home/skills/<name>/SKILL.md`，项目 Skill 写入 `<workspace>/.nexus/skills/<name>/SKILL.md`。新建使用文件夹形式；已有平铺 Markdown 文件仍可编辑，额外的文件头字段会保留。

内置 Skill 显示在列表中，只能查看，不能通过界面保存或删除。Skill 的摘要、正文和额外元数据以对应 `SKILL.md` 文件为唯一正文来源；编辑界面不会另存一份正文副本。改名会移动 Skill 文件夹并更新文件头中的 `name`。

普通 Skill 在每轮模型请求前按当前工作区和用户目录重新发现。手动修改文件后，打开或刷新「技能」界面会重新读取列表；下一次聊天或任务请求也会使用最新的文件内容，不需要重启服务。人设条目则在下一次读取人设时重新组装，刷新「人设与自我」或发起下一次灵请求即可看到文件中的最新正文。

`read_skill` 用于按需读取普通 Skill；`category="persona"` 只用于读取已保存且启用的人设条目正文。普通 Skill 的接口回归独立运行，人设 HTTP 测试只检查人设行为。

## 编辑、摘要建议与最近请求

编辑区保存栏目和条目。条目的摘要生成使用当前主会话的模型 profile 和 effort，只依据请求中的 `name` 与 `body` 生成建议文本。结果只返回编辑区，不写入人设文件，也不改变条目的启用状态。用户需要先采用建议，再保存条目；只有保存请求才改变人设文档。

保存成功后无需重启。已经发出的模型请求继续使用它开始时的内容，下一次灵请求重新读取保存后的人设。编辑区里尚未保存的草稿不会改变服务端保存的文档。

`preview` 是根据当前已保存文档计算出的下一次注入预览，包含所有栏目正文和启用条目的描述，不触发模型调用。`last_request` 是最近一次真实灵模型请求携带的人设上下文，包含 `content`、`time`、`source` 和 `model`；没有发生过请求时为 `null`。摘要生成、读取和保存人设都不会更新它。

## HTTP 接口

所有接口使用 JSON。

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `GET /api/persona` | 无 | 当前快照 |
| `PUT /api/persona` | 完整 `document` | 保存后的同一份快照 |
| `POST /api/persona/summary` | `{name,body}` | `{summary}`，只生成草稿 |

`GET` 和 `PUT` 使用同一种快照结构：

```json
{
  "persona": {
    "sections": [{"id": "core", "title": "核心设定", "content": "…"}],
    "entries": [{
      "id": "writing-style",
      "name": "writing-style",
      "description": "…",
      "body": "…",
      "enabled": true
    }]
  },
  "preview": "…",
  "last_request": null
}
```

`PUT` 接受 `{sections, entries}` 完整文档，新增、修改、删除和重排都以传入的数组为准。`id` 为空的栏目由服务端分配；已有栏目以 `id` 识别，标题可以修改。条目以 `name` 作为文件夹和最终 `id`，正文写入对应 `SKILL.md`。`POST /api/persona/summary` 的请求与响应形状不变。缺少模型配置或 API Key 时返回 `400`，模型侧生成失败返回 `502`；这两个失败路径都保留原有人设文件。

UI 和 HTTP 只接受当前的 `sections`/`description` 文档。旧的 `{core, entries:[{summary}]}` 形状不再由 HTTP 接收；这种形状只在 `PersonaStore` 读取既有本地文件时用于一次性迁移。

## 聊天修改工具

`persona_manage` 只在灵的聊天中可用，自动唤醒、普通会话、独立任务和子代理都不提供编辑工具。它支持以下动作：

| `action` | 参数 | 行为 |
| --- | --- | --- |
| `read` | 无 | 读取完整 document |
| `save_section` | `section:{title,content,id?}` | 无 `id` 时新增栏目；有 `id` 时更新对应栏目 |
| `delete_section` | `id` | 删除栏目 |
| `move_section` | `id,offset` | 按非零整数偏移调整栏目顺序，边界处截断 |
| `save_entry` | `entry:{name,description,body?,enabled?}`，`entry.id` 可选 | 新增条目，或按 `entry.id` 更新已有条目 |
| `delete_entry` | `id` | 删除条目及其文件夹 |

保存条目需要英文 slug 名称和非空 `description`；正文可以留空，`enabled` 默认 `true`。修改名称会同时改变条目 `id` 和文件夹。工具只应在用户明确要求修改时保存，推断出的偏好或草稿不自动写入。

## 本地存储

以下路径位于启动时选定的个人数据目录：

- `persona.json`：完整栏目，以及条目的名称、顺序和启用状态；不重复保存条目摘要或正文。
- `persona-skills/<name>/SKILL.md`：该条目的唯一正文文件，文件头保存名称和描述。
- `persona.json.legacy-backup`：首次读取旧格式时保留的原文件副本。
- `persona-last-request.json`：最近一次真实灵模型请求携带的人设快照。
- `skills/<name>/SKILL.md` 或 `<workspace>/.nexus/skills/<name>/SKILL.md`：独立管理的个人和项目普通 Skill。

旧格式迁移只为既有 `core/entries(summary)` 文件服务。新代码首次读取时把 `core` 放入「核心设定」栏目，补上空的「回复原则」，将旧条目 `id` 作为文件夹名、`summary` 写入 `description`、`body` 写入 `SKILL.md`，并保留顺序和启用状态。迁移后新保存和 HTTP 写回都使用当前格式，不再同步维护旧格式。

## 验证记录

2026-10-22 本轮在临时 home 和临时项目目录中实测，不调用真实模型，不修改日常 `.nexus-data` 人设与 Skill。另只读复制实际九条人设到隔离目录，逐项确认核心、摘要、正文、顺序和启用状态保持一致，原始备份逐字节一致，重载不重复迁移。

- `python -m pytest tests/test_persona_http.py -q`：9 项通过。使用真实标准库 HTTP 服务、临时 home 和模拟摘要函数，覆盖默认两栏目、栏目增删改名与顺序、条目启用停用和删除、改名后的 `persona-skills/<slug>/SKILL.md` 位置、保存后重载、手动修改 Skill 文件后的刷新、摘要草稿不落盘，以及旧 9 条通过本地文件迁移。
- `python -m pytest tests/test_persona_runtime.py -q`：6 项通过。使用模拟模型响应，覆盖灵聊天与唤醒的栏目和描述注入、普通会话/子代理/独立任务隔离、聊天 `save_section`/`move_section`/`save_entry`/`delete_section`/`delete_entry` 在下一次请求生效、按需读取最新正文及压缩后保留。
- `tests/test_persona_http.py` 只保留了迁移准备用的旧 `core`/`summary` 数据；其余断言均使用 `sections` 和 `description`。

自动化测试确认的是请求装配、存储分工和接口行为。实际人格表达、真实模型下的长期一致性以及浏览器完整交互仍需日常使用观察；本轮未重启日常实例，也未提交或推送。

整合后的相关 Python 测试共 124 项通过（本轮文件、接口与人设测试 37 项；既有 Skill、聊天、工作协调及上下文回归 87 项）。前端行为测试共 57 项通过，其中人设 9 项、技能管理 7 项、既有页面行为 41 项。相关 Python 静态检查、JavaScript 语法检查及差异空白检查通过。

正在运行的旧实例未热迁移数据。下次以新代码启动后，首次读取人设时自动迁移原有内容并保留备份；这是结构升级的一次性处理。之后编辑栏目或 Skill 文件不需要重启。
