"""Public host SDK v1 namespaces; direct and remote clients share the same contract."""
from .runtime import CAPABILITIES

class Namespace:
    def __init__(self, call, path=''):
        self._call, self._path = call, path

    def __getattr__(self, name):
        return Namespace(self._call, self._path + ('.' if self._path else '') + name)

    def __call__(self, **params):
        if self._path == 'models.stream':
            return stream(self._call, params)
        return self._call(self._path, params)


def stream(call, params):
    import time
    key = call('operations.start', {'method': 'models.generate', 'params': params})['operation_id']
    cursor = 0
    try:
        while True:
            page = call('operations.events', {'operation_id': key, 'after': cursor})
            cursor = page['cursor']
            for event in page['events']:
                if event['type'] == 'model.event':
                    yield event['data']
                if event['type'] == 'operation.done':
                    op = call('operations.get', {'operation_id': key})
                    if op['status'] != 'completed': raise ValueError(op['error'])
                    yield {'type': 'complete', 'result': op['result']}
                    return
            time.sleep(.05)
    finally:
        try:
            op = call('operations.get', {'operation_id': key})
            if op['status'] in ('pending', 'running'): call('operations.cancel', {'operation_id': key})
        except ValueError:
            pass
