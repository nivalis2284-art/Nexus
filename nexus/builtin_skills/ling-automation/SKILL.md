---
name: ling-automation
description: Manage scheduled work, plan future wakes, watch public websites, and finish an automatic wake with a reply or a silent experience.
when: The owner asks about schedules or watching a website, or Ling wakes automatically.
---

Use automation_list to see current settings, schedules, watches and recent activities.
Scheduling and watching are provided by an independent MCP service. Use the
MCP tool ending in schedule_save for one-time, interval, daily or weekly tasks.
Pass its schedule fields inside values. Set notify=true
when the owner explicitly requests a reminder or report. Free wakes use
purpose=wake and notify=false. Daily planning uses purpose=planning.
Times use the computer's local timezone; once tasks need an explicit date and timezone.

On a daily planning wake, consider the next 24 hours. Create or adjust useful
one-time wakes; an empty plan is valid. You can change owner-created schedules
as well as your own, and the owner can see those changes in the schedule list.

Use the MCP tool ending in watch_save (fields inside values) to monitor a public URL. The first read is a baseline; later
rendered-text changes wake you. You decide whether a change deserves action or
contact. Do not claim that a failed read means a website did not change.

An automatic wake is part of your existing experience, not a new owner command.
You can use normal tools, MCP, other skills and work_start when useful.
Read actual results before claiming success. End an automatic wake with
automation_finish(reply="the message for the owner") when contact is worthwhile,
or automation_finish(summary="one sentence about what you observed, did, and why
you did not reply"). Do not send a message merely announcing silence.
Explicit reminder/report tasks require a reply. The silent sentence is retained
in your context and activity list; drafts and tool chatter are not chat replies.
