import math
import struct
import unittest

from mastervolt_simulator import BatterySimulation, encode_frames, resolve_remaining_seconds


class EncodeFramesTests(unittest.TestCase):
    def test_encodes_signed_little_endian_values(self):
        frame_285, frame_385 = encode_frames(100, float("nan"), 32.0, -300, -10)
        self.assertEqual(frame_285, struct.pack("<hhhh", 1000, 3200, -100, -3000))
        self.assertTrue(math.isnan(struct.unpack("<f", frame_385[:4])[0]))
        self.assertEqual(frame_385[4:], bytes(4))

    def test_encodes_remaining_time_as_float32(self):
        _, frame_385 = encode_frames(50, 12.5, 24.0, 10, 20)
        self.assertEqual(frame_385, struct.pack("<f", 12.5) + bytes(4))

    def test_rounds_voltage_to_nearest_centivolt(self):
        frame_285, _ = encode_frames(50, 1, 12.345, 10, 20)
        self.assertEqual(struct.unpack("<h", frame_285[2:4])[0], 1234)

    def test_encodes_soc_and_temperature_in_tenths(self):
        frame_285, _ = encode_frames(50.5, 1, 24.0, 10, 20.3)
        self.assertEqual(struct.unpack("<h", frame_285[0:2])[0], 505)
        self.assertEqual(struct.unpack("<h", frame_285[4:6])[0], 203)

    def test_encodes_current_in_deciamps(self):
        positive, _ = encode_frames(50, 1, 24.0, 134.5, 20)
        negative, _ = encode_frames(50, 1, 24.0, -12.34, 20)
        self.assertEqual(struct.unpack("<h", positive[6:8])[0], 1345)
        self.assertEqual(struct.unpack("<h", negative[6:8])[0], -123)

    def test_rejects_out_of_range_values(self):
        invalid = [
            (-1, 0, 0, 0, 0),
            (0, -1, 0, 0, 0),
            (0, 0, 32.01, 0, 0),
            (0, 0, 0, 3276.8, 0),
            (0, 0, 0, 0, 71),
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                encode_frames(*values)


class RemainingTimeTests(unittest.TestCase):
    def test_positive_and_zero_current_have_no_remaining_time(self):
        self.assertTrue(math.isnan(resolve_remaining_seconds("120", 1)))
        self.assertTrue(math.isnan(resolve_remaining_seconds("120", 0)))

    def test_negative_current_uses_static_remaining_time(self):
        self.assertEqual(resolve_remaining_seconds("120.5", -1), 120.5)

    def test_static_nan_switch_forces_no_data(self):
        self.assertTrue(math.isnan(resolve_remaining_seconds("not a number", -1, True)))


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

    def test_cycles_at_zero_and_one_hundred_percent(self):
        simulation = BatterySimulation()
        discharged = simulation.step(60)
        self.assertEqual(discharged["mode"], "Discharging")
        self.assertEqual(discharged["soc"], 0)
        self.assertLess(discharged["amps"], 0)

        charging = simulation.step(0)
        self.assertEqual(charging["mode"], "Charging")
        self.assertEqual(charging["soc"], 0)
        self.assertGreater(charging["amps"], 0)
        self.assertTrue(math.isnan(charging["remaining_seconds"]))

        charged = simulation.step(60)
        self.assertEqual(charged["mode"], "Charging")
        self.assertEqual(charged["soc"], 100)
        self.assertGreater(charged["amps"], 0)

        full = simulation.step(0)
        self.assertEqual(full["mode"], "Discharging")
        self.assertEqual(full["soc"], 100)
        self.assertLess(full["amps"], 0)

    def test_each_phase_reports_remaining_time_in_seconds(self):
        simulation = BatterySimulation()
        sample = simulation.step(5)
        self.assertEqual(sample["remaining_seconds"], 3_300)
        self.assertEqual(sample["mode"], "Discharging")
        self.assertGreater(sample["soc"], 0)

    def test_non_negative_current_reports_unknown_remaining_time(self):
        simulation = BatterySimulation(mode="Charging", soc=0)
        sample = simulation.step(5)
        self.assertGreaterEqual(sample["amps"], 0)
        self.assertTrue(math.isnan(sample["remaining_seconds"]))

        simulation._current = lambda: 0
        self.assertTrue(math.isnan(simulation.step(5)["remaining_seconds"]))

    def test_current_and_voltage_match_energy_and_soc_change(self):
        simulation = BatterySimulation()
        start_soc = simulation.soc
        sample = simulation.step(0.01)
        removed_wh = (start_soc - simulation.soc) / 100 * simulation.energy_wh
        expected_wh = sample["volts"] * -sample["amps"] * (0.01 / 60)
        self.assertAlmostEqual(removed_wh, expected_wh, places=2)

    def test_office_profile_integrates_to_6000_wh_in_one_hour(self):
        simulation = BatterySimulation()
        step_minutes = 5 / 60
        discharge_wh = 0.0
        powers = []
        for _ in range(round(60 / step_minutes)):
            sample = simulation.step(step_minutes)
            power = sample["volts"] * -sample["amps"]
            powers.append(power)
            discharge_wh += power * step_minutes / 60

        self.assertAlmostEqual(discharge_wh, 6_000, delta=0.5)
        self.assertEqual(sample["soc"], 0)
        self.assertEqual(sample["mode"], "Discharging")
        self.assertGreater(max(powers) - min(powers), 1_000)

    def test_charge_tapers_and_voltage_rises_under_charge(self):
        simulation = BatterySimulation(mode="Charging", soc=0)
        start = simulation.step(0)
        simulation.phase_elapsed_minutes = 59
        simulation._update_soc()
        near_full = simulation.step(0)
        self.assertGreater(abs(start["amps"]), abs(near_full["amps"]))
        self.assertGreater(near_full["volts"], start["volts"])


if __name__ == "__main__":
    unittest.main()
