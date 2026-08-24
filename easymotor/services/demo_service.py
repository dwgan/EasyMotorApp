"""State-only coordinator for the beginner demonstration workflow."""

from __future__ import annotations

from enum import Enum

from easymotor.core.safety_policy import DemoRunPlan, make_demo_plan


class DemoAction(Enum):
    START_MOTOR = "start_motor"
    RUN_TIMED = "run_timed"
    RUN_CONTINUOUS = "run_continuous"


class DemoPhase(Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"


class DemoError(ValueError):
    """User-facing demo rejection identified by a translation key."""

    def __init__(self, message_key: str) -> None:
        super().__init__(message_key)
        self.message_key = message_key


class DemoService:
    """Create safe demo plans without owning serial or motor-control APIs."""

    def __init__(self) -> None:
        self.phase = DemoPhase.IDLE
        self.plan: DemoRunPlan | None = None

    def request_run(
        self,
        *,
        direction: int,
        speed_rpm: int,
        continuous: bool,
        connected: bool,
        mci_state: int | None,
        another_operation_active: bool,
    ) -> DemoAction:
        if not connected:
            raise DemoError("connect_first")
        if another_operation_active or self.phase != DemoPhase.IDLE:
            raise DemoError("demo_busy")
        plan = make_demo_plan(direction, speed_rpm, continuous)
        if mci_state == 0:
            self.plan = plan
            self.phase = DemoPhase.PREPARING
            return DemoAction.START_MOTOR
        # The customer demo owns an explicit mode-2 -> Type-3 -> 0x700A
        # sequence.  Never take over a motor that another path already left
        # in RUN because its selected run mode cannot be proven here.
        raise DemoError("demo_unsafe")

    def motor_ready(self) -> DemoAction | None:
        if self.phase != DemoPhase.PREPARING or self.plan is None:
            return None
        self.phase = DemoPhase.RUNNING
        return self._run_action(self.plan)

    def cancel(self) -> None:
        self.phase = DemoPhase.IDLE
        self.plan = None

    @staticmethod
    def _run_action(plan: DemoRunPlan) -> DemoAction:
        return DemoAction.RUN_CONTINUOUS if plan.continuous else DemoAction.RUN_TIMED
