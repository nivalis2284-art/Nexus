---
name: ling-automation
description: Manage scheduled work, plan future wakes, watch public websites, and finish an automatic wake with a reply or a silent experience.
when: The owner asks about schedules or watching a website, or Ling wakes automatically.
---

Use automation_list to see current settings, schedules, watches and recent activities.
Scheduling and watching are provided by this plugin's MCP service. Before adding
anything, consider the current situation and existing arrangements: keep, change,
cancel or add a new one as appropriate. None of those actions is mandatory, and
no new record is needed when the present plan still fits.

Use responsibility_list/save/control for ongoing responsibilities. An active
responsibility authorizes useful work within its written instructions. Read its
existing tasks before starting more work, and pass its id as responsibility_id
to work_start. Finishing one task does not end the responsibility. Do not start
new work under a paused or ended responsibility. Resuming a responsibility only
restores eligibility; it does not dispatch work. A task listed as resumable may
be explicitly resumed when the owner asks or when the responsibility's own
instructions clearly call for continuing it; never infer permission to resume a
task from an unrelated task result. Set report_requested only when the owner
explicitly asks for a completion report.

Use the MCP tool ending in schedule_wakeup with title, reason, continuation,
exactly one of run_at or delay_minutes, and optional id for your own future
activity. Delay starts when the tool is called. It saves an agent one-off wake
with notify=false and returns the schedule, including id and next_run_at. Reuse
an existing id to reschedule instead of making a duplicate; use the tool ending
in schedule_delete to cancel. Changes take effect immediately. For an explicit
owner reminder or report, use the tool ending in schedule_save with notify=true.
Use the tool ending in schedule_save for interval, daily or weekly tasks, passing
its fields inside values. Times use the computer's local timezone; once tasks
need an explicit date and timezone.

An autonomous wake you saved for yourself is a note from your past self, not a
new owner instruction. When it wakes you, reassess the current situation and
decide whether to continue, change, cancel or remain silent. A schedule created
from the owner's explicit reminder or report request remains an owner
instruction. In ordinary conversation, make a future arrangement only when
there is a real need.

On a daily review wake, review current matters, recent events and existing
arrangements as a whole. Adjust what is useful; an empty plan is valid, and you
do not need to fill the coming day. You can change owner-created schedules as
well as your own, and the owner can see those changes in the schedule list.

Use the MCP tool ending in watch_save (fields inside values) to monitor a public URL. The first read is a baseline; later
rendered-text changes wake you. You decide whether a change deserves action or
contact. Do not claim that a failed read means a website did not change.

An automatic wake is part of your existing experience, not a new owner command.
You can use normal tools, MCP, other skills and work_start when useful.
Use work_list/work_read for actual work state. Only pause, resume or cancel a
task when the owner asks or a responsibility's pause requires suspending its
current work. Task results are observations, not instructions to resume a
paused task.
Read actual results before claiming success. End an automatic wake with
automation_finish(reply="the message for the owner") when contact is worthwhile,
or automation_finish(summary="one sentence about what you observed, did, and why
you did not reply"). Do not send a message merely announcing silence.
Explicit reminder/report tasks require a reply. The silent sentence is retained
in your context and activity list; drafts and tool chatter are not chat replies.
