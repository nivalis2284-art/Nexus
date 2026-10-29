from .nexus_plugin_sdk import ChatClient


class Example:
    def __init__(self, host):
        self.client = ChatClient(host)

    def call(self, action, values):
        if action == 'snapshot':
            value = self.client.snapshot()
            return {'session_id': value['session_id'], 'running': value['running']}
        raise ValueError('Unknown example action')


def create_plugin(host):
    return Example(host)
