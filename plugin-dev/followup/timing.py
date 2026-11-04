"""Calendar schedules follow the current Windows zone; intervals use elapsed seconds."""
import calendar
import math
from datetime import UTC, datetime, timedelta

from tzlocal import reload_localzone


def local_zone():
    return reload_localzone()


def validate(raw, now, zone):
    if not isinstance(raw, dict):
        raise TypeError('触发时间必须为对象')
    value = dict(raw)
    kind = value.get('kind')
    if kind == 'once':
        if 'delay_seconds' in value:
            seconds = value.pop('delay_seconds')
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError('延迟秒数必须为正数')
            value['at'] = datetime.fromtimestamp(now + seconds, UTC).isoformat()
        try:
            at = datetime.fromisoformat(value['at'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('请填写 ISO 日期时间，例如 2026-11-05T09:00') from None
        if (at if at.tzinfo else at.replace(tzinfo=zone)).timestamp() <= now:
            raise ValueError('单次执行时间必须在未来')
        return {'kind': kind, 'at': at.isoformat()}
    if kind == 'interval':
        seconds = value.get('seconds')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('间隔秒数必须为正数')
        return {'kind': kind, 'seconds': seconds}
    if kind not in ('daily', 'weekdays', 'weekly', 'monthly'):
        raise ValueError('未知时间类型')
    try:
        clock = datetime.strptime(value.get('time', ''), '%H:%M').strftime('%H:%M')
    except (TypeError, ValueError):
        raise ValueError('时间须为 HH:MM') from None
    result = {'kind': kind, 'time': clock}
    if kind == 'weekly':
        days = value.get('days')
        if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(7) for d in days):
            raise ValueError('每周日期须为 0（周一）到 6（周日）的非空数组')
        result['days'] = sorted(set(days))
    if kind == 'monthly':
        day = value.get('day')
        if type(day) is not int or not 0 <= day <= 31:
            raise ValueError('每月日期须为 1 到 31，或 0 表示最后一天')
        result['day'] = day
    return result


def next_time(schedule, after, zone):
    kind = schedule['kind']
    if kind == 'once':
        at = datetime.fromisoformat(schedule['at'])
        stamp = (at if at.tzinfo else at.replace(tzinfo=zone)).timestamp()
        return stamp if stamp > after else None
    if kind == 'interval':
        return after + schedule['seconds']
    hour, minute = map(int, schedule['time'].split(':'))
    date = datetime.fromtimestamp(after, zone).date()
    # A weekly date is within a week; a monthly 31st is within two months.
    for offset in range(63):
        candidate = date + timedelta(days=offset)
        if kind == 'weekdays' and candidate.weekday() >= 5:
            continue
        if kind == 'weekly' and candidate.weekday() not in schedule['days']:
            continue
        if kind == 'monthly' and candidate.day != (schedule['day'] or calendar.monthrange(candidate.year, candidate.month)[1]):
            continue
        value = datetime(candidate.year, candidate.month, candidate.day, hour, minute, tzinfo=zone)
        stamp = value.timestamp()
        # Do not run a nonexistent daylight-saving wall time at a different time.
        if datetime.fromtimestamp(stamp, zone).replace(tzinfo=None) != value.replace(tzinfo=None):
            continue
        if stamp > after:
            return stamp
    raise ValueError('无法计算下一次执行时间')


def describe(schedule):
    kind = schedule['kind']
    if kind == 'event':
        return f"工作 {schedule['task_id']}：{'、'.join(schedule['events'])}" + ('（持续）' if schedule['repeat'] else '（一次）')
    if kind == 'once':
        return '一次：' + schedule['at']
    if kind == 'interval':
        return f"每隔 {schedule['seconds']:g} 秒"
    prefix = {'daily': '每天', 'weekdays': '工作日', 'weekly': '每周', 'monthly': '每月'}[kind]
    if kind == 'weekly':
        prefix += '、'.join('一二三四五六日'[d] for d in schedule['days'])
    if kind == 'monthly':
        prefix += str(schedule['day']) + ' 日' if schedule['day'] else '最后一天'
    return prefix + ' ' + schedule['time']
