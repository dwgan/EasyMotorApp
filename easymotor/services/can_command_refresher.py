"""Deadline-driven CAN motion refresh independent of the Tk event loop."""

from __future__ import annotations

import queue
import threading
import time
from typing import Protocol

from easymotor.protocols.can_motor import MODE_MOTOR
from easymotor.protocols.can_motor import MitCommand


class CanVelocityTransport(Protocol):
    @property
    def last_feedback_time(self) -> float: ...

    @property
    def last_feedback_mode(self) -> int | None: ...

    def command_velocity(self, motor_rpm: int) -> None: ...

    def command_mit(self, command: MitCommand) -> None: ...

    def stop(self) -> None: ...


class CanCommandRefresher:
    """Refresh live CAN commands without depending on GUI responsiveness.

    The worker owns the refresh deadline.  Calls to :meth:`stop` serialize
    against an in-flight Type 18 speed reference or Type 1 MIT write, so a
    stale refresh cannot follow a user-requested Type 4 stop.
    """

    def __init__(
        self,
        event_queue: queue.Queue[tuple[str, object]],
        *,
        interval_s: float = 0.25,
        feedback_timeout_s: float = 0.75,
    ) -> None:
        self._events = event_queue
        self._interval_s = interval_s
        self._feedback_timeout_s = feedback_timeout_s
        self._condition = threading.Condition()
        self._shutdown = False
        self._enabled = False
        self._transport: CanVelocityTransport | None = None
        self._rpm = 0
        self._mit_command: MitCommand | None = None
        self._deadline: float | None = None
        self._started_at = 0.0
        self._sent_count = 0
        self._generation = 0
        self._thread = threading.Thread(
            target=self._run,
            name="easymotor-can-command-refresh",
            daemon=True,
        )
        self._thread.start()

    def start(
        self,
        transport: CanVelocityTransport,
        motor_rpm: int,
        *,
        deadline: float | None = None,
    ) -> None:
        with self._condition:
            self._transport = transport
            self._rpm = motor_rpm
            self._mit_command = None
            self._deadline = deadline
            self._started_at = time.monotonic()
            self._sent_count = 0
            self._enabled = True
            self._generation += 1
            self._condition.notify_all()

    def start_mit(
        self,
        transport: CanVelocityTransport,
        command: MitCommand,
    ) -> None:
        """Refresh a full Type-1 MIT command at the configured deadline."""
        with self._condition:
            self._transport = transport
            self._mit_command = command
            self._deadline = None
            self._started_at = time.monotonic()
            self._sent_count = 0
            self._enabled = True
            self._generation += 1
            self._condition.notify_all()

    def update_mit(self, command: MitCommand) -> None:
        """Replace the live MIT command without resetting safety deadlines."""
        with self._condition:
            if not self._enabled or self._mit_command is None:
                raise RuntimeError("MIT refresh is not active")
            self._mit_command = command
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._enabled = False
            self._transport = None
            self._mit_command = None
            self._deadline = None
            self._generation += 1
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._enabled = False
            self._transport = None
            self._mit_command = None
            self._shutdown = True
            self._generation += 1
            self._condition.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)

    def _disable_locked(self) -> None:
        self._enabled = False
        self._transport = None
        self._mit_command = None
        self._deadline = None
        self._generation += 1

    def _run(self) -> None:
        next_send = 0.0
        observed_generation = -1
        while True:
            with self._condition:
                while not self._shutdown and not self._enabled:
                    self._condition.wait()
                if self._shutdown:
                    return
                if observed_generation != self._generation:
                    observed_generation = self._generation
                    next_send = time.monotonic() + self._interval_s
                now = time.monotonic()
                wait_s = next_send - now
                if wait_s > 0.0:
                    self._condition.wait(wait_s)
                    continue
                transport = self._transport
                if transport is None:
                    self._disable_locked()
                    continue
                deadline = self._deadline
                if deadline is not None and now >= deadline:
                    self._disable_locked()
                    try:
                        transport.stop()
                    except Exception as exc:
                        self._events.put(("can_refresh_error", str(exc)))
                    self._events.put(("can_timed_deadline", None))
                    continue
                feedback_reference = max(
                    self._started_at,
                    transport.last_feedback_time,
                )
                if (
                    transport.last_feedback_time >= self._started_at
                    and transport.last_feedback_mode != MODE_MOTOR
                ):
                    self._disable_locked()
                    try:
                        transport.stop()
                    except Exception as exc:
                        self._events.put(("can_refresh_error", str(exc)))
                    self._events.put(("can_refresh_motor_exit", None))
                    continue
                if now - feedback_reference > self._feedback_timeout_s:
                    self._disable_locked()
                    try:
                        transport.stop()
                    except Exception as exc:
                        self._events.put(("can_refresh_error", str(exc)))
                    self._events.put(("can_refresh_timeout", None))
                    continue
                try:
                    # Keep the condition locked across the physical write.  A
                    # simultaneous stop() therefore completes after this frame
                    # and can safely put Type 4 last on the wire.
                    if self._mit_command is None:
                        transport.command_velocity(self._rpm)
                    else:
                        transport.command_mit(self._mit_command)
                except Exception as exc:
                    self._disable_locked()
                    self._events.put(("can_refresh_error", str(exc)))
                    continue
                self._sent_count += 1
                self._events.put(("can_refresh_sent", self._sent_count))
                next_send += self._interval_s
                if next_send <= time.monotonic():
                    next_send = time.monotonic() + self._interval_s
