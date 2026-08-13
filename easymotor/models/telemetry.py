"""Typed telemetry parsed from the engineering debug console."""

from __future__ import annotations

from dataclasses import dataclass
import re


CPU_LOAD_RE = re.compile(
    r"CPU win=(\d+) rt=(\d+) foc=(\d+)/(\d+) "
    r"enc=(\d+)/(\d+) samples=(\d+)/(\d+)"
    r"(?: enc_rate=(\d+)/(\d+))?"
)
ENCODER_MODEL_RE = re.compile(r"ENC port=(\d+) role=(\d+) model=(\d+)\b")

ENCODER_MODEL_NAMES = {
    1: "IC-MU128",
    2: "IC-MU150",
    3: "IC-MU200",
    4: "AS5047P",
}


@dataclass(frozen=True)
class CpuLoadTelemetry:
    """One firmware CPU load window; all load values are permille."""

    window_ms: int
    realtime_permille: int
    foc_average_permille: int
    foc_peak_permille: int
    encoder_average_permille: int
    encoder_peak_permille: int
    foc_samples: int
    encoder_samples: int
    encoder_completed_hz: int | None = None
    encoder_requested_hz: int | None = None

    @property
    def realtime_percent(self) -> float:
        return self.realtime_permille / 10.0

    @property
    def encoder_completion_percent(self) -> float | None:
        if self.encoder_completed_hz is None or not self.encoder_requested_hz:
            return None
        return self.encoder_completed_hz * 100.0 / self.encoder_requested_hz

    @property
    def encoder_rate_low(self) -> bool:
        completion = self.encoder_completion_percent
        return completion is not None and completion < 90.0


@dataclass(frozen=True)
class EncoderTelemetry:
    port: int
    role: int
    model: int

    @property
    def model_name(self) -> str:
        return ENCODER_MODEL_NAMES.get(self.model, "Unknown")


class TelemetryModel:
    """Latest typed diagnostic values, independent of Tk widgets."""

    def __init__(self) -> None:
        self.cpu_load: CpuLoadTelemetry | None = None
        self.encoders: dict[int, EncoderTelemetry] = {}
        self.as5047p_health_seen = False
        self.cpu_update_count = 0
        self.encoder_update_count = 0

    def parse_line(self, line: str) -> bool:
        match = CPU_LOAD_RE.search(line)
        if match is not None:
            values = tuple(
                int(value) if value is not None else None
                for value in match.groups()
            )
            self.cpu_load = CpuLoadTelemetry(*values)
            self.cpu_update_count += 1
            return True
        encoder_match = ENCODER_MODEL_RE.search(line)
        if encoder_match is not None:
            encoder = EncoderTelemetry(
                *(int(value) for value in encoder_match.groups())
            )
            self.encoders[encoder.role] = encoder
            self.encoder_update_count += 1
            return True
        return False

    def note_as5047p_health(self) -> None:
        """Record the legacy AS5047P-specific ENC_ERR telemetry source."""
        self.as5047p_health_seen = True

    def encoder_values(self) -> tuple[EncoderTelemetry, ...]:
        return tuple(self.encoders[role] for role in sorted(self.encoders))


def format_cpu_load(value: CpuLoadTelemetry | None) -> str:
    if value is None:
        return "CPU real-time load: unknown"
    return (
        f"CPU real-time={value.realtime_percent:.1f}% | "
        f"FOC avg/peak={value.foc_average_permille / 10.0:.1f}/"
        f"{value.foc_peak_permille / 10.0:.1f}% | "
        f"Encoder avg/peak={value.encoder_average_permille / 10.0:.1f}/"
        f"{value.encoder_peak_permille / 10.0:.1f}%"
        + (
            f" | Encoder rate={value.encoder_completed_hz / 1000.0:.1f}/"
            f"{value.encoder_requested_hz / 1000.0:.1f} kHz "
            f"({value.encoder_completion_percent:.0f}%, "
            f"{'LOW' if value.encoder_rate_low else 'OK'})"
            if value.encoder_completion_percent is not None
            else ""
        )
    )
