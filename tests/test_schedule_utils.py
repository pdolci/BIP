import datetime

from schedule_utils import parse_time_str


def test_parse_time_str_accepts_standard_hh_mm():
    assert parse_time_str("07:30") == datetime.time(7, 30)


def test_parse_time_str_accepts_flexible_numeric_formats():
    assert parse_time_str("7:30") == datetime.time(7, 30)
    assert parse_time_str("7.30") == datetime.time(7, 30)
    assert parse_time_str("730") == datetime.time(7, 30)


def test_parse_time_str_accepts_named_slots():
    assert parse_time_str("dopo cena") == datetime.time(21, 0)


def test_parse_time_str_rejects_invalid_values():
    assert parse_time_str("25:00") is None
    assert parse_time_str("orario casuale") is None
