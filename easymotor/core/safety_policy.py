"""Central user-facing safety limits shared by all UI modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


DEMO_SPEED_PRESETS_RPM: Final = (5, 30, 100)
DEMO_DEFAULT_SPEED_RPM: Final = 30
DEMO_DEFAULT_DURATION_MS: Final = 5_000


@dataclass(frozen=True)
class DemoRunPlan:
    direction: int
    speed_rpm: int
    duration_ms: int = DEMO_DEFAULT_DURATION_MS
    continuous: bool = False

    @property
    def command_rpm(self) -> int:
        return self.direction * self.speed_rpm


def make_demo_plan(
    direction: int,
    speed_rpm: int,
    continuous: bool,
    duration_ms: int = DEMO_DEFAULT_DURATION_MS,
) -> DemoRunPlan:
    """Validate the deliberately small control surface exposed in demo mode."""
    if direction not in (-1, 1):
        raise ValueError("演示方向必须为正转或反转")
    if speed_rpm not in DEMO_SPEED_PRESETS_RPM:
        raise ValueError("演示速度只能选择 5、30 或 100 motor rpm")
    if duration_ms != DEMO_DEFAULT_DURATION_MS:
        raise ValueError("演示模式的单次运行时长固定为 5 秒")
    return DemoRunPlan(direction, speed_rpm, duration_ms, bool(continuous))
