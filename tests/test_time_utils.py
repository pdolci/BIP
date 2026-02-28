import datetime
import unittest

from schedule_utils import FREQ_DAILY
from time_utils import compute_next_send_utc, local_naive_to_utc_naive, utc_naive_to_local_naive


class _Schedule:
    def __init__(self, frequency_type, delivery_time):
        self.frequency_type = frequency_type
        self.delivery_time = delivery_time
        self.frequency_days = 1
        self.weekdays = None


class TimeUtilsTests(unittest.TestCase):
    def test_utc_local_roundtrip(self):
        local_value = datetime.datetime(2026, 2, 14, 9, 15)
        utc_value = local_naive_to_utc_naive(local_value)
        converted_back = utc_naive_to_local_naive(utc_value)
        self.assertEqual(converted_back, local_value)

    def test_compute_next_send_utc_respects_local_delivery_time(self):
        schedule = _Schedule(FREQ_DAILY, datetime.time(9, 0))
        reference_utc = datetime.datetime(2026, 1, 1, 8, 30)

        next_send_utc = compute_next_send_utc(schedule, reference_utc, allow_immediate=False)

        self.assertEqual(next_send_utc, datetime.datetime(2026, 1, 2, 8, 0))


if __name__ == "__main__":
    unittest.main()
