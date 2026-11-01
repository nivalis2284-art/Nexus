class Plugin:
    def call(self, action, values):
        if action != 'ui':
            raise ValueError('未知操作')
        text = values.get('values', {}).get('text', '')
        return {'ui_version': 1, 'components': [
            {'id': 'text', 'type': 'field', 'name': 'text', 'label': '输入文字', 'value': text},
            {'id': 'show', 'type': 'button', 'label': '显示', 'action': 'show', 'variant': 'primary'},
            {'id': 'result', 'type': 'text', 'text': text or '输入文字后点击显示。'},
        ]}


def create_plugin(host):
    return Plugin()
