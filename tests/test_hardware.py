import unittest

from hardware import HardwareBridge


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.device = HardwareBridge("test-device")

    def test_disconnection_sequence_reset_and_duplicate_frame(self):
        bridge = self.device
        self.assertFalse(bridge.ingest(b"not a telemetry frame", now=0))
        self.assertTrue(bridge.ingest(b"GZ1,0,N,C\r\n", now=1))
        self.assertEqual(bridge.snapshot(now=1)["target"], "center")
        bridge.ingest(b"GZ1,1,L,L", now=1.1)
        bridge.ingest(b"GZ1,1,B,L", now=1.3)
        status = bridge.snapshot(now=1.3)
        self.assertEqual((status["event"], status["zone"], status["target"]), (1, "both", "left"))
        self.assertFalse(bridge.snapshot(now=3)["connected"])
        bridge.ingest(b"GZ1,3,R,R", now=3)
        self.assertEqual(bridge.snapshot(now=3)["event"], 1)
        bridge.ingest(b"GZ1,0,N,C", now=3.2)
        bridge.ingest(b"GZ1,1,R,R", now=3.4)
        self.assertEqual(bridge.snapshot(now=3.4)["event"], 2)

    def test_uint32_rollover_and_malformed_frame_validation(self):
        bridge = self.device
        self.assertFalse(bridge.ingest(b"GZ1,4294967296,N,C", now=1))
        self.assertFalse(bridge.ingest(b"GZ1,3,R,C", now=1))
        bridge.ingest(b"GZ1,4294967295,L,L", now=1)
        bridge.ingest(b"GZ1,0,R,R", now=1.1)
        self.assertEqual(bridge.snapshot(now=1.1)["event"], 1)

    def test_serial_reassembles_partial_lines_and_discards_oversized_frame(self):
        bridge = self.device

        class Device:
            in_waiting = 1
            chunks = iter([b"Boot\nGZ1,0,N", b",C\r\n" + b"X" * 70, b"\nGZ1,1,L,L\n"])

            def read(self, _):
                chunk = next(self.chunks, b"")
                if not chunk:
                    bridge.stop.set()
                return chunk

        bridge.read(Device())
        self.assertEqual(bridge.snapshot()["event"], 1)
        self.assertEqual(bridge.snapshot()["target"], "left")
