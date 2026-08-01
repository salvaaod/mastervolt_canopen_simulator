import struct
import unittest

from mastervolt_simulator import encode_frames


class EncodeFramesTests(unittest.TestCase):
    def test_encodes_signed_little_endian_values(self):
        frame_285, frame_385 = encode_frames(100, -1, 32.0, -300, -10)
        self.assertEqual(frame_285, struct.pack("<hhhh", 100, -1, 3200, -300))
        self.assertEqual(frame_385, struct.pack("<h", -10) + bytes(6))

    def test_rounds_voltage_to_nearest_centivolt(self):
        frame_285, _ = encode_frames(50, 1, 12.345, 10, 20)
        self.assertEqual(struct.unpack("<h", frame_285[4:6])[0], 1234)

    def test_rejects_out_of_range_values(self):
        invalid = [
            (-1, 0, 0, 0, 0),
            (0, -2, 0, 0, 0),
            (0, 0, 32.01, 0, 0),
            (0, 0, 0, 301, 0),
            (0, 0, 0, 0, 71),
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                encode_frames(*values)


if __name__ == "__main__":
    unittest.main()
