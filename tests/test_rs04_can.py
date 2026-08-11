import unittest

from rs04_can import (
    AtFrameDecoder,
    CanFrame,
    build_device_id_request,
    build_parameter_read,
    build_parameter_write,
    build_rejection_probe,
    encode_at_frame,
    make_id,
    parse_device_id_response,
    parse_parameter_response,
    split_id,
)


class Rs04ProtocolTests(unittest.TestCase):
    def test_id_fields_follow_official_29_bit_layout(self):
        arbitration_id = make_id(18, 0x00FD, 0x01)
        self.assertEqual(arbitration_id, 0x1200FD01)
        self.assertEqual(split_id(arbitration_id), (18, 0x00FD, 0x01))

    def test_official_type18_usb_can_example(self):
        frame = CanFrame(0x1200FD01, bytes.fromhex("05 70 00 00 01 00 00 00"))
        self.assertEqual(
            encode_at_frame(frame),
            bytes.fromhex(
                "41 54 90 07 E8 0C 08 05 70 00 00 01 00 00 00 0D 0A"
            ),
        )

    def test_motorstudio_type19_capture_round_trip(self):
        raw = bytes.fromhex(
            "41 54 98 07 EB FC 08 16 00 5C 00 01 51 34 34 0D 0A"
        )
        frames = AtFrameDecoder().feed(raw)
        self.assertEqual(frames, [CanFrame(0x1300FD7F, raw[7:15])])

    def test_stream_decoder_handles_noise_and_fragmentation(self):
        raw = encode_at_frame(build_device_id_request())
        decoder = AtFrameDecoder()
        self.assertEqual(decoder.feed(b"noiseA" + raw[:5]), [])
        self.assertEqual(decoder.feed(raw[5:]), [build_device_id_request()])

    def test_type17_request_is_little_endian(self):
        frame = build_parameter_read(0x701E)
        self.assertEqual(frame.arbitration_id, 0x1100FD7F)
        self.assertEqual(frame.data, bytes.fromhex("1E 70 00 00 00 00 00 00"))

    def test_official_type17_float_response(self):
        frame = CanFrame(
            make_id(17, 0x007F, 0xFD),
            bytes.fromhex("1E 70 00 00 00 00 F0 41"),
        )
        index, value = parse_parameter_response(frame)
        self.assertEqual(index, 0x701E)
        self.assertEqual(value, 30.0)

    def test_device_response_uid_is_little_endian(self):
        frame = CanFrame(
            make_id(0, 0x007F, 0xFE),
            bytes.fromhex("16 00 5C 00 01 51 34 34"),
        )
        self.assertEqual(parse_device_id_response(frame), (127, 0x34345101005C0016))

    def test_safe_write_whitelist_and_encoding(self):
        frame = build_parameter_write(0x7026, 10)
        self.assertEqual(frame.arbitration_id, 0x1200FD7F)
        self.assertEqual(frame.data, bytes.fromhex("26 70 00 00 0A 00 00 00"))
        self.assertEqual(
            build_parameter_write(0x7028, 0).data,
            bytes.fromhex("28 70 00 00 00 00 00 00"),
        )

    def test_unsafe_or_out_of_range_writes_are_rejected(self):
        for index, value in ((0x7005, 1), (0x7026, 0), (0x7028, 19), (0x701E, 1.0)):
            with self.subTest(index=index, value=value):
                with self.assertRaises(ValueError):
                    build_parameter_write(index, value)

    def test_fixed_rejection_probes_have_exact_non_motion_payloads(self):
        expected = {
            "readonly_mechpos": "19 70 00 00 00 00 00 00",
            "epscan_below_min": "26 70 00 00 00 00 00 00",
            "cantimeout_gap": "28 70 00 00 13 00 00 00",
        }
        for name, payload_hex in expected.items():
            with self.subTest(name=name):
                _label, _index, frame = build_rejection_probe(name)
                self.assertEqual(frame.arbitration_id, 0x1200FD7F)
                self.assertEqual(frame.data, bytes.fromhex(payload_hex))

    def test_arbitrary_rejection_probe_is_not_available(self):
        with self.assertRaises(ValueError):
            build_rejection_probe("arbitrary_write")


if __name__ == "__main__":
    unittest.main()
