import datetime
from zoneinfo import ZoneInfo

from config import Config


APP_TIMEZONE = ZoneInfo(getattr(Config, "APP_TIMEZONE", "Europe/Rome"))
UTC = ZoneInfo("UTC")


def utc_now_naive():
    """Restituisce l'istante corrente in UTC senza timezone (formato DB legacy)."""
    return datetime.datetime.now(UTC).replace(tzinfo=None)


def utc_now_timestamp():
    """Restituisce il timestamp UNIX corrente calcolato in UTC."""
    return int(datetime.datetime.now(UTC).timestamp())


def local_now_naive():
    """Restituisce l'ora corrente nel fuso applicativo senza timezone."""
    return datetime.datetime.now(APP_TIMEZONE).replace(tzinfo=None)


def local_naive_to_utc_naive(value):
    """Converte una datetime naive locale in datetime naive UTC."""
    if value is None:
        return None
    localized = value.replace(tzinfo=APP_TIMEZONE)
    return localized.astimezone(UTC).replace(tzinfo=None)


def utc_naive_to_local_naive(value):
    """Converte una datetime naive UTC in datetime naive locale."""
    if value is None:
        return None
    utc_value = value.replace(tzinfo=UTC)
    return utc_value.astimezone(APP_TIMEZONE).replace(tzinfo=None)


def compute_next_send_utc(schedule, reference_utc_naive, allow_immediate=False):
    """Calcola il prossimo invio partendo da un riferimento UTC naive e restituisce UTC naive."""
    from schedule_utils import compute_next_send_datetime

    reference_local_naive = utc_naive_to_local_naive(reference_utc_naive)
    next_local_naive = compute_next_send_datetime(reference_local_naive, schedule, allow_immediate=allow_immediate)
    return local_naive_to_utc_naive(next_local_naive)
