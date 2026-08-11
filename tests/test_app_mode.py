import unittest

from easymotor_app import EasyMotorApp


class _InterfaceSelection:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


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

    def test_demo_rs485_selection_is_also_preserved(self):
        app = object.__new__(EasyMotorApp)
        selection = _InterfaceSelection("rs485")
        app.interface_var = selection

        app.app_mode = "engineer"
        self.assertEqual(app._connection_interface(), "rs485")
        self.assertEqual(selection.get(), "rs485")

        app.app_mode = "demo"
        self.assertEqual(app._connection_interface(), "rs485")


if __name__ == "__main__":
    unittest.main()
