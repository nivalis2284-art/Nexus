"""Versioned particle contract; no host imports."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

KINDS = {'dialogue', 'tool_result', 'document'}
ROLES = {'user_statement', 'assistant_statement', 'tool_output', 'reported_statement'}

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def uid():
    return uuid.uuid4().hex

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def validate(p):
    expected = {'schema_version', 'id', 'content', 'kind', 'role', 'source', 'provenance', 'event_time', 'expandable', 'created_at', 'updated_at', 'manually_edited'}
    if set(p) != expected or p['schema_version'] != 1:
        raise ValueError('粒子字段或格式版本无效')
    if not isinstance(p['content'], str) or not p['content'].strip():
        raise ValueError('粒子正文不能为空')
    if p['kind'] not in KINDS or p['role'] not in ROLES:
        raise ValueError('粒子类别或角色无效')
    if set(p['source']) != {'label', 'plugin_id', 'tool_name'} or not isinstance(p['source']['label'], str):
        raise ValueError('来源格式无效')
    provenance = p['provenance']
    if set(provenance) != {'source_id', 'segment_ids', 'occurred_at'}:
        raise ValueError('来源定位格式无效')
    if not isinstance(provenance['segment_ids'], list) or not all(isinstance(x, str) for x in provenance['segment_ids']):
        raise ValueError('原文片段标识无效')
    if type(p['expandable']) is not bool or type(p['manually_edited']) is not bool:
        raise ValueError('粒子状态无效')
    if p['expandable'] and (not provenance['source_id'] or not provenance['segment_ids']):
        raise ValueError('可展开粒子必须关联原文')
    event = p['event_time']
    if event is not None:
        if not isinstance(event, dict) or set(event) != {'start', 'end'}:
            raise ValueError('事件时间需要 start/end')
        start, end = (datetime.strptime(event[k], '%Y-%m-%dT%H:%M') for k in ('start', 'end'))
        if start > end:
            raise ValueError('事件开始时间不能晚于结束时间')
    return p

DEFAULTS = {
    'capture_compression': True, 'capture_mode': 'interval', 'capture_interval_minutes': 60, 'capture_daily': '03:00',
    'process_mode': 'arrival', 'process_interval_minutes': 60, 'process_daily': '04:00',
    'timezone': 'UTC', 'timezone_confirmed': False, 'batch_chars': 24000,
    'passive_enabled': True, 'passive_limit': 5, 'passive_chars': 3000, 'similarity_threshold': 0.5,
    'llm_url': '', 'llm_model': '', 'embedding_url': '', 'embedding_model': '',
}
