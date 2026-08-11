import unittest

from rs04_can import build_parameter_read, split_id
from rs04_long_run import LONG_RUN_PARAMETER_INDICES, LongRunSession


class LongRunSessionTests(unittest.TestCase):
    def test_long_run_allowlist_builds_only_type17_reads(self):
        self.assertEqual(
            LONG_RUN_PARAMETER_INDICES,
            (0x7005, 0x7019, 0x701B, 0x7026, 0x7028),
        )
        for index in LONG_RUN_PARAMETER_INDICES:
            frame = build_parameter_read(index)
            self.assertEqual(split_id(frame.arbitration_id), (17, 0x00FD, 0x7F))
            self.assertEqual(int.from_bytes(frame.data[0:2], "little"), index)
            self.assertEqual(frame.data[2:], bytes(6))

    def test_cycles_parameters_and_records_latency(self):
        session = LongRunSession(duration_s=10.0, interval_s=0.1, timeout_s=0.5)
        session.start(100.0)

        first = session.tick(100.0, "start")
        self.assertEqual(first, LONG_RUN_PARAMETER_INDICES[0])
        self.assertTrue(session.accept_response(first, 0, 100.012, "done"))
        self.assertIsNone(session.tick(100.05, "early"))

        second = session.tick(100.1, "second")
        self.assertEqual(second, LONG_RUN_PARAMETER_INDICES[1])
        self.assertTrue(session.accept_response(second, 0.25, 100.125, "second-done"))
        self.assertEqual(session.tx_count, 2)
        self.assertEqual(session.response_count, 2)
        self.assertAlmostEqual(session.average_latency_ms, 18.5)
        self.assertAlmostEqual(session.max_latency_ms, 25.0)
        self.assertEqual([record.result for record in session.records], ["ok", "ok"])

    def test_timeout_advances_to_next_parameter(self):
        session = LongRunSession(duration_s=10.0, interval_s=0.1, timeout_s=0.5)
        session.start(0.0)
        first = session.tick(0.0, "first")
        self.assertAlmostEqual(session.elapsed_s(0.25), 0.25)
        second = session.tick(0.5, "timeout")

        self.assertNotEqual(first, second)
        self.assertEqual(session.timeout_count, 1)
        self.assertEqual(session.tx_count, 2)
        self.assertEqual(session.records[0].result, "timeout")

    def test_rejection_and_send_failure_are_separate_outcomes(self):
        session = LongRunSession(duration_s=10.0)
        session.start(5.0)
        first = session.tick(5.0, "first")
        self.assertTrue(session.reject_response(first, "status=1", "rejected"))
        second = session.tick(5.1, "second")
        self.assertTrue(session.send_failed("port closed", "failed"))

        self.assertEqual(session.rejection_count, 1)
        self.assertEqual(session.send_failure_count, 1)
        self.assertEqual(
            [record.result for record in session.records], ["rejected", "send_failed"]
        )

    def test_completion_stops_without_sending_another_request(self):
        session = LongRunSession(duration_s=1.0)
        session.start(10.0)
        self.assertIsNone(session.tick(11.0, "complete"))
        self.assertFalse(session.running)
        self.assertEqual(session.stop_reason, "completed")

    def test_completion_waits_for_the_last_outstanding_response(self):
        session = LongRunSession(duration_s=1.0, timeout_s=0.5)
        session.start(10.0)
        index = session.tick(10.95, "last")
        self.assertIsNone(session.tick(11.0, "deadline"))
        self.assertTrue(session.running)
        self.assertTrue(session.accept_response(index, 0, 11.1, "response"))
        self.assertIsNone(session.tick(11.1, "complete"))
        self.assertFalse(session.running)
        self.assertEqual(session.tx_count, session.response_count)

    def test_rejects_unsafe_scheduler_configuration(self):
        with self.assertRaises(ValueError):
            LongRunSession(interval_s=0.01)
        with self.assertRaises(ValueError):
            LongRunSession(interval_s=0.5, timeout_s=0.1)
        with self.assertRaises(ValueError):
            LongRunSession(parameter_indices=(0xFFFF,))

    def test_fixed_step_schedule_does_not_accumulate_float_drift(self):
        session = LongRunSession(duration_s=10.0)
        session.start(0.0)
        now = 0.0
        while session.running:
            index = session.tick(now, str(now))
            if index is not None:
                session.accept_response(index, 0, now + 0.005, str(now + 0.005))
            now += 0.01
        self.assertEqual(session.tx_count, 100)
        self.assertEqual(session.response_count, 100)


if __name__ == "__main__":
    unittest.main()
