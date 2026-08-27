import math
import unittest

from easymotor.protocols.can_motor import MotorFeedback
from easymotor.services.interactive_demo import (
    InteractiveDemoController,
    InteractiveDemoMode,
)


def feedback(position=0.4, velocity=0.0):
    return MotorFeedback(1, 2, 0, position, velocity, 0.0, 25.0)


class InteractiveDemoControllerTests(unittest.TestCase):
    def test_flywheel_is_unpowered_while_waiting_for_a_push(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        command = controller.command(feedback(velocity=0.0), 1.0)
        self.assertEqual(command.velocity_rad_s, 0.0)
        self.assertEqual(command.kd, 0.0)

    def test_spring_returns_to_captured_start_position(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.SPRING, feedback(position=0.4)
        )
        command = controller.command(feedback(position=0.8), 1.0)
        self.assertEqual(command.position_rad, 0.4)
        self.assertEqual((command.kp, command.kd), (1.5, 0.12))

    def test_damper_has_no_position_or_velocity_demand(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.DAMPER, feedback()
        )
        command = controller.command(feedback(velocity=1.0), 1.0)
        self.assertEqual(command.kp, 0.0)
        self.assertEqual(command.velocity_rad_s, 0.0)
        self.assertEqual(command.kd, 0.05)

    def test_detent_selects_nearest_fifteen_degree_position(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.DETENT, feedback(position=0.0)
        )
        command = controller.command(feedback(position=0.3), 1.0)
        self.assertAlmostEqual(command.position_rad, math.radians(15.0))

    def test_flywheel_latches_impulse_and_limits_speed(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.0), 1.0)
        command = controller.command(feedback(velocity=4.0), 1.01)
        self.assertAlmostEqual(
            command.velocity_rad_s,
            InteractiveDemoController.FLYWHEEL_MAX_RAD_S,
        )
        self.assertEqual(command.kd, 1.0)

    def test_flywheel_latches_slow_push_without_large_acceleration(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.0), 1.0)
        command = controller.command(feedback(velocity=0.08), 1.1)
        self.assertAlmostEqual(command.velocity_rad_s, 0.08 * 1.30)
        self.assertEqual(command.kd, 1.0)
        held = controller.command(feedback(velocity=0.08), 1.31)
        self.assertAlmostEqual(held.velocity_rad_s, 0.08 * 1.30)
        self.assertEqual(held.kd, controller.FLYWHEEL_HOLD_KD)

    def test_flywheel_does_not_follow_normal_coast_down_to_zero(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.10), 1.0)
        controller.command(feedback(velocity=0.20), 1.1)
        locked = controller.command(feedback(velocity=0.20), 1.31)
        self.assertAlmostEqual(locked.velocity_rad_s, 0.20 * 1.30)
        coast = controller.command(feedback(velocity=0.22), 1.8)
        self.assertAlmostEqual(coast.velocity_rad_s, 0.20 * 1.30)
        self.assertEqual(coast.kd, controller.FLYWHEEL_HOLD_KD)

    def test_flywheel_capture_has_a_hard_deadline_while_speed_keeps_changing(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.08), 1.0)
        for index in range(1, 10):
            controller.command(
                feedback(velocity=0.08 + index * 0.02),
                1.0 + index * 0.10,
            )

        locked = controller.command(feedback(velocity=0.18), 2.01)
        self.assertAlmostEqual(locked.velocity_rad_s, 0.26 * 1.30)
        self.assertEqual(locked.kd, controller.FLYWHEEL_HOLD_KD)

    def test_flywheel_does_not_discard_latched_speed_during_startup_grace(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.20), 1.0)
        locked = controller.command(feedback(velocity=0.20), 1.21)
        self.assertAlmostEqual(locked.velocity_rad_s, 0.20 * 1.30)
        for now_s in (1.40, 1.70, 1.95):
            held = controller.command(feedback(velocity=0.01), now_s)
            self.assertAlmostEqual(held.velocity_rad_s, 0.20 * 1.30)
            self.assertEqual(held.kd, controller.FLYWHEEL_HOLD_KD)

    def test_flywheel_latches_push_peak_instead_of_release_tail(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.10), 1.0)
        controller.command(feedback(velocity=0.25), 1.1)
        controller.command(feedback(velocity=0.05), 1.31)
        locked = controller.command(feedback(velocity=0.01), 1.52)
        self.assertAlmostEqual(locked.velocity_rad_s, 0.25 * 1.30)
        self.assertEqual(locked.kd, controller.FLYWHEEL_HOLD_KD)

    def test_flywheel_releases_drive_after_confirmed_hand_braking(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.20), 1.0)
        controller.command(feedback(velocity=0.20), 1.21)
        controller.command(feedback(velocity=0.10), 2.0)
        released = controller.command(feedback(velocity=0.10), 2.26)
        self.assertEqual(released.velocity_rad_s, 0.0)
        self.assertEqual(released.kd, 0.0)
        self.assertTrue(controller.flywheel_released_for_reverse)

    def test_flywheel_captures_reverse_push_after_releasing_drive(self):
        controller = InteractiveDemoController.create(
            InteractiveDemoMode.FLYWHEEL, feedback(velocity=0.0)
        )
        controller.command(feedback(velocity=0.20), 1.0)
        controller.command(feedback(velocity=0.20), 1.21)
        controller.command(feedback(velocity=0.10), 2.0)
        controller.command(feedback(velocity=0.10), 2.26)
        reversing = controller.command(feedback(velocity=-0.08), 2.30)
        self.assertLess(reversing.velocity_rad_s, 0.0)
        self.assertEqual(reversing.kd, controller.FLYWHEEL_CAPTURE_KD)


if __name__ == "__main__":
    unittest.main()
