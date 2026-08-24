import queue
import unittest

from easymotor.protocols.can_motor import AtFrameDecoder
from easymotor.transports.usb_can import USB_CAN_BAUD, UsbCanMotorTransport


class FakeSerial:
    def __init__(self, **settings):
        self.settings = settings
        self.writes = []
        self.closed = False

    @property
    def in_waiting(self):
        return 0

    def reset_input_buffer(self):
        pass

    def read(self, _size):
        return b""

    def write(self, raw):
        self.writes.append(raw)
        return len(raw)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class UsbCanTransportTests(unittest.TestCase):
    def test_transport_uses_vendor_baud_and_only_validated_demo_frames(self):
        events = queue.Queue()
        instances = []

        def factory(**settings):
            instance = FakeSerial(**settings)
            instances.append(instance)
            return instance

        transport = UsbCanMotorTransport(events, serial_factory=factory)
        transport.connect("COM_TEST")
        transport.set_active_report(True)
        transport.enumerate()
        transport.select_speed_mode()
        transport.enable()
        transport.command_velocity(5)
        transport.stop()
        transport.select_mit_mode()
        transport.close()

        adapter = instances[0]
        self.assertEqual(adapter.settings["port"], "COM_TEST")
        self.assertEqual(adapter.settings["baudrate"], USB_CAN_BAUD)
        self.assertTrue(adapter.closed)
        frames = [AtFrameDecoder().feed(raw)[0] for raw in adapter.writes]
        self.assertEqual(
            [frame.arbitration_id >> 24 for frame in frames],
            [24, 0, 18, 3, 18, 4, 18],
        )
        self.assertEqual(frames[2].data, bytes.fromhex("05 70 00 00 02 00 00 00"))
        self.assertEqual(frames[4].data[:4], bytes.fromhex("0A 70 00 00"))
        self.assertEqual(frames[6].data, bytes.fromhex("05 70 00 00 00 00 00 00"))


if __name__ == "__main__":
    unittest.main()
