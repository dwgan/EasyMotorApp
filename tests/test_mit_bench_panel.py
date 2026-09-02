import unittest
from types import SimpleNamespace

from easymotor.features.mit_bench.panel import MitBenchPanel


class MitBenchZeroTests(unittest.TestCase):
    @staticmethod
    def _panel(send_result: bool = True):
        observations: list[tuple[bool, bool]] = []
        panel = SimpleNamespace(
            zero_valid=True,
            _zero_requested=False,
            _require_connection=lambda: True,
            _render_status=lambda: None,
        )

        def send_zero():
            observations.append((panel.zero_valid, panel._zero_requested))

        panel._set_zero = send_zero

        def show_action_error(callback):
            callback()
            return send_result

        panel._show_action_error = show_action_error
        return panel, observations

    def test_zero_ack_gate_is_armed_before_type6_send(self):
        panel, observations = self._panel()

        MitBenchPanel._on_zero(panel)

        self.assertEqual(observations, [(False, True)])
        self.assertTrue(panel._zero_requested)

    def test_failed_type6_send_cancels_pending_ack(self):
        panel, _observations = self._panel(send_result=False)

        MitBenchPanel._on_zero(panel)

        self.assertFalse(panel.zero_valid)
        self.assertFalse(panel._zero_requested)

    def test_zero_sta_readback_marks_zero_valid(self):
        panel = SimpleNamespace(
            zero_valid=False,
            _zero_requested=True,
            _render_status=lambda: None,
        )

        MitBenchPanel.set_zero_valid(panel, True)

        self.assertTrue(panel.zero_valid)
        self.assertFalse(panel._zero_requested)


if __name__ == "__main__":
    unittest.main()
