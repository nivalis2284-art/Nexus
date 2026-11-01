from nexus.plugins.api import EmptyArgs, ToolSpec


class Plugin:
    def tools(self, scope):
        return [ToolSpec('example_status', 'Read the example plugin status.',
                         EmptyArgs, self.status, read_only=True)]

    def status(self, context, arguments):
        return {'content': '示例插件已启用。'}


def create_plugin(host):
    return Plugin()
