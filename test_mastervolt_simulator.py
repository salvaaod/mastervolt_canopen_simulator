import struct
import unittest

from mastervolt_simulator import BatterySimulation, encode_frames


class EncodeFramesTests(unittest.TestCase):
    def test_encodes_signed_little_endian_values(self):
        frame_285, frame_385 = encode_frames(100, -1, 32.0, -300, -10)
        self.assertEqual(frame_285, struct.pack("<hhhh", 100, -1, 3200, -3000))
        self.assertEqual(frame_385, struct.pack("<h", -10) + bytes(6))

    def test_rounds_voltage_to_nearest_centivolt(self):
        frame_285, _ = encode_frames(50, 1, 12.345, 10, 20)
        self.assertEqual(struct.unpack("<h", frame_285[4:6])[0], 1234)

    def test_encodes_current_in_deciamps(self):
        positive, _ = encode_frames(50, 1, 24.0, 134.5, 20)
        negative, _ = encode_frames(50, 1, 24.0, -12.34, 20)
        self.assertEqual(struct.unpack("<h", positive[6:8])[0], 1345)
        self.assertEqual(struct.unpack("<h", negative[6:8])[0], -123)

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
        self.assertEqual(sample["amps"], round(sample["amps"], 1))

    def test_cycles_at_twenty_and_one_hundred_percent(self):
        simulation = BatterySimulation()
        discharged = simulation.step(30)
        self.assertEqual(discharged["mode"], "Discharging")
        self.assertEqual(discharged["soc"], 20)
        self.assertLess(discharged["amps"], 0)

        charging = simulation.step(0)
        self.assertEqual(charging["mode"], "Charging")
        self.assertEqual(charging["soc"], 20)
        self.assertGreater(charging["amps"], 0)
        self.assertEqual(charging["time_minutes"], -1)

        charged = simulation.step(30)
        self.assertEqual(charged["mode"], "Charging")
        self.assertEqual(charged["soc"], 100)
        self.assertGreater(charged["amps"], 0)

        full = simulation.step(0)
        self.assertEqual(full["mode"], "Discharging")
        self.assertEqual(full["soc"], 100)
        self.assertLess(full["amps"], 0)

    def test_each_phase_counts_down_from_thirty_minutes(self):
        simulation = BatterySimulation()
        sample = simulation.step(5)
        self.assertEqual(sample["time_minutes"], 25)
        self.assertEqual(sample["mode"], "Discharging")
        self.assertGreater(sample["soc"], 20)

    def test_non_negative_current_reports_unknown_remaining_time(self):
        simulation = BatterySimulation(mode="Charging", soc=20)
        sample = simulation.step(5)
        self.assertGreaterEqual(sample["amps"], 0)
        self.assertEqual(sample["time_minutes"], -1)

        simulation._current = lambda: 0
        self.assertEqual(simulation.step(5)["time_minutes"], -1)

    def test_current_and_voltage_match_energy_and_soc_change(self):
        simulation = BatterySimulation()
        start_soc = simulation.soc
        sample = simulation.step(0.01)
        removed_wh = (start_soc - simulation.soc) / 100 * simulation.energy_wh
        expected_wh = sample["volts"] * -sample["amps"] * (0.01 / 60)
        self.assertAlmostEqual(removed_wh, expected_wh, places=2)

    def test_office_profile_integrates_to_1440_wh_in_thirty_minutes(self):
        simulation = BatterySimulation()
        step_minutes = 5 / 60
        discharge_wh = 0.0
        powers = []
        for _ in range(round(30 / step_minutes)):
            sample = simulation.step(step_minutes)
            power = sample["volts"] * -sample["amps"]
            powers.append(power)
            discharge_wh += power * step_minutes / 60

        self.assertAlmostEqual(discharge_wh, 1_440, delta=0.25)
        self.assertEqual(sample["soc"], 20)
        self.assertEqual(sample["mode"], "Discharging")
        self.assertGreater(max(powers) - min(powers), 1_000)

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
