import queue
import unittest
from collections import deque

from easymotor_app import (
    EasyMotorApp,
    WAVE_SINGLE_BLOCK_SAMPLES,
    WAVE_SINGLE_SOF,
    compress_envelope_entries,
    compress_wave_points,
    parse_debug_baud,
    parse_wave_time_window,
)


def _single_channel_frame(
    seq: int, channel: int, samples: tuple[int, ...], dropped: int = 0
) -> bytes:
    frame = bytearray((WAVE_SINGLE_SOF, seq & 0xFF, seq >> 8, channel, len(samples)))
    frame.extend((dropped & 0xFF, dropped >> 8))
    for sample in samples:
        frame.extend(int(sample).to_bytes(2, "little", signed=True))
    checksum = 0
    for byte in frame[1:]:
        checksum ^= byte
    frame.append(checksum)
    return bytes(frame)


class DebugBaudTests(unittest.TestCase):
    def test_common_and_custom_baud_rates_are_accepted(self):
        self.assertEqual(parse_debug_baud("4,000,000"), 4_000_000)
        self.assertEqual(parse_debug_baud("3_500_000"), 3_500_000)

    def test_invalid_baud_rates_are_rejected(self):
        for value in ("", "fast", "1200", "12000001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_debug_baud(value)


class WaveDisplayTests(unittest.TestCase):
    def test_time_window_parser_supports_ms_and_seconds(self):
        self.assertAlmostEqual(parse_wave_time_window("200 ms"), 0.2)
        self.assertAlmostEqual(parse_wave_time_window("2 s"), 2.0)

    def test_pixel_compression_keeps_noise_extrema(self):
        points = [(index, 0) for index in range(100)]
        points[24] = (24, -500)
        points[25] = (25, 700)

        compressed = compress_wave_points(points, 10)

        self.assertIn((24, -500), compressed)
        self.assertIn((25, 700), compressed)
        self.assertLessEqual(len(compressed), 20)

    def test_envelope_compression_keeps_noise_extrema(self):
        entries = [(index, -index, index) for index in range(100)]
        entries[24] = (24, -900, 24)
        entries[25] = (25, -25, 800)

        compressed = compress_envelope_entries(entries, 10)

        self.assertTrue(any(low == -900 for _, low, _ in compressed))
        self.assertTrue(any(high == 800 for _, _, high in compressed))
        self.assertLessEqual(len(compressed), 10)

    def test_incremental_display_envelope_uses_fixed_sample_buckets(self):
        app = object.__new__(EasyMotorApp)
        app._wave_display_entries = {
            name: deque() for name in ("u", "v", "w")
        }
        app._wave_display_accumulators = {
            name: None for name in ("u", "v", "w")
        }
        app._wave_display_bucket_samples = 3

        app._append_wave_display_sample("u", 10, 20)
        app._append_wave_display_sample("u", 11, -50)
        app._append_wave_display_sample("u", 12, 80)

        self.assertEqual(list(app._wave_display_entries["u"]), [(10, -50, 80)])
        self.assertIsNone(app._wave_display_accumulators["u"])


class SingleChannelWaveFrameTests(unittest.TestCase):
    def test_fragmented_full_sample_block_is_decoded(self):
        app = object.__new__(EasyMotorApp)
        app.rx_queue = queue.Queue()
        samples = tuple(range(-64, 64))
        self.assertEqual(len(samples), WAVE_SINGLE_BLOCK_SAMPLES)
        encoded = _single_channel_frame(123, 1, samples)
        pending = bytearray(encoded[:17])

        app._extract_wave_frames(pending)
        self.assertTrue(app.rx_queue.empty())

        pending.extend(encoded[17:])
        app._extract_wave_frames(pending)

        kind, payload = app.rx_queue.get_nowait()
        self.assertEqual(kind, "wave_single")
        self.assertEqual(payload, (123, 1, 0, samples))
        self.assertFalse(pending)

    def test_invalid_channel_block_is_discarded(self):
        app = object.__new__(EasyMotorApp)
        app.rx_queue = queue.Queue()
        pending = bytearray(_single_channel_frame(1, 3, (10, 20)))

        app._extract_wave_frames(pending)

        self.assertTrue(app.rx_queue.empty())

    def test_full_sample_handler_reports_firmware_ring_drops(self):
        app = object.__new__(EasyMotorApp)
        app._wave_last_seq = None
        app._wave_single_last_dropped = None
        app._wave_lost_count = 0
        app._wave_glitch_count = 0
        app._wave_frame_count = 0
        app._wave_prev_raw = None
        app._wave_buffers = {name: deque() for name in ("u", "v", "w")}
        app._wave_display_entries = {
            name: deque() for name in ("u", "v", "w")
        }
        app._wave_display_accumulators = {
            name: None for name in ("u", "v", "w")
        }
        app._wave_display_bucket_samples = 5
        app._wave_csv_file = None
        app._wave_csv_rows = []

        app._on_wave_single_frame((10, 2, 3, (100, 101, 102)))

        self.assertEqual(app._wave_lost_count, 3)
        self.assertEqual(list(app._wave_buffers["w"]), [(10, 100), (11, 101), (12, 102)])
        self.assertEqual(app._wave_frame_count, 3)


if __name__ == "__main__":
    unittest.main()
