import struct
import unittest

from easymotor.protocols.can_motor import (
    AtFrameDecoder,
    CanFrame,
    MODE_MOTOR,
    MitCommand,
    build_active_report,
    build_device_id_request,
    build_demo_run_mode,
    build_enable,
    build_parameter_read,
    build_parameter_write,
    build_mit_control,
    build_rejection_probe,
    build_stop,
    build_save,
    build_set_node_id,
    build_set_zero,
    build_velocity_control,
    encode_at_frame,
    make_id,
    parse_device_id_response,
    parse_feedback,
    parse_parameter_response,
    split_id,
)


class CanProtocolTests(unittest.TestCase):
    def test_type3_enable_type4_stop_and_type24_reporting(self):
        self.assertEqual(build_enable(), CanFrame(0x0300FD7F, bytes(8)))
        self.assertEqual(build_stop(), CanFrame(0x0400FD7F, bytes(8)))
        self.assertEqual(
            build_active_report(True),
            CanFrame(0x1800FD7F, bytes.fromhex("00 00 00 00 00 00 00 01")),
        )

    def test_demo_velocity_uses_explicit_type18_speed_reference(self):
        frame = build_velocity_control(100)
        self.assertEqual(frame.arbitration_id, 0x1200FD7F)
        self.assertEqual(frame.data[0:4], bytes.fromhex("0A 70 00 00"))
        self.assertAlmostEqual(
            struct.unpack("<f", frame.data[4:8])[0],
            100 / 9 * (2 * 3.141592653589793 / 60),
            places=6,
        )

    def test_demo_mode_select_only_allows_mit_and_speed_pi(self):
        self.assertEqual(
            build_demo_run_mode(2).data,
            bytes.fromhex("05 70 00 00 02 00 00 00"),
        )
        self.assertEqual(
            build_demo_run_mode(0).data,
            bytes.fromhex("05 70 00 00 00 00 00 00"),
        )
        for value in (7, 2.0, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_demo_run_mode(value)

    def test_openarmx_mit_golden_vector(self):
        command = MitCommand(
            position_rad=0.02,
            velocity_rad_s=0.1,
            kp=10.0,
            kd=1.0,
            torque_nm=0.0,
        )
        frame = build_mit_control(command, node_id=1)
        self.assertEqual(frame.arbitration_id, 0x01800001)
        self.assertEqual(frame.data, bytes.fromhex("80 34 80 DA 00 83 02 8F"))

    def test_openarmx_startup_sequence_requires_no_type6_zero(self):
        command = MitCommand()
        sequence = (
            build_stop(node_id=1),
            build_parameter_write(0x7005, 0, node_id=1),
            build_enable(node_id=1),
            build_mit_control(command, node_id=1),
        )
        self.assertEqual(
            [frame.arbitration_id for frame in sequence],
            [0x0400FD01, 0x1200FD01, 0x0300FD01, 0x01800001],
        )
        self.assertEqual(sequence[1].data, bytes.fromhex("05 70 00 00 00 00 00 00"))
        self.assertEqual(sequence[3].data, bytes.fromhex("80 00 80 00 00 00 00 00"))

    def test_mit_safety_envelope_rejects_invalid_values(self):
        invalid = (
            MitCommand(position_rad=float("nan")),
            MitCommand(velocity_rad_s=15.01),
            MitCommand(kp=5000.01),
            MitCommand(kd=100.01),
            MitCommand(torque_nm=0.01),
        )
        for command in invalid:
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    build_mit_control(command, node_id=1)

    def test_zero_id_and_save_frames_match_rs04_layout(self):
        self.assertEqual(
            build_set_zero(node_id=1),
            CanFrame(0x0600FD01, bytes.fromhex("01 00 00 00 00 00 00 00")),
        )
        self.assertEqual(build_set_node_id(2, node_id=1).arbitration_id, 0x0702FD01)
        self.assertEqual(build_save(node_id=2), CanFrame(0x1600FD02, bytes(8)))

    def test_torque_calibration_parameters_use_bounded_safe_writes(self):
        positive = build_parameter_write(0x7030, 0.009, node_id=1)
        negative = build_parameter_write(0x7031, 0.010, node_id=1)
        calibrated = build_parameter_write(0x7032, 1, node_id=1)
        self.assertEqual(positive.data[:4], bytes.fromhex("30 70 00 00"))
        self.assertEqual(negative.data[:4], bytes.fromhex("31 70 00 00"))
        self.assertEqual(calibrated.data, bytes.fromhex("32 70 00 00 01 00 00 00"))
        with self.assertRaises(ValueError):
            build_parameter_write(0x7030, 0.101, node_id=1)

    def test_rotor_alignment_maintenance_write_can_only_clear(self):
        clear = build_parameter_write(0x7033, 0, node_id=1)
        self.assertEqual(clear.data, bytes.fromhex("33 70 00 00 00 00 00 00"))
        with self.assertRaises(ValueError):
            build_parameter_write(0x7033, 1, node_id=1)

    def test_speed_reference_rejects_values_outside_demo_envelope(self):
        for value in (-101, 101, 5.0, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_velocity_control(value)
        with self.assertRaises(ValueError):
            build_velocity_control(5, node_id=0x80)

    def test_type2_feedback_decodes_mode_faults_and_big_endian_values(self):
        frame = CanFrame(
            make_id(2, 0x807F, 0xFD),
            bytes.fromhex("80 00 80 00 80 00 00 FA"),
        )
        feedback = parse_feedback(frame)
        self.assertEqual(feedback.node_id, 0x7F)
        self.assertEqual(feedback.mode, MODE_MOTOR)
        self.assertEqual(feedback.faults, 0)
        self.assertAlmostEqual(feedback.temperature_c, 25.0)
        self.assertAlmostEqual(feedback.position_rad, 0.0, places=3)
        self.assertAlmostEqual(feedback.velocity_rad_s, 0.0, places=2)
        self.assertEqual(feedback.torque_nm, 0.0)

    def test_id_fields_follow_official_29_bit_layout(self):
        arbitration_id = make_id(18, 0x00FD, 0x01)
        self.assertEqual(arbitration_id, 0x1200FD01)
        self.assertEqual(split_id(arbitration_id), (18, 0x00FD, 0x01))

    def test_broadcast_target_id_is_supported(self):
        self.assertEqual(make_id(3, 0x00FD, 0xFE), 0x0300FDFE)
        self.assertEqual(split_id(0x0300FDFE), (3, 0x00FD, 0xFE))

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

    def test_official_temperature_parameters_are_signed_deci_celsius(self):
        frame = CanFrame(
            make_id(17, 0x007F, 0xFD),
            bytes.fromhex("05 30 00 00 FB FF 00 00"),
        )
        index, value = parse_parameter_response(frame)
        self.assertEqual(index, 0x3005)
        self.assertEqual(value, -5)
        self.assertEqual(build_parameter_read(0x3006).data[:2], bytes.fromhex("06 30"))

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
