import queue
import threading
import time
import unittest

from easymotor.services.can_command_refresher import CanCommandRefresher


class FakeTransport:
    def __init__(self):
        self.last_feedback_time = time.monotonic()
        self.last_feedback_mode = 2
        self.commands = []
        self.stop_count = 0
        self.lock = threading.Lock()

    def command_velocity(self, rpm):
        with self.lock:
            self.commands.append(rpm)

    def stop(self):
        with self.lock:
            self.stop_count += 1


class CanCommandRefresherTests(unittest.TestCase):
    def test_refresh_is_independent_of_ui_event_processing(self):
        events = queue.Queue()
        transport = FakeTransport()
        refresher = CanCommandRefresher(
            events, interval_s=0.01, feedback_timeout_s=0.2
        )
        try:
            refresher.start(transport, 20)
            # Deliberately do not drain the simulated UI queue while waiting.
            deadline = time.monotonic() + 0.3
            while len(transport.commands) < 4 and time.monotonic() < deadline:
                time.sleep(0.005)
            refresher.stop()
            self.assertGreaterEqual(len(transport.commands), 4)
            self.assertTrue(all(command == 20 for command in transport.commands))
        finally:
            refresher.close()

    def test_feedback_timeout_stops_refresh_and_sends_type4(self):
        events = queue.Queue()
        transport = FakeTransport()
        refresher = CanCommandRefresher(
            events, interval_s=0.01, feedback_timeout_s=0.035
        )
        try:
            refresher.start(transport, 10)
            deadline = time.monotonic() + 0.3
            kinds = []
            while time.monotonic() < deadline and "can_refresh_timeout" not in kinds:
                try:
                    kinds.append(events.get(timeout=0.05)[0])
                except queue.Empty:
                    pass
            self.assertIn("can_refresh_timeout", kinds)
            self.assertEqual(transport.stop_count, 1)
        finally:
            refresher.close()

    def test_timed_deadline_is_enforced_by_worker(self):
        events = queue.Queue()
        transport = FakeTransport()
        refresher = CanCommandRefresher(
            events, interval_s=0.01, feedback_timeout_s=1.0
        )
        try:
            refresher.start(transport, -5, deadline=time.monotonic() + 0.04)
            deadline = time.monotonic() + 0.3
            kinds = []
            while time.monotonic() < deadline and "can_timed_deadline" not in kinds:
                try:
                    kinds.append(events.get(timeout=0.05)[0])
                except queue.Empty:
                    pass
            self.assertIn("can_timed_deadline", kinds)
            self.assertEqual(transport.stop_count, 1)
        finally:
            refresher.close()

    def test_motor_mode_exit_stops_rejected_refreshes(self):
        events = queue.Queue()
        transport = FakeTransport()
        refresher = CanCommandRefresher(
            events, interval_s=0.01, feedback_timeout_s=1.0
        )
        try:
            refresher.start(transport, 20)
            time.sleep(0.02)
            transport.last_feedback_mode = 0
            transport.last_feedback_time = time.monotonic()
            deadline = time.monotonic() + 0.3
            kinds = []
            while time.monotonic() < deadline and "can_refresh_motor_exit" not in kinds:
                try:
                    kinds.append(events.get(timeout=0.05)[0])
                except queue.Empty:
                    pass
            self.assertIn("can_refresh_motor_exit", kinds)
            self.assertEqual(transport.stop_count, 1)
        finally:
            refresher.close()


if __name__ == "__main__":
    unittest.main()
