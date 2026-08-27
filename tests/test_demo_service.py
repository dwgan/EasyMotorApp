import unittest

from easymotor.core.safety_policy import (
    DEMO_DEFAULT_DURATION_MS,
    DEMO_DEFAULT_SPEED_RPM,
    DEMO_SPEED_PRESETS_RPM,
    make_demo_plan,
)
from easymotor.services.demo_service import DemoAction, DemoPhase, DemoService


class DemoSafetyTests(unittest.TestCase):
    def test_demo_defaults_are_fixed_product_choices(self):
        self.assertEqual(DEMO_SPEED_PRESETS_RPM, (5, 30, 100))
        self.assertEqual(DEMO_DEFAULT_SPEED_RPM, 30)
        self.assertEqual(DEMO_DEFAULT_DURATION_MS, 5000)
        plan = make_demo_plan(-1, 30, False)
        self.assertEqual(plan.command_rpm, -30)
        self.assertEqual(plan.duration_ms, 5000)

    def test_invalid_demo_values_are_rejected(self):
        for args in ((0, 5, False, 5000), (1, 20, False, 5000), (1, 5, False, 6000)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                make_demo_plan(*args)

    def test_idle_motor_is_prepared_before_timed_run(self):
        service = DemoService()
        action = service.request_run(
            direction=1,
            speed_rpm=5,
            continuous=False,
            connected=True,
            mci_state=0,
            another_operation_active=False,
        )
        self.assertEqual(action, DemoAction.START_MOTOR)
        self.assertEqual(service.phase, DemoPhase.PREPARING)
        self.assertEqual(service.motor_ready(), DemoAction.RUN_TIMED)
        self.assertEqual(service.phase, DemoPhase.RUNNING)

    def test_continuous_run_requires_idle_then_explicit_start(self):
        service = DemoService()
        action = service.request_run(
            direction=-1,
            speed_rpm=100,
            continuous=True,
            connected=True,
            mci_state=0,
            another_operation_active=False,
        )
        self.assertEqual(action, DemoAction.START_MOTOR)
        self.assertEqual(service.plan.command_rpm, -100)
        self.assertEqual(service.motor_ready(), DemoAction.RUN_CONTINUOUS)
        service.cancel()
        self.assertEqual(service.phase, DemoPhase.IDLE)
        self.assertIsNone(service.plan)

    def test_disconnected_busy_or_unsafe_state_is_rejected(self):
        cases = (
            dict(connected=False, mci_state=0, another_operation_active=False),
            dict(connected=True, mci_state=0, another_operation_active=True),
            dict(connected=True, mci_state=4, another_operation_active=False),
            dict(connected=True, mci_state=6, another_operation_active=False),
        )
        for state in cases:
            with self.subTest(state=state), self.assertRaises(ValueError):
                DemoService().request_run(
                    direction=1,
                    speed_rpm=5,
                    continuous=False,
                    **state,
                )


if __name__ == "__main__":
    unittest.main()
