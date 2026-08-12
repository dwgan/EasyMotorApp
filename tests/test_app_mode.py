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


class AppModeInterfaceTests(unittest.TestCase):
    def test_engineer_connection_uses_rs485_without_changing_demo_can_selection(self):
        app = object.__new__(EasyMotorApp)
        selection = _InterfaceSelection("can")
        app.interface_var = selection

        app.app_mode = "demo"
        self.assertEqual(app._connection_interface(), "can")

        app.app_mode = "engineer"
        self.assertEqual(app._connection_interface(), "rs485")
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
        self.assertEqual(app._connection_interface(), "rs485")
        self.assertEqual(selection.get(), "rs485")

        app.app_mode = "demo"
        self.assertEqual(app._connection_interface(), "can")

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
