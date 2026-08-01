import struct
import unittest

from mastervolt_simulator import BatterySimulation, encode_frames


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


class BatterySimulationTests(unittest.TestCase):
    def test_starts_full_and_discharge_values_are_plausible(self):
        simulation = BatterySimulation()
        sample = simulation.step()
        self.assertEqual(sample["mode"], "Discharging")
        self.assertLess(simulation.soc, 100)
        self.assertLess(sample["amps"], 0)
        self.assertGreaterEqual(sample["volts"], 23)
        self.assertLessEqual(sample["volts"], 29.2)

    def test_cycles_at_twenty_and_one_hundred_percent(self):
        simulation = BatterySimulation()
        charging = simulation.step(30)
        self.assertEqual(charging["mode"], "Charging")
        self.assertEqual(charging["soc"], 20)
        self.assertGreater(charging["amps"], 0)

        full = simulation.step(30)
        self.assertEqual(full["mode"], "Discharging")
        self.assertEqual(full["soc"], 100)
        self.assertLess(full["amps"], 0)

    def test_each_phase_counts_down_from_thirty_minutes(self):
        simulation = BatterySimulation()
        sample = simulation.step(5)
        self.assertEqual(sample["time_minutes"], 25)
        self.assertEqual(sample["mode"], "Discharging")
        self.assertGreater(sample["soc"], 20)

    def test_current_matches_capacity_and_accelerated_soc_change(self):
        simulation = BatterySimulation()
        start_soc = simulation.soc
        sample = simulation.step(0.01)
        removed_ah = (start_soc - simulation.soc) / 100 * simulation.capacity_ah
        expected_ah = -sample["amps"] * (0.01 / 60) * 16
        self.assertAlmostEqual(removed_ah, expected_ah, places=3)

    def test_charge_tapers_and_voltage_rises_under_charge(self):
        simulation = BatterySimulation(mode="Charging", soc=20)
        start = simulation.step(0)
        simulation.phase_elapsed_minutes = 29
        simulation._update_soc()
        near_full = simulation.step(0)
        self.assertGreater(abs(start["amps"]), abs(near_full["amps"]))
        self.assertGreater(near_full["volts"], start["volts"])


if __name__ == "__main__":
    unittest.main()
