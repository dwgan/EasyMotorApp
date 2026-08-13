import unittest

from easymotor.models.telemetry import TelemetryModel, format_cpu_load


class CpuLoadTelemetryTests(unittest.TestCase):
    def test_cpu_line_is_parsed_as_permille(self):
        model = TelemetryModel()

        self.assertTrue(
            model.parse_line(
                "CPU win=1000 rt=347 foc=301/612 enc=46/190 "
                "samples=50000/18200 enc_rate=18200/25000"
            )
        )
        assert model.cpu_load is not None
        self.assertEqual(model.cpu_load.realtime_percent, 34.7)
        self.assertEqual(model.cpu_load.encoder_completion_percent, 72.8)
        self.assertTrue(model.cpu_load.encoder_rate_low)
        self.assertEqual(model.cpu_update_count, 1)
        self.assertIn("FOC avg/peak=30.1/61.2%", format_cpu_load(model.cpu_load))

    def test_legacy_cpu_line_without_encoder_rate_remains_supported(self):
        model = TelemetryModel()

        self.assertTrue(
            model.parse_line(
                "CPU win=1000 rt=347 foc=301/612 enc=46/190 samples=50000/25000"
            )
        )
        assert model.cpu_load is not None
        self.assertIsNone(model.cpu_load.encoder_completion_percent)

    def test_unrelated_line_does_not_replace_cpu_state(self):
        model = TelemetryModel()
        self.assertFalse(model.parse_line("RT t=1000 mci=0"))
        self.assertIsNone(model.cpu_load)

    def test_encoder_model_line_uses_shared_model_ids(self):
        model = TelemetryModel()
        self.assertTrue(
            model.parse_line(
                "ENC port=0 role=1 model=4 raw=123 pos=123 vel=0 valid=1"
            )
        )
        self.assertEqual(model.encoder_values()[0].model_name, "AS5047P")
        self.assertEqual(model.encoder_update_count, 1)

    def test_legacy_as5047p_health_is_recorded_without_inventing_a_port(self):
        model = TelemetryModel()

        model.note_as5047p_health()

        self.assertTrue(model.as5047p_health_seen)
        self.assertEqual(model.encoder_values(), ())


if __name__ == "__main__":
    unittest.main()
