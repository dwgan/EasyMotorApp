"""Pure state machine for a non-motion CAN endurance test."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from easymotor.protocols.can_motor import PARAMETER_BY_INDEX


LONG_RUN_PARAMETER_INDICES: Final = (
    0x7005,  # run_mode
    0x7019,  # mechPos
    0x701B,  # mechVel
    0x7026,  # EPScan_time
    0x7028,  # cantimeout
)


@dataclass(frozen=True)
class LongRunRecord:
    sent_at: str
    finished_at: str
    index: int
    name: str
    result: str
    value: int | float | None
    latency_ms: float | None
    detail: str = ""


class LongRunSession:
    """Schedule one outstanding type-17 read at a time and collect outcomes."""

    def __init__(
        self,
        duration_s: float = 3600.0,
        interval_s: float = 0.1,
        timeout_s: float = 0.5,
        parameter_indices: tuple[int, ...] = LONG_RUN_PARAMETER_INDICES,
    ) -> None:
        if duration_s <= 0:
            raise ValueError("duration must be positive")
        if interval_s < 0.05:
            raise ValueError("interval must be at least 50 ms")
        if timeout_s < interval_s:
            raise ValueError("timeout must not be shorter than interval")
        if not parameter_indices:
            raise ValueError("at least one parameter is required")
        for index in parameter_indices:
            if index not in PARAMETER_BY_INDEX:
                raise ValueError(f"unknown RS04 parameter index 0x{index:04X}")

        self.duration_s = duration_s
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self.parameter_indices = parameter_indices
        self.records: list[LongRunRecord] = []
        self.reset()

    def reset(self) -> None:
        self.running = False
        self.stop_reason = "not_started"
        self.started_monotonic: float | None = None
        self.deadline_monotonic = 0.0
        self.next_due_monotonic = 0.0
        self.pending_index: int | None = None
        self.pending_sent_monotonic = 0.0
        self.pending_sent_at = ""
        self.parameter_cursor = 0
        self.tx_count = 0
        self.response_count = 0
        self.timeout_count = 0
        self.rejection_count = 0
        self.send_failure_count = 0
        self.total_latency_ms = 0.0
        self.max_latency_ms = 0.0
        self.records.clear()

    def start(self, now: float) -> None:
        self.reset()
        self.running = True
        self.stop_reason = "running"
        self.started_monotonic = now
        self.deadline_monotonic = now + self.duration_s
        self.next_due_monotonic = now

    def stop(self, reason: str = "stopped") -> None:
        self.running = False
        self.stop_reason = reason
        self.pending_index = None

    def tick(self, now: float, timestamp: str) -> int | None:
        """Return the next parameter index to send, or ``None`` when idle."""
        if not self.running:
            return None
        if now + 1e-9 >= self.deadline_monotonic:
            if self.pending_index is not None:
                if now - self.pending_sent_monotonic < self.timeout_s:
                    return None
                self.timeout_count += 1
                self._append_record(timestamp, "timeout", None, None)
                self.pending_index = None
            self.stop("completed")
            return None
        if self.pending_index is not None:
            if now - self.pending_sent_monotonic < self.timeout_s:
                return None
            self.timeout_count += 1
            self._append_record(timestamp, "timeout", None, None)
            self.pending_index = None
        if now + 1e-9 < self.next_due_monotonic:
            return None

        index = self.parameter_indices[self.parameter_cursor]
        self.parameter_cursor = (self.parameter_cursor + 1) % len(self.parameter_indices)
        self.pending_index = index
        self.pending_sent_monotonic = now
        self.pending_sent_at = timestamp
        self.next_due_monotonic = now + self.interval_s
        self.tx_count += 1
        return index

    def accept_response(
        self, index: int, value: int | float, now: float, timestamp: str
    ) -> bool:
        if not self.running or index != self.pending_index:
            return False
        latency_ms = max(0.0, (now - self.pending_sent_monotonic) * 1000.0)
        self.response_count += 1
        self.total_latency_ms += latency_ms
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        self._append_record(timestamp, "ok", value, latency_ms)
        self.pending_index = None
        return True

    def reject_response(self, index: int, detail: str, timestamp: str) -> bool:
        if not self.running or index != self.pending_index:
            return False
        self.rejection_count += 1
        self._append_record(timestamp, "rejected", None, None, detail)
        self.pending_index = None
        return True

    def send_failed(self, detail: str, timestamp: str) -> bool:
        if not self.running or self.pending_index is None:
            return False
        self.send_failure_count += 1
        self._append_record(timestamp, "send_failed", None, None, detail)
        self.pending_index = None
        return True

    @property
    def average_latency_ms(self) -> float:
        if self.response_count == 0:
            return 0.0
        return self.total_latency_ms / self.response_count

    def elapsed_s(self, now: float) -> float:
        if self.started_monotonic is None:
            return 0.0
        return max(0.0, min(now, self.deadline_monotonic) - self.started_monotonic)

    def _append_record(
        self,
        finished_at: str,
        result: str,
        value: int | float | None,
        latency_ms: float | None,
        detail: str = "",
    ) -> None:
        if self.pending_index is None:
            return
        parameter = PARAMETER_BY_INDEX[self.pending_index]
        self.records.append(
            LongRunRecord(
                sent_at=self.pending_sent_at,
                finished_at=finished_at,
                index=parameter.index,
                name=parameter.name,
                result=result,
                value=value,
                latency_ms=latency_ms,
                detail=detail,
            )
        )
