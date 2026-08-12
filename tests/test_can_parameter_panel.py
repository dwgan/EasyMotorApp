import unittest

from easymotor.features.can_parameters.panel import CanParameterPanel
from easymotor.protocols.can_motor import CanFrame, make_id
from easymotor.services.endurance_service import LongRunSession


class _Value:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = str(value)


class CanParameterPanelRoutingTests(unittest.TestCase):
    def test_type17_response_is_consumed_by_shared_panel(self):
        panel = object.__new__(CanParameterPanel)
        panel.host_id = 0xFD
        panel.long_run = LongRunSession()
        panel.pending_verification = {}
        panel.pending_rejection = {}
        panel._verify_after_id = None
        panel.last_value_var = _Value()
        logs = []
        panel._log = lambda level, text: logs.append((level, text))
        panel._state_changed = lambda: None

        frame = CanFrame(
            make_id(17, 0x007F, 0xFD),
            bytes.fromhex("19 70 00 00 00 00 00 3F"),
        )

        self.assertTrue(panel.handle_frame(frame))
        self.assertIn("0x7019 mechPos", panel.last_value_var.value)
        self.assertTrue(any("mechPos" in text for _level, text in logs))

    def test_non_parameter_frame_is_not_consumed(self):
        panel = object.__new__(CanParameterPanel)
        panel.host_id = 0xFD

        frame = CanFrame(make_id(2, 0x007F, 0xFD), bytes(8))

        self.assertFalse(panel.handle_frame(frame))

    def test_verification_timeout_releases_busy_state(self):
        panel = object.__new__(CanParameterPanel)
        panel.pending_verification = {0x7026: 2}
        panel.pending_rejection = {}
        panel._verify_after_id = "scheduled"
        logs = []
        state_changes = []
        panel._log = lambda level, text: logs.append((level, text))
        panel._state_changed = lambda: state_changes.append(True)

        panel._expire_verification(0x7026)

        self.assertFalse(panel.pending_verification)
        self.assertIsNone(panel._verify_after_id)
        self.assertTrue(state_changes)
        self.assertTrue(any("timed out" in text for _level, text in logs))


if __name__ == "__main__":
    unittest.main()
