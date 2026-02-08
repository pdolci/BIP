import datetime

WEEKDAY_CHOICES = ["0", "1", "2", "3", "4", "5", "6"]


def parse_delivery_time(raw_value):
    if not raw_value:
        return None

    normalized = raw_value.strip().lower()
    aliases = {
        "dopo cena": datetime.time(hour=20, minute=30),
        "colazione": datetime.time(hour=7, minute=30),
        "mattina": datetime.time(hour=7, minute=30),
        "pranzo": datetime.time(hour=13, minute=0),
        "sera": datetime.time(hour=20, minute=0),
    }

    if normalized in aliases:
        return aliases[normalized]

    try:
        return datetime.datetime.strptime(normalized, "%H:%M").time()
    except ValueError:
        return None


def compute_next_send_date(base_date, frequency_mode, frequency_days, frequency_weekdays, delivery_time):
    pivot = base_date
    target_time = delivery_time or datetime.time(hour=8, minute=0)

    if frequency_mode == "daily":
        candidate = pivot + datetime.timedelta(days=1)
    elif frequency_mode == "weekend":
        candidate = pivot + datetime.timedelta(days=1)
        while candidate.weekday() not in {5, 6}:
            candidate += datetime.timedelta(days=1)
    elif frequency_mode == "weekdays":
        selected = {int(day) for day in (frequency_weekdays or "").split(",") if day in WEEKDAY_CHOICES}
        if not selected:
            selected = {0, 2, 4}
        candidate = pivot + datetime.timedelta(days=1)
        while candidate.weekday() not in selected:
            candidate += datetime.timedelta(days=1)
    else:
        interval = max(1, frequency_days)
        candidate = pivot + datetime.timedelta(days=interval)

    return datetime.datetime.combine(candidate.date(), target_time)


def describe_frequency(schedule):
    if schedule.frequency_mode == "daily":
        return "giornaliera"
    if schedule.frequency_mode == "weekend":
        return "solo weekend"
    if schedule.frequency_mode == "weekdays":
        mapping = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
        days = [mapping[int(day)] for day in (schedule.frequency_weekdays or "").split(",") if day in WEEKDAY_CHOICES]
        return f"giorni specifici: {', '.join(days) if days else 'Lun, Mer, Ven'}"
    return f"ogni {schedule.frequency_days} giorni"
