"""Normalize authenticated QQ events without inferring authority from content."""
from __future__ import annotations

from copy import deepcopy

EVENTS = {'C2C_MESSAGE_CREATE', 'GROUP_AT_MESSAGE_CREATE', 'GROUP_MESSAGE_CREATE'}


def normalize(event, data):
    if event not in EVENTS:
        raise ValueError('不支持的 QQ 消息事件')
    author = data.get('author') or {}
    group = event != 'C2C_MESSAGE_CREATE'
    sender = author.get('member_openid' if group else 'user_openid') or author.get('id')
    target = data.get('group_openid') if group else sender
    if not isinstance(sender, str) or not sender or not isinstance(target, str) or not target or not data.get('id'):
        raise ValueError('QQ 消息缺少发送者、会话或消息标识')
    ext = (data.get('message_scene') or {}).get('ext') or []
    index = next((item.split('=', 1)[1] for item in ext
                  if isinstance(item, str) and item.startswith('msg_idx=')), '')
    return {'source': 'qq', 'event': event, 'message_id': str(data['id']),
            'message_index': index, 'timestamp': str(data.get('timestamp') or ''),
            'sender': {'kind': 'group' if group else 'c2c', 'id': sender,
                       'name': str(author.get('username') or ''), 'bot': bool(author.get('bot'))},
            'conversation': {'kind': 'group' if group else 'c2c', 'id': target},
            'text': str(data.get('content') or ''),
            'attachments': deepcopy(data.get('attachments') or []),
            'quoted': deepcopy(data.get('msg_elements') or []),
            'card': deepcopy(data.get('ark_data') or {})}


def matches(identities, envelope):
    sender, conversation = envelope['sender'], envelope['conversation']
    return any(item.get('kind') == sender['kind'] and item.get('id') == sender['id']
               and (sender['kind'] != 'group' or item.get('group_id') == conversation['id'])
               for item in identities)


def attachments(envelope):
    """Inspect nested quoted attachments as well as the outer message."""
    found = list(envelope['attachments'])

    def visit(elements):
        for item in elements:
            found.extend(item.get('attachments') or [])
            visit(item.get('msg_elements') or [])
    visit(envelope['quoted'])
    return found


def quoted_text(elements):
    return [{'author': {key: (item.get('author') or {}).get(key, '')
                        for key in ('id', 'username')},
             'content': str(item.get('content') or ''), 'card': item.get('ark_data') or {},
             'quoted': quoted_text(item.get('msg_elements') or [])} for item in elements]
