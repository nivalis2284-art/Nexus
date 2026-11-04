---
name: followup-arrangements
description: 为所有者创建、管理定时交代和关注事项，或在指定后台工作发生变化后继续执行。
when: 所有者要求稍后执行、提醒、重复任务、持续关注，或衔接后台工作时
---

使用 followup_list/read/save/control 管理安排。先理解所有者的目标，在其交代范围内自行选择合适的频率和方法；需要重要取舍或缺少信息时提问。不要为了设置安排发明任务，也不要反复请求已经给出的授权。

followup_save 的 values 字段：title、goal、trigger 必填；context 保存未来执行必需的背景。可选 report_mode（always 每次汇报，changes 首轮基线后按需汇报）、report_when（自然语言汇报要求）、end_when（结束条件）、workspace、provider_id、model_id、effort。未指定的项目和模型固定为创建时的当前选择。更新时传 arrangement_id，只填写需要修改的字段；运行中的安排先暂停并等待工作停止。

trigger 使用以下一种格式：

- 单次延迟：`{"kind":"once","delay_seconds":600}`。
- 单次日期：`{"kind":"once","at":"2026-11-05T09:00"}`，无偏移时随 Windows 本地时区；带偏移的日期是明确时刻。
- 固定间隔：`{"kind":"interval","seconds":7200}`。
- 每天／工作日：`{"kind":"daily","time":"09:00"}` 或 `{"kind":"weekdays","time":"09:00"}`。
- 每周：`{"kind":"weekly","days":[0,2,4],"time":"09:00"}`，0 周一，6 周日。
- 每月：`{"kind":"monthly","day":31,"time":"09:00"}`；不存在该日期的月份跳过；day=0 表示每月最后一天。
- 工作事件：`{"kind":"event","task_id":"实际工作ID","events":["completed"],"repeat":false}`。状态可选 completed、paused、failed、cancelled、waiting_user、blocked。先用 work_list 确认对象；只监听创建后的新事件。repeat=true 持续监听。

保存不立即执行。向所有者说明实际保存的安排、时区、任务和汇报方式；明确要求现在运行时才调用 followup_control(operation="run")。停机、休眠、停用期间的触发不补跑。同一安排运行期间多次触发合并为一次待执行。暂停或删除同时停止当前工作；历史保留。

执行安排时正常使用现有工具和权限，自行解决普通问题。不要把没有获取到信息说成没有变化。缺少信息或需要取舍时提出问题；不能继续时如实暂停。审批是现有审批，不是创建安排时一次性批准未来所有工具。

本插件发起的每轮工作用 followup_finish 完成：summary 写实际结果；context 留给下轮的必要状态、事实与教训；notify 和 report_text 表达是否值得汇报及内容；finished 表示整个关注目标已经达成，不是仅表示这次检查结束。无需变更就安静结束，首次基线和每次汇报模式由程序保证。不要用 task_finish 替代该结果提交工具。
