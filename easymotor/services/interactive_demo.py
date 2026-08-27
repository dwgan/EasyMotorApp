"""Host-side MIT models for the customer interaction demonstrations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from easymotor.protocols.can_motor import (
    MitCommand,
    MotorFeedback,
    POSITION_MAX_RAD,
    POSITION_MIN_RAD,
)


class InteractiveDemoMode(str, Enum):
    SPRING = "spring"
    DAMPER = "damper"
    DETENT = "detent"
    FLYWHEEL = "flywheel"


@dataclass
class InteractiveDemoController:
    """Convert Type-2 feedback into bounded Type-1 demonstration commands.

    These intentionally modest gains are a customer-demo starting point. The
    firmware remains responsible for current, speed, watchdog, and fault limits.
    """

    mode: InteractiveDemoMode
    center_rad: float
    last_velocity_rad_s: float
    velocity_target_rad_s: float = 0.0
    last_update_s: float | None = None
    flywheel_capture_until_s: float = 0.0
    flywheel_capture_deadline_s: float = 0.0
    flywheel_capture_velocity_rad_s: float = 0.0
    flywheel_locked_at_s: float = 0.0
    flywheel_brake_since_s: float | None = None
    flywheel_released_for_reverse: bool = False

    REDUCTION = 9.0
    DETENT_STEP_RAD = math.radians(15.0)
    # MIT velocity is output-shaft rad/s. Keep this interaction demo inside
    # the familiar 30 motor-rpm envelope of the 1:9 customer product.
    FLYWHEEL_MAX_RAD_S = 30.0 / REDUCTION * (2.0 * math.pi / 60.0)
    FLYWHEEL_START_RAD_S = 0.04
    FLYWHEEL_RECAPTURE_MARGIN_RAD_S = 0.08
    FLYWHEEL_CAPTURE_SETTLE_S = 0.20
    FLYWHEEL_CAPTURE_MAX_S = 1.00
    FLYWHEEL_CAPTURE_CHANGE_RAD_S = 0.015
    FLYWHEEL_ASSIST_GAIN = 1.30
    FLYWHEEL_CAPTURE_KD = 1.0
    FLYWHEEL_HOLD_KD = 3.0
    FLYWHEEL_BRAKE_GRACE_S = 0.75
    FLYWHEEL_BRAKE_FRACTION = 0.65
    FLYWHEEL_BRAKE_CONFIRM_S = 0.25

    @classmethod
    def create(
        cls,
        mode: InteractiveDemoMode,
        feedback: MotorFeedback,
    ) -> "InteractiveDemoController":
        return cls(
            mode=mode,
            center_rad=feedback.position_rad,
            last_velocity_rad_s=feedback.velocity_rad_s,
        )

    def command(self, feedback: MotorFeedback, now_s: float) -> MitCommand:
        if self.mode == InteractiveDemoMode.SPRING:
            command = MitCommand(
                position_rad=self.center_rad,
                kp=1.5,
                kd=0.12,
            )
        elif self.mode == InteractiveDemoMode.DAMPER:
            command = MitCommand(kd=0.05)
        elif self.mode == InteractiveDemoMode.DETENT:
            offset = feedback.position_rad - self.center_rad
            target = self.center_rad + round(offset / self.DETENT_STEP_RAD) * self.DETENT_STEP_RAD
            command = MitCommand(
                position_rad=max(POSITION_MIN_RAD, min(POSITION_MAX_RAD, target)),
                kp=1.5,
                kd=0.08,
            )
        else:
            command = self._flywheel_command(feedback, now_s)
        self.last_velocity_rad_s = feedback.velocity_rad_s
        self.last_update_s = now_s
        return command

    def _flywheel_command(self, feedback: MotorFeedback, now_s: float) -> MitCommand:
        velocity = feedback.velocity_rad_s
        capturing = False
        if self.flywheel_released_for_reverse:
            reversed_by_hand = velocity * self.velocity_target_rad_s < 0.0
            if reversed_by_hand and abs(velocity) >= self.FLYWHEEL_START_RAD_S:
                self.flywheel_released_for_reverse = False
                self.flywheel_capture_until_s = now_s + self.FLYWHEEL_CAPTURE_SETTLE_S
                self.flywheel_capture_deadline_s = now_s + self.FLYWHEEL_CAPTURE_MAX_S
                self.flywheel_capture_velocity_rad_s = velocity
                self.flywheel_locked_at_s = 0.0
                self.flywheel_brake_since_s = None
                capturing = True
            elif abs(velocity) < self.FLYWHEEL_START_RAD_S:
                # The user has brought the flywheel to rest. Return to the
                # unpowered waiting state so the next push selects direction.
                self.flywheel_released_for_reverse = False
                self.velocity_target_rad_s = 0.0
                self.flywheel_brake_since_s = None
            else:
                return MitCommand()

        if self.flywheel_capture_until_s != 0.0 and self.flywheel_locked_at_s == 0.0:
            if now_s < self.flywheel_capture_until_s:
                capturing = True
                same_direction = velocity * self.flywheel_capture_velocity_rad_s >= 0.0
                if same_direction and abs(velocity) > abs(self.flywheel_capture_velocity_rad_s):
                    # Retain the latest high point of the push.  Feedback often
                    # falls sharply during hand release; copying every final
                    # sample made the latched command collapse toward zero.
                    self.flywheel_capture_velocity_rad_s = velocity
                if abs(velocity - self.last_velocity_rad_s) >= self.FLYWHEEL_CAPTURE_CHANGE_RAD_S:
                    self.flywheel_capture_until_s = min(
                        self.flywheel_capture_deadline_s,
                        now_s + self.FLYWHEEL_CAPTURE_SETTLE_S,
                    )
            else:
                self.velocity_target_rad_s = (
                    self.flywheel_capture_velocity_rad_s * self.FLYWHEEL_ASSIST_GAIN
                )
                if abs(self.velocity_target_rad_s) < self.FLYWHEEL_START_RAD_S:
                    self.velocity_target_rad_s = 0.0
                    self.flywheel_capture_velocity_rad_s = 0.0
                    self.flywheel_capture_until_s = 0.0
                    self.flywheel_capture_deadline_s = 0.0
                else:
                    self.flywheel_locked_at_s = now_s
        elif self.flywheel_locked_at_s == 0.0:
            if abs(velocity) >= self.FLYWHEEL_START_RAD_S:
                self.flywheel_capture_until_s = (
                    now_s + self.FLYWHEEL_CAPTURE_SETTLE_S
                )
                self.flywheel_capture_deadline_s = now_s + self.FLYWHEEL_CAPTURE_MAX_S
                self.flywheel_capture_velocity_rad_s = velocity
                capturing = True
        else:
            same_direction_faster = (
                velocity * self.velocity_target_rad_s > 0.0
                and abs(velocity)
                > abs(self.velocity_target_rad_s) + self.FLYWHEEL_RECAPTURE_MARGIN_RAD_S
            )
            reversed_by_hand = velocity * self.velocity_target_rad_s < 0.0
            if same_direction_faster or reversed_by_hand:
                self.flywheel_capture_until_s = (
                    now_s + self.FLYWHEEL_CAPTURE_SETTLE_S
                )
                self.flywheel_capture_deadline_s = now_s + self.FLYWHEEL_CAPTURE_MAX_S
                self.flywheel_capture_velocity_rad_s = velocity
                self.flywheel_locked_at_s = 0.0
                self.flywheel_brake_since_s = None
                capturing = True
            else:
                braking_by_hand = (
                    now_s - self.flywheel_locked_at_s >= self.FLYWHEEL_BRAKE_GRACE_S
                    and velocity * self.velocity_target_rad_s > 0.0
                    and abs(velocity)
                    <= abs(self.velocity_target_rad_s) * self.FLYWHEEL_BRAKE_FRACTION
                )
                if braking_by_hand:
                    if self.flywheel_brake_since_s is None:
                        self.flywheel_brake_since_s = now_s
                    elif (
                        now_s - self.flywheel_brake_since_s
                        >= self.FLYWHEEL_BRAKE_CONFIRM_S
                    ):
                        self.flywheel_released_for_reverse = True
                        self.flywheel_locked_at_s = 0.0
                        self.flywheel_capture_until_s = 0.0
                        self.flywheel_capture_deadline_s = 0.0
                        return MitCommand()
                else:
                    self.flywheel_brake_since_s = None

        if capturing:
            self.velocity_target_rad_s = velocity * self.FLYWHEEL_ASSIST_GAIN

        self.velocity_target_rad_s = max(
            -self.FLYWHEEL_MAX_RAD_S,
            min(self.FLYWHEEL_MAX_RAD_S, self.velocity_target_rad_s),
        )
        holding = (
            self.flywheel_locked_at_s != 0.0
            and abs(self.velocity_target_rad_s) >= self.FLYWHEEL_START_RAD_S
        )
        return MitCommand(
            velocity_rad_s=self.velocity_target_rad_s,
            kd=(
                self.FLYWHEEL_CAPTURE_KD
                if capturing
                else (self.FLYWHEEL_HOLD_KD if holding else 0.0)
            ),
        )
