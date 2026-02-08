import datetime

FREQ_EVERY_N_DAYS = "every_n_days"
FREQ_DAILY = "daily"
FREQ_WEEKDAYS = "weekdays"
FREQ_WEEKEND = "weekend"

WEEKDAY_LABELS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

def parse_weekdays(value):
    if not value:
        return set()
    days = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            days.add(int(token))
        except ValueError:
            continue
    return days


def serialize_weekdays(days):
    if not days:
        return ""
    return ",".join(str(day) for day in sorted(set(days)))


def parse_time_str(value):
    if not value:
        return None
    try:
        return datetime.time.fromisoformat(value)
    except ValueError:
        return None


def compute_next_send_datetime_from_params(
    now,
    frequency_type,
    frequency_days,
    weekdays,
    delivery_time,
    allow_immediate=False,
):
    schedule = _SimpleSchedule(
        frequency_type=frequency_type,
        frequency_days=frequency_days,
        weekdays=weekdays,
        delivery_time=delivery_time,
    )
    return compute_next_send_datetime(now, schedule, allow_immediate=allow_immediate)


def compute_next_send_datetime(now, schedule, allow_immediate=False):
    frequency_type = schedule.frequency_type or FREQ_EVERY_N_DAYS
    delivery_time = schedule.delivery_time

    if frequency_type == FREQ_WEEKEND:
        weekdays = {5, 6}
        return _next_weekday_occurrence(now, weekdays, delivery_time, allow_immediate)

    if frequency_type == FREQ_WEEKDAYS:
        weekdays = parse_weekdays(schedule.weekdays)
        if not weekdays:
            weekdays = {0, 1, 2, 3, 4}
        return _next_weekday_occurrence(now, weekdays, delivery_time, allow_immediate)

    if frequency_type == FREQ_DAILY:
        return _next_daily(now, delivery_time, allow_immediate)

    interval = max(1, int(schedule.frequency_days or 1))
    return _next_every_n_days(now, interval, delivery_time, allow_immediate)


class _SimpleSchedule:
    def __init__(self, frequency_type, frequency_days, weekdays, delivery_time):
        self.frequency_type = frequency_type
        self.frequency_days = frequency_days
        self.weekdays = weekdays
        self.delivery_time = delivery_time


def _combine(date_value, delivery_time, fallback_time):
    if delivery_time:
        return datetime.datetime.combine(date_value, delivery_time)
    return datetime.datetime.combine(date_value, fallback_time)


def _next_daily(now, delivery_time, allow_immediate):
    if delivery_time:
        candidate_date = now.date()
        if (not allow_immediate) or now.time() >= delivery_time:
            candidate_date += datetime.timedelta(days=1)
        return datetime.datetime.combine(candidate_date, delivery_time)
    if allow_immediate:
        return now
    return now + datetime.timedelta(days=1)


def _next_every_n_days(now, interval_days, delivery_time, allow_immediate):
    if allow_immediate and delivery_time and now.time() < delivery_time:
        return datetime.datetime.combine(now.date(), delivery_time)
    if allow_immediate and not delivery_time:
        return now
    target_date = now.date() + datetime.timedelta(days=interval_days)
    return _combine(target_date, delivery_time, now.time())


def _next_weekday_occurrence(now, weekdays, delivery_time, allow_immediate):
    candidate_date = now.date()
    if delivery_time:
        if (not allow_immediate) or now.time() >= delivery_time:
            candidate_date += datetime.timedelta(days=1)
    else:
        if not allow_immediate:
            candidate_date += datetime.timedelta(days=1)

    for _ in range(8):
        if candidate_date.weekday() in weekdays:
            return _combine(candidate_date, delivery_time, now.time())
        candidate_date += datetime.timedelta(days=1)

    return _combine(candidate_date, delivery_time, now.time())
