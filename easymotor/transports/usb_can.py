"""Threaded adapter for the vendor USB-CAN serial module."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

import serial

from easymotor.protocols.can_motor import (
    AtFrameDecoder,
    CanFrame,
    build_active_report,
    build_device_id_request,
    build_enable,
    build_mit_control,
    build_save,
    build_set_node_id,
    build_set_zero,
    build_stop,
    build_velocity_control,
    encode_at_frame,
    parse_feedback,
    MitCommand,
)


USB_CAN_BAUD = 921_600


class UsbCanMotorTransport:
    """Own one USB-CAN COM port and publish decoded frames to a UI queue."""

    def __init__(
        self,
        event_queue: queue.Queue[tuple[str, object]],
        *,
        node_id: int = 0x7F,
        host_id: int = 0xFD,
        serial_factory: Callable[..., serial.Serial] = serial.Serial,
    ) -> None:
        self.event_queue = event_queue
        self.node_id = node_id
        self.host_id = host_id
        self._serial_factory = serial_factory
        self._connection: serial.Serial | None = None
        self._write_lock = threading.Lock()
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._last_feedback_time = 0.0
        self._last_feedback_mode: int | None = None

    @property
    def connected(self) -> bool:
        return self._connection is not None

    @property
    def last_feedback_time(self) -> float:
        """Monotonic receive time updated before the UI queue is serviced."""
        return self._last_feedback_time

    @property
    def last_feedback_mode(self) -> int | None:
        return self._last_feedback_mode

    def connect(self, port: str) -> None:
        if self.connected:
            raise RuntimeError("USB-CAN transport is already connected")
        connection = self._serial_factory(
            port=port,
            baudrate=USB_CAN_BAUD,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=0.2,
        )
        connection.reset_input_buffer()
        self._connection = connection
        self._last_feedback_time = 0.0
        self._last_feedback_mode = None
        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="easymotor-usb-can", daemon=True
        )
        self._reader_thread.start()

    def close(self) -> None:
        self._reader_stop.set()
        connection = self._connection
        self._connection = None
        reader_thread = self._reader_thread
        self._reader_thread = None
        if connection is not None:
            try:
                connection.close()
            except (serial.SerialException, OSError):
                pass
        if reader_thread is not None and reader_thread is not threading.current_thread():
            reader_thread.join(timeout=0.2)

    def enumerate(self) -> None:
        self.send(build_device_id_request(self.node_id, self.host_id))

    def set_active_report(self, enabled: bool) -> None:
        self.send(build_active_report(enabled, self.node_id, self.host_id))

    def enable(self) -> None:
        self.send(build_enable(self.node_id, self.host_id))

    def command_velocity(self, motor_rpm: int) -> None:
        self.send(build_velocity_control(motor_rpm, self.node_id))

    def command_mit(self, command: MitCommand) -> None:
        self.send(build_mit_control(command, self.node_id))

    def set_zero(self) -> None:
        self.send(build_set_zero(self.node_id, self.host_id))

    def set_node_id(self, new_node_id: int) -> None:
        self.send(build_set_node_id(new_node_id, self.node_id, self.host_id))
        self.node_id = new_node_id

    def save_configuration(self) -> None:
        self.send(build_save(self.node_id, self.host_id))

    def stop(self) -> None:
        self.send(build_stop(self.node_id, self.host_id))

    def send(self, frame: CanFrame) -> None:
        connection = self._connection
        if connection is None:
            raise RuntimeError("USB-CAN transport is not connected")
        raw = encode_at_frame(frame)
        with self._write_lock:
            connection.write(raw)
            connection.flush()
        self.event_queue.put(("can_tx", frame))

    def _reader_loop(self) -> None:
        decoder = AtFrameDecoder()
        while not self._reader_stop.is_set():
            connection = self._connection
            if connection is None:
                return
            try:
                chunk = connection.read(max(connection.in_waiting, 1))
            except (serial.SerialException, OSError) as exc:
                self.event_queue.put(("can_error", str(exc)))
                return
            if not chunk:
                continue
            for frame in decoder.feed(chunk):
                feedback = parse_feedback(frame)
                if feedback is not None:
                    self._last_feedback_time = time.monotonic()
                    self._last_feedback_mode = feedback.mode
                self.event_queue.put(("can_frame", frame))
