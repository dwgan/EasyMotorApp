from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from easymotor_app import EasyMotorApp
from easymotor.protocols.can_motor import MitCommand


class _InterfaceSelection:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class _CanTransport:
    def __init__(self) -> None:
        self.enumeration_count = 0
        self.probed_node_ids = []

    def enumerate(self, node_id=None) -> None:
        self.enumeration_count += 1
        self.probed_node_ids.append(node_id)


class _DebugSerial:
    in_waiting = 0

    def __init__(self) -> None:
        self.writes = []

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    def flush(self) -> None:
        pass


class AppModeInterfaceTests(unittest.TestCase):
    def test_confirmed_stop_immediately_refreshes_demo_state(self):
        app = object.__new__(EasyMotorApp)
        app.stop_pending = True
        app.mci_state = 0
        app.can_stop_not_before = 0.0
        app._restore_mit_mode_after_demo = lambda: None
        app._append_log = lambda *args: None
        app._operation_log = lambda *args: None
        refreshes = []
        app._update_control_state = lambda: refreshes.append(True)

        app._stop_retry_tick()

        self.assertFalse(app.stop_pending)
        self.assertEqual(refreshes, [True])

    def test_advanced_rs485_controls_are_explicitly_labeled(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        for label in (
            "RS485 Debug 状态",
            "RS485 状态",
            "RS485 Debug 运动与 PWM 遥测",
            "RS485 Debug 电流波形",
        ):
            self.assertIn(label, source)

    def test_low_value_terminal_diagnostics_are_not_gui_buttons(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        ui_start = source.index("    def _build_engineer_ui(")
        ui_end = source.index("    def _translate_engineer_widgets(", ui_start)
        ui_source = source[ui_start:ui_end]

        self.assertNotIn('send_command("help")', ui_source)
        self.assertNotIn('send_command("can status")', ui_source)
        self.assertNotIn('send_command("can codec")', ui_source)

    def test_parameters_reuse_main_can_transport(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        self.assertIn("self.can_parameter_panel = CanParameterPanel(", source)
        self.assertIn("send_frame=self._send_parameter_frame", source)
        self.assertIn("transport.send(frame)", source)
        self.assertNotIn("CanToolWindow", source)

    def test_can_discovery_disables_unsolicited_active_reports(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        device_start = source.index("device = parse_device_id_response(frame)")
        feedback_start = source.index("feedback = parse_feedback(frame)", device_start)
        discovery_source = source[device_start:feedback_start]

        self.assertIn("transport.set_active_report(False)", discovery_source)
        self.assertNotIn("transport.set_active_report(True)", discovery_source)

    def test_active_reports_are_limited_to_enable_and_alignment(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        start_begin = source.index("    def start_motor(")
        start_end = source.index("    def _poll_start_sequence(", start_begin)
        start_source = source[start_begin:start_end]
        motor_begin = source.index("elif feedback.mode == MODE_MOTOR:")
        motor_end = source.index("            else:", motor_begin)
        motor_source = source[motor_begin:motor_end]

        self.assertLess(
            start_source.index("set_active_report(True)"),
            start_source.index("self.can_transport.enable()"),
        )
        self.assertIn("set_active_report(False)", motor_source)

    def test_enable_is_retried_only_while_feedback_remains_reset(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        poll_begin = source.index("    def _poll_start_sequence(")
        poll_end = source.index("    def send_speed(", poll_begin)
        poll_source = source[poll_begin:poll_end]

        self.assertIn('self.mci_state == 0', poll_source)
        self.assertIn('self.can_transport.enable()', poll_source)
        self.assertIn('START_ENABLE_MAX_ATTEMPTS', poll_source)
        self.assertLess(
            poll_source.index('if self.mci_state == 6:'),
            poll_source.index('self.mci_state == 0'),
        )

    def test_reset_report_does_not_disable_alignment_reporting(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        reset_begin = source.index("if feedback.mode == MODE_RESET:")
        calibrating_begin = source.index(
            "elif feedback.mode == MODE_CALIBRATING:", reset_begin
        )
        reset_source = source[reset_begin:calibrating_begin]

        self.assertIn("not self.start_waiting", reset_source)
        self.assertIn("set_active_report(False)", reset_source)

    def test_logs_are_split_by_source(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        self.assertIn('(\"all\", \"全部\", \"All\")', source)
        self.assertIn('(\"can\", \"CAN\", \"CAN\")', source)
        self.assertIn('(\"rs485\", \"RS485 Debug\", \"RS485 Debug\")', source)
        self.assertIn('(\"app\", \"应用\", \"Application\")', source)
        self.assertIn('self._log_entries.append((channel, tag, timestamp, text))', source)

    def test_advanced_pages_are_split_by_interface_responsibility(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        for tab in (
            "engineer_overview_tab",
            "engineer_can_tab",
            "engineer_rs485_tab",
            "engineer_log_tab",
        ):
            self.assertIn(f"self.{tab} = ttk.Frame", source)
        self.assertNotIn("engineer_monitor_tab", source)

    def test_rs485_debug_tab_exposes_waveform_window(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        monitor_start = source.index("motion_frame = ttk.LabelFrame(")
        can_start = source.index("self.mit_bench_panel = MitBenchPanel(", monitor_start)
        monitor_source = source[monitor_start:can_start]

        self.assertIn("self.engineer_rs485_tab", monitor_source)
        self.assertIn("command=self.open_wave_popup", monitor_source)

    def test_engineer_can_page_uses_mit_as_primary_motion_control(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        mit_start = source.index("self.mit_bench_panel = MitBenchPanel(")
        parameter_start = source.index("parameter_frame = ttk.LabelFrame(", mit_start)
        can_source = source[mit_start:parameter_start]

        self.assertIn("self.mit_bench_panel = MitBenchPanel(", can_source)
        self.assertIn("enable=self.start_motor", can_source)
        self.assertIn("stop=self.stop_motor", can_source)
        self.assertIn("save_alignment=self._save_rotor_alignment", can_source)
        self.assertNotIn("CAN 安全演示控制", can_source)
        self.assertNotIn("DEMO_SPEED_PRESETS_RPM", can_source)

    def test_can_feedback_is_compactly_integrated_into_connection(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        connection_start = source.index("can_connection = ttk.LabelFrame(")
        mit_start = source.index("self.mit_bench_panel = MitBenchPanel(", connection_start)
        connection_source = source[connection_start:mit_start]

        self.assertIn("textvariable=self.can_feedback_var", connection_source)
        self.assertIn("textvariable=self.temperature_var", connection_source)
        self.assertIn('font=("Consolas", 10)', connection_source)
        self.assertNotIn("can_feedback = ttk.LabelFrame(", source)
        render_start = source.index("    def _render_can_feedback(")
        render_end = source.index("    def _temperature_poll_tick(", render_start)
        render_source = source[render_start:render_end]
        self.assertNotIn("board_temperature_c", render_source)
        self.assertNotIn("motor_temperature_c", render_source)
        self.assertIn("feedback.velocity_rad_s: .4f", render_source)

        mit_source = Path("easymotor/features/mit_bench/panel.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("feedback.velocity_rad_s: .4f", mit_source)
        self.assertIn('font=("Consolas", 10)', mit_source)

    def test_rs485_raw_diagnostics_are_collapsed_by_default(self):
        app = object.__new__(EasyMotorApp)
        app.rs485_details_visible = False
        self.assertFalse(app.rs485_details_visible)

    def test_engineer_can_page_does_not_duplicate_customer_speed_demo(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        self.assertNotIn("def _start_engineer_can_run", source)
        self.assertNotIn("engineer_can_speed_var", source)
        self.assertNotIn("engineer_can_continuous_var", source)

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
        send_end = source.index("    def toggle_wave_stream(", send_start)
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

    def test_legacy_rs485_motion_and_stage_i_controls_are_removed(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        for removed in (
            "engineer_control_tab",
            "start_scope_hold",
            "Stage-I 双向探测",
            "Stage-I 完整验收",
            "send_command(f\"speed {value}\")",
            "send_command(\"start\")",
            "send_command(\"keep\"",
        ):
            self.assertNotIn(removed, source)

        self.assertNotIn("def _can_command_refresh_tick(self)", source)
        self.assertIn("self.can_command_refresher.start", source)

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
        self.assertEqual(transport.probed_node_ids, [0x7F])
        self.assertEqual(len(scheduled), 1)

    def test_can_connection_exposes_full_read_only_device_detection(self):
        source = Path("easymotor_app.py").read_text(encoding="utf-8")

        self.assertIn('text="检测 CAN ID", command=self.detect_can_device', source)
        self.assertNotIn("advanced_can_save_button", source)
        self.assertNotIn("def save_detected_can_configuration", source)
        self.assertIn("CAN_DETECTION_NODE_IDS", source)
        self.assertIn("self.can_transport.enumerate(node_id)", source)
        self.assertIn('self.can_device_var.set(f"CAN ID: {node_id}', source)
        self.assertIn("self.can_parameter_panel.node_id = node_id", source)
        self.assertIn("def set_detected_can_id(self)", source)
        self.assertIn("CAN ID change verified", source)
        self.assertIn("self.can_new_node_id_var = tk.IntVar(value=1)", source)
        self.assertIn("CAN ID {node_id} verified; Type 22 flash save requested", source)

        panel_source = Path("easymotor/features/mit_bench/panel.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('"apply_id"', panel_source)
        self.assertNotIn("self.node_var", panel_source)

        demo_source = Path("easymotor/features/demo/view.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('text=tr(language, "detect_device")', demo_source)
        self.assertIn("command=self._on_detect_device", demo_source)
        self.assertNotIn("set_detected_can_id", demo_source)

    def test_rotor_alignment_save_requires_valid_7033_then_sends_type22(self):
        class _Transport:
            def __init__(self):
                self.save_count = 0

            def save_configuration(self):
                self.save_count += 1

        app = object.__new__(EasyMotorApp)
        transport = _Transport()
        app.can_transport = transport
        app._parameter_connection_ready = lambda: True
        app._parameter_operation_idle = lambda: True
        app._append_log = lambda *args: None
        app._operation_log = lambda *args: None

        app.rotor_alignment_valid = False
        with self.assertRaises(RuntimeError):
            app._save_rotor_alignment()
        self.assertEqual(transport.save_count, 0)

        app.rotor_alignment_valid = True
        with patch("easymotor_app.messagebox.askyesno", return_value=True):
            app._save_rotor_alignment()
        self.assertEqual(transport.save_count, 1)

    def test_mit_single_pulse_auto_enables_from_idle_and_stops_after_200_ms(self):
        class _Transport:
            def __init__(self):
                self.commands = []

            def command_mit(self, command):
                self.commands.append(command)

        app = object.__new__(EasyMotorApp)
        transport = _Transport()
        command = MitCommand(0.0, 0.0, 0.0, 0.0, 0.0)
        scheduled = []
        app.can_transport = transport
        app.mci_state = 0
        app.pending_mit_single_command = None
        app.start_waiting = False
        app.motion_command_active = False
        app._parameter_connection_ready = lambda: True
        app.start_motor = lambda: True
        app._append_log = lambda *args: None
        app._operation_log = lambda *args: None
        app.after = lambda delay, callback: scheduled.append((delay, callback))

        app._mit_send_once(command)
        self.assertIs(app.pending_mit_single_command, command)
        self.assertEqual(transport.commands, [])

        app.mci_state = 6
        app.pending_mit_single_command = None
        app._send_mit_single_frame(command)
        self.assertEqual(transport.commands, [command])
        self.assertTrue(app.motion_command_active)
        self.assertEqual(scheduled[0][0], 200)

        source = Path("easymotor_app.py").read_text(encoding="utf-8")
        poll_start = source.index("    def _poll_start_sequence(")
        send_start = source.index("    def send_speed(", poll_start)
        self.assertIn(
            "self._send_mit_single_frame(pending_mit_command)",
            source[poll_start:send_start],
        )

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
