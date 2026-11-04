"""Tool adapters only; scheduling and its dependency run in the isolated worker."""
import json
import uuid
from typing import Literal

from nexus.plugins.api import BaseModel, EmptyArgs, Field, ToolSpec


class ReadArgs(BaseModel):
    arrangement_id: str = Field(min_length=1)


class SaveArgs(BaseModel):
    values: dict = Field(description='Fields: title, goal, trigger, context, report_mode(always/changes), report_when, end_when, workspace, provider_id, model_id, effort. Trigger examples: {"kind":"interval","seconds":7200}; {"kind":"once","delay_seconds":600}; {"kind":"weekdays","time":"09:00"}; {"kind":"event","task_id":"...","events":["completed"],"repeat":false}. Read followup-arrangements skill for all calendar types.')
    arrangement_id: str | None = Field(default=None, description='Empty creates; existing id updates only supplied fields. Pause active work before editing.')


class ControlArgs(ReadArgs):
    operation: Literal['pause', 'resume', 'delete', 'run']


class FinishArgs(BaseModel):
    summary: str = Field(min_length=1, max_length=8000, description='Actual result, including any material limitation.')
    context: str = Field(default='', max_length=12000, description='Useful facts and conclusions for the next check.')
    notify: bool = Field(description='Whether this result deserves an owner update, following their instructions.')
    finished: bool = Field(description='Whether the overall arrangement goal/end condition is fulfilled, not merely this run.')
    report_text: str = Field(default='', max_length=8000)


class Plugin:
    def __init__(self, host):
        self.host = host

    def call(self, action, values):
        if action != 'ui':
            raise ValueError('未知操作')
        return self.host.call_service('worker', 'page', request=values)

    def tools(self, scope):
        def invoke(action):
            def handler(ctx, args):
                request_id = str(getattr(ctx, '_task_tool_call_id', '') or getattr(ctx, 'call_id', '') or uuid.uuid4().hex)
                result = self.host.call_service('worker', 'manage', action=action, arguments=args, request_id=request_id)
                return {'content': json.dumps(result, ensure_ascii=False)}
            return handler
        tools = [
            ToolSpec('followup_list', 'List saved arrangements, their next trigger and actual state.', EmptyArgs, invoke('list'), read_only=True),
            ToolSpec('followup_read', 'Read an arrangement and its execution history.', ReadArgs, invoke('read'), read_only=True),
            ToolSpec('followup_save', 'Create or update an arrangement requested by the owner. Save sufficient context; tell the owner the actual schedule. Does not run immediately.', SaveArgs, invoke('save')),
            ToolSpec('followup_control', 'Pause/resume/delete an arrangement or explicitly run it now. Pause/delete also stop current work. Preserve normal approval boundaries.', ControlArgs, invoke('control')),
        ]
        task_id = scope.get('task_id')
        if task_id:
            work = self.host.work.get(task_id=task_id)
            source = work.get('plugin_source', {})
            if work.get('plugin_id') == 'followup' and source.get('run_id'):
                def finish(ctx, args):
                    self.host.call_service('worker', 'manage', action='result', arguments={
                        **args, 'task_id': task_id, 'run_id': source['run_id']})
                    result = ctx.task_callbacks.finish({'summary': args['summary']})
                    return {'content': '本轮实际结果已提交。', **result}
                tools.append(ToolSpec('followup_finish', 'Finish this arrangement run. Judge whether to notify and whether the overall goal is done; save useful context for the next run. Use instead of task_finish.', FinishArgs, finish))
        return tools


def create_plugin(host):
    return Plugin(host)
