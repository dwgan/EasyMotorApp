from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from easymotor_app import EasyMotorApp


class _InterfaceSelection:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class _CanTransport:
    def __init__(self) -> None:
        self.enumeration_count = 0

    def enumerate(self) -> None:
        self.enumeration_count += 1


class _DebugSerial:
    in_waiting = 0

    def __init__(self) -> None:
        self.writes = []

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    def flush(self) -> None:
        pass


class AppModeInterfaceTests(unittest.TestCase):
    def test_monitor_tab_exposes_waveform_window(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        monitor_start = source.index("motion_frame = ttk.LabelFrame(")
        can_start = source.index("can_frame = ttk.LabelFrame(", monitor_start)
        monitor_source = source[monitor_start:can_start]

        self.assertIn("command=self.open_wave_popup", monitor_source)

    def test_engineer_can_page_has_bounded_motion_and_waveform_controls(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        can_motion_start = source.index("can_motion = ttk.LabelFrame(")
        hidden_controls_start = source.index(
            "controls = ttk.Frame(self.engineer_control_tab", can_motion_start
        )
        can_source = source[can_motion_start:hidden_controls_start]

        self.assertIn("DEMO_SPEED_PRESETS_RPM", can_source)
        self.assertIn("self._start_engineer_can_run(1)", can_source)
        self.assertIn("self._start_engineer_can_run(-1)", can_source)
        self.assertIn("command=self.stop_motor", can_source)
        self.assertIn("command=self.open_wave_popup", can_source)

    def test_engineer_can_run_reuses_demo_safety_service(self):
        app = object.__new__(EasyMotorApp)
        app.engineer_can_speed_var = _InterfaceSelection(10)
        app.engineer_can_continuous_var = _InterfaceSelection(True)
        calls = []
        app.start_demo_run = lambda direction, speed, continuous: calls.append(
            (direction, speed, continuous)
        )

        app._start_engineer_can_run(-1)

        self.assertEqual(calls, [(-1, 10, True)])

    def test_motion_connection_remains_can_in_every_page(self):
        app = object.__new__(EasyMotorApp)
        selection = _InterfaceSelection("can")
        app.interface_var = selection

        app.app_mode = "demo"
        self.assertEqual(app._connection_interface(), "can")

        app.app_mode = "engineer"
        self.assertEqual(app._connection_interface(), "can")
        self.assertEqual(selection.get(), "can")

        app.app_mode = "demo"
        self.assertEqual(app._connection_interface(), "can")

    def test_rs485_debug_blocks_motor_control_commands(self):
        app = object.__new__(EasyMotorApp)
        app.active_interface = "rs485"

        for command in ("start", "speed 5", "iq 5", "keep", "stop", "faultack"):
            self.assertFalse(app.send_command(command, quiet=True), command)

    def test_demo_is_can_only_even_if_legacy_selection_is_rs485(self):
        app = object.__new__(EasyMotorApp)
        selection = _InterfaceSelection("rs485")
        app.interface_var = selection

        app.app_mode = "engineer"
        self.assertEqual(app._connection_interface(), "can")
        self.assertEqual(selection.get(), "rs485")

        app.app_mode = "demo"
        self.assertEqual(app._connection_interface(), "can")

    def test_can_and_rs485_debug_have_independent_port_state(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        self.assertIn("self.debug_port_var = tk.StringVar()", source)
        self.assertIn("command=self.toggle_debug_connection", source)
        self.assertIn("self.advanced_can_connect_button = ttk.Button(", source)
        self.assertIn("command=self.toggle_connection", source)
        self.assertIn("def _disconnect_can(self)", source)
        self.assertIn("def _disconnect_debug(self)", source)

        demo_start = source.index("    def show_demo_mode(")
        engineer_start = source.index("    def show_engineer_mode(", demo_start)
        motor_start = source.index("    def _motor_activity_active(", engineer_start)
        self.assertNotIn("_motor_activity_active", source[demo_start:engineer_start])
        self.assertNotIn("_motor_activity_active", source[engineer_start:motor_start])

    def test_rs485_diagnostics_are_not_blocked_by_active_can(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        send_start = source.index("    def send_command(")
        send_end = source.index("    def start_scope_hold(", send_start)
        send_source = source[send_start:send_end]

        self.assertNotIn('if self.active_interface == "can"', send_source)
        self.assertIn('normalized.startswith("wave ")', send_source)

        app = object.__new__(EasyMotorApp)
        debug_serial = _DebugSerial()
        app.active_interface = "can"
        app.serial_port = debug_serial
        app.serial_lock = threading.Lock()
        app.last_rx_time = 0.0
        app._append_log = lambda _kind, _text: None

        self.assertTrue(app.send_command("wave on 10", quiet=True))
        self.assertEqual(debug_serial.writes, [b"wave on 10\r\n"])

    def test_can_discovery_retries_while_motor_is_unpowered(self):
        app = object.__new__(EasyMotorApp)
        transport = _CanTransport()
        scheduled = []
        app.connected = True
        app.active_interface = "can"
        app.can_uid = None
        app.can_transport = transport
        app.can_enumeration_generation = 4
        app.can_enumeration_started = 10**9
        app.can_enumeration_guidance_shown = False
        app._render_demo_view = lambda: None
        app.after = lambda delay, callback: scheduled.append((delay, callback))

        app._retry_can_enumeration(4)

        self.assertEqual(transport.enumeration_count, 1)
        self.assertEqual(len(scheduled), 1)

    def test_stale_can_discovery_callback_stops_after_ready_or_disconnect(self):
        app = object.__new__(EasyMotorApp)
        transport = _CanTransport()
        app.connected = True
        app.active_interface = "can"
        app.can_uid = None
        app.can_transport = transport
        app.can_enumeration_generation = 5

        app._retry_can_enumeration(4)

        self.assertEqual(transport.enumeration_count, 0)

    def test_long_can_wait_shows_inline_guidance_without_error_dialog(self):
        app = object.__new__(EasyMotorApp)
        transport = _CanTransport()
        logs = []
        app.connected = True
        app.active_interface = "can"
        app.can_uid = None
        app.can_transport = transport
        app.can_enumeration_generation = 2
        app.can_enumeration_started = 0.0
        app.can_enumeration_guidance_shown = False
        app.language_var = _InterfaceSelection("en")
        app._append_log = lambda kind, text: logs.append((kind, text))
        app._render_demo_view = lambda: None
        app.after = lambda _delay, _callback: None

        with (
            patch("easymotor_app.time.monotonic", return_value=20.0),
            patch("easymotor_app.messagebox.showwarning") as warning,
        ):
            app._retry_can_enumeration(2)

        self.assertTrue(app.can_enumeration_guidance_shown)
        self.assertTrue(logs)
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
