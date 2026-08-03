"""Small Tkinter transmitter for two Mastervolt-style CAN frames."""

from __future__ import annotations

import ctypes
import math
import os
import struct
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk


FRAME_285 = 0x285
FRAME_385 = 0x385
SEND_INTERVAL_MS = 5_000
SIMULATION_STEP_MINUTES = SEND_INTERVAL_MS / 60_000
PHASE_DURATION_MINUTES = 60.0
MIN_SOC = 10.0
USABLE_SOC_PERCENT = 100.0 - MIN_SOC
# Compensates for deciamp and centivolt CAN fields at five-second sampling.
CAN_ENERGY_CALIBRATION = 0.99964


class CAN_OBJ(ctypes.Structure):
    _fields_ = [
        ("ID", ctypes.c_uint),
        ("TimeStamp", ctypes.c_uint),
        ("TimeFlag", ctypes.c_ubyte),
        ("SendType", ctypes.c_ubyte),
        ("RemoteFlag", ctypes.c_ubyte),
        ("ExternFlag", ctypes.c_ubyte),
        ("DataLen", ctypes.c_ubyte),
        ("Data", ctypes.c_ubyte * 8),
        ("Reserved", ctypes.c_ubyte * 3),
    ]


class INIT_CONFIG(ctypes.Structure):
    _fields_ = [
        ("AccCode", ctypes.c_uint),
        ("AccMask", ctypes.c_uint),
        ("Reserved", ctypes.c_uint),
        ("Filter", ctypes.c_ubyte),
        ("Timing0", ctypes.c_ubyte),
        ("Timing1", ctypes.c_ubyte),
        ("Mode", ctypes.c_ubyte),
    ]


@dataclass(frozen=True)
class DeviceConfig:
    dll_path: str = str(Path(__file__).with_name("ECanVci.dll"))
    device_type: int = 4  # USBCAN-II
    device_index: int = 0
    can_index: int = 0
    timing0: int = 0x01
    timing1: int = 0x1C  # 250 kbit/s


@dataclass
class BatterySimulation:
    """Timed, variable 24 V / 6 kWh LiFePO4 charge-cycle model."""

    capacity_ah: float = 250.0
    energy_wh: float = 6_000.0
    soc: float = 100.0
    mode: str = "Discharging"
    temperature: float = 25.0
    elapsed_minutes: float = 0.0
    phase_elapsed_minutes: float = 0.0

    def reset(self):
        """Every newly enabled simulation starts full and discharging."""
        self.soc = 100.0
        self.mode = "Discharging"
        self.temperature = 25.0
        self.elapsed_minutes = 0.0
        self.phase_elapsed_minutes = 0.0

    def _update_soc(self):
        progress = min(1.0, self.phase_elapsed_minutes / PHASE_DURATION_MINUTES)
        if self.mode == "Discharging":
            # Integral of office base load, HVAC cycling and equipment spikes.
            profile = progress
            profile += 0.28 / (2.0 * math.pi) * (1.0 - math.cos(2.0 * math.pi * progress))
            profile += 0.10 / (6.0 * math.pi) * (1.0 - math.cos(6.0 * math.pi * progress))
            profile += 0.06 / (14.0 * math.pi) * (1.0 - math.cos(14.0 * math.pi * progress))
            self.soc = 100.0 - USABLE_SOC_PERCENT * profile
        else:
            # Integral of a charge current which tapers as the battery fills.
            profile = 1.2 * progress - 0.2 * progress**2
            profile += 0.1 / (4.0 * math.pi) * (1.0 - math.cos(4.0 * math.pi * progress))
            self.soc = MIN_SOC + USABLE_SOC_PERCENT * profile

    def _current(self) -> float:
        progress = min(1.0, self.phase_elapsed_minutes / PHASE_DURATION_MINUTES)
        average_power = (
            self.energy_wh
            * (USABLE_SOC_PERCENT / 100.0)
            / (PHASE_DURATION_MINUTES / 60.0)
            * CAN_ENERGY_CALIBRATION
        )
        resistance = 0.002
        normalized = max(0.0, min(1.0, (self.soc - MIN_SOC) / USABLE_SOC_PERCENT))
        open_circuit = self._open_circuit_voltage(normalized)
        if self.mode == "Discharging":
            shape = 1.0 + 0.28 * math.sin(2.0 * math.pi * progress)
            shape += 0.10 * math.sin(6.0 * math.pi * progress)
            shape += 0.06 * math.sin(14.0 * math.pi * progress)
            power = average_power * shape
            # Solve P = I(OCV - IR), choosing the physically meaningful root.
            return -(open_circuit - math.sqrt(open_circuit**2 - 4 * resistance * power)) / (
                2 * resistance
            )

        charge_efficiency = 0.96
        shape = 1.2 - 0.4 * progress + 0.1 * math.sin(4.0 * math.pi * progress)
        power = average_power / charge_efficiency * shape
        polarized_voltage = open_circuit + 0.9 * normalized**6
        # Solve P = I(OCV + IR) for positive charging current.
        return (-polarized_voltage + math.sqrt(polarized_voltage**2 + 4 * resistance * power)) / (
            2 * resistance
        )

    @staticmethod
    def _open_circuit_voltage(normalized: float) -> float:
        voltage = 25.65 + 0.75 * normalized
        return voltage + 0.75 * normalized**8 - 0.55 * (1.0 - normalized) ** 7

    def _voltage(self, current: float) -> float:
        normalized = max(0.0, min(1.0, (self.soc - MIN_SOC) / USABLE_SOC_PERCENT))
        # An 8-cell LiFePO4 plateau with steeper knees near either limit.
        open_circuit = self._open_circuit_voltage(normalized)
        # The 250 Ah pack is modelled at about 2 milliohms, including
        # cells and interconnects, to give load sag and charge lift.
        loaded = open_circuit + current * 0.002
        if current > 0:
            # Cell polarization produces the familiar CV-region rise near full.
            loaded += 0.9 * normalized**6
        ripple = 0.04 * math.sin(self.elapsed_minutes / 7.0)
        return max(23.0, min(29.2, loaded + ripple))

    def step(self, minutes: float = SIMULATION_STEP_MINUTES) -> dict[str, float | int | str]:
        """Advance the model and return values ready for the CAN fields."""
        # Keep the boundary sample in the phase that produced it. The following
        # sample changes direction, ensuring each current profile spans 60 min.
        if self.phase_elapsed_minutes >= PHASE_DURATION_MINUTES - 1e-9:
            self.phase_elapsed_minutes -= PHASE_DURATION_MINUTES
            self.mode = "Charging" if self.mode == "Discharging" else "Discharging"
        self.elapsed_minutes += minutes
        self.phase_elapsed_minutes = min(
            PHASE_DURATION_MINUTES, self.phase_elapsed_minutes + minutes
        )
        self._update_soc()
        current = self._current()

        # First-order thermal response to I²R heating and a slowly varying room.
        ambient_wave = 1.2 * math.sin(self.elapsed_minutes / 180.0)
        target_temp = 25.0 + ambient_wave + current * current * 0.0001
        thermal_response = 1.0 - math.exp(-minutes / 20.0) if minutes else 0.0
        self.temperature += (target_temp - self.temperature) * thermal_response
        return {
            "soc": round(self.soc),
            "remaining_seconds": (
                math.nan
                if current >= 0
                else round((PHASE_DURATION_MINUTES - self.phase_elapsed_minutes) * 60)
            ),
            "volts": round(self._voltage(current), 2),
            "amps": round(current, 1),
            "temperature": round(self.temperature),
            "mode": self.mode,
        }


def encode_frames(
    soc: float, remaining_seconds: float, volts: float, amps: float, temp: float
):
    """Return the 0x285 measurements and 0x385 remaining-time payloads."""
    if not 0 <= soc <= 100:
        raise ValueError("SOC must be between 0 and 100 %")
    if (
        not math.isnan(remaining_seconds)
        and not 0 <= remaining_seconds <= 3.4028235e38
    ):
        raise ValueError("Remaining time must be non-negative or NaN (no data)")
    if not 0 <= volts <= 32:
        raise ValueError("Voltage must be between 0 and 32.00 V")
    if not -300 <= amps <= 300:
        raise ValueError("Current must be between -300 and 300 A")
    if not -10 <= temp <= 70:
        raise ValueError("Temperature must be between -10 and 70 °C")

    raw_soc = round(soc * 10)
    raw_volts = round(volts * 100)
    raw_temp = round(temp * 10)
    raw_amps = round(amps * 10)
    data_285 = struct.pack("<hhhh", raw_soc, raw_volts, raw_temp, raw_amps)
    data_385 = struct.pack("<f", remaining_seconds) + bytes(4)
    return data_285, data_385


def resolve_remaining_seconds(value: str, amps: float, force_nan: bool = False) -> float:
    """Resolve the remaining-time input, using NaN when no time is available."""
    if force_nan or amps >= 0:
        return math.nan
    return float(value)


class GCANDevice:
    def __init__(self, config: DeviceConfig):
        self.config = config
        self.dll = None
        self.is_open = False

    def _load(self):
        if os.name != "nt":
            raise RuntimeError("The supplied ECanVci.dll can only be used on Windows")
        self.dll = ctypes.WinDLL(self.config.dll_path)
        uint3 = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        self.dll.OpenDevice.argtypes = uint3
        self.dll.OpenDevice.restype = ctypes.c_uint
        self.dll.CloseDevice.argtypes = uint3[:2]
        self.dll.CloseDevice.restype = ctypes.c_uint
        self.dll.InitCAN.argtypes = uint3 + [ctypes.POINTER(INIT_CONFIG)]
        self.dll.InitCAN.restype = ctypes.c_uint
        self.dll.StartCAN.argtypes = uint3
        self.dll.StartCAN.restype = ctypes.c_uint
        self.dll.Transmit.argtypes = uint3 + [ctypes.POINTER(CAN_OBJ), ctypes.c_ulong]
        self.dll.Transmit.restype = ctypes.c_ulong

    def open(self):
        self._load()
        c = self.config
        if not self.dll.OpenDevice(c.device_type, c.device_index, 0):
            raise RuntimeError("OpenDevice failed")
        config = INIT_CONFIG(0, 0xFFFFFFFF, 0, 0, c.timing0, c.timing1, 0)
        if not self.dll.InitCAN(c.device_type, c.device_index, c.can_index, ctypes.byref(config)):
            self.close()
            raise RuntimeError("InitCAN failed")
        if not self.dll.StartCAN(c.device_type, c.device_index, c.can_index):
            self.close()
            raise RuntimeError("StartCAN failed")
        self.is_open = True

    def send(self, frame_id: int, payload: bytes):
        if not self.is_open or self.dll is None:
            raise RuntimeError("GCAN device is not connected")
        if len(payload) != 8:
            raise ValueError("A payload must contain exactly 8 bytes")
        frame = CAN_OBJ(ID=frame_id, SendType=0, RemoteFlag=0, ExternFlag=0, DataLen=8)
        frame.Data[:] = payload
        c = self.config
        sent = self.dll.Transmit(c.device_type, c.device_index, c.can_index, ctypes.byref(frame), 1)
        if sent != 1:
            raise RuntimeError(f"Transmit failed for ID 0x{frame_id:03X}")

    def close(self):
        if self.dll is not None:
            c = self.config
            self.dll.CloseDevice(c.device_type, c.device_index)
        self.is_open = False


class SimulatorApp(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=16)
        self.root = root
        self.device = GCANDevice(DeviceConfig())
        self.simulation = BatterySimulation()
        self.timer_id = None
        self.values = {
            "SOC (%)": tk.StringVar(value="80"),
            "Remaining time (s, NaN = no data)": tk.StringVar(value="120"),
            "Voltage (V)": tk.StringVar(value="24.00"),
            "Current (A)": tk.StringVar(value="0.0"),
            "Temperature (°C)": tk.StringVar(value="25"),
        }
        self.status = tk.StringVar(value="Disconnected")
        self.simulation_enabled = tk.BooleanVar(value=False)
        self.static_remaining_nan = tk.BooleanVar(value=False)
        self.cycle_status = tk.StringVar(value="Simulation off")
        self.inputs = []
        self._build()

    def _build(self):
        self.root.title("Mastervolt CAN Frame Simulator")
        self.root.resizable(False, False)
        self.grid(sticky="nsew")
        ttk.Label(self, text="CAN frames 0x285 / 0x385", font=("TkDefaultFont", 14, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 12)
        )
        limits = [
            (0, 100, 0.1),
            (0, 3.4028235e38, 1),
            (0, 32, 0.01),
            (-300, 300, 0.1),
            (-10, 70, 0.1),
        ]
        for row, ((label, variable), (low, high, step)) in enumerate(zip(self.values.items(), limits), 1):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            input_widget = ttk.Spinbox(
                self, textvariable=variable, from_=low, to=high, increment=step, width=14
            )
            input_widget.grid(row=row, column=1, sticky="ew", pady=3)
            self.inputs.append(input_widget)
        self.remaining_time_input = self.inputs[1]
        self.static_nan_control = ttk.Checkbutton(
            self,
            text="Static values: send remaining time as NaN",
            variable=self.static_remaining_nan,
            command=self.toggle_static_remaining_nan,
        )
        self.static_nan_control.grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 2))
        ttk.Checkbutton(
            self,
            text="Enable 24 V / 6 kWh battery simulation",
            variable=self.simulation_enabled,
            command=self.toggle_simulation,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(self, textvariable=self.cycle_status, font=("TkDefaultFont", 10, "bold")).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        self.button = ttk.Button(self, text="Connect and start", command=self.toggle)
        self.button.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        ttk.Label(self, textvariable=self.status, wraplength=390).grid(row=10, column=0, columnspan=2)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

    def toggle_static_remaining_nan(self):
        state = "disabled" if self.static_remaining_nan.get() else "normal"
        self.remaining_time_input.configure(state=state)

    def toggle_simulation(self):
        enabled = self.simulation_enabled.get()
        for input_widget in self.inputs:
            input_widget.configure(state="disabled" if enabled else "normal")
        self.static_nan_control.configure(state="disabled" if enabled else "normal")
        if enabled:
            self.simulation.reset()
            self._show_simulation_values(self.simulation.step(0))
        else:
            self.toggle_static_remaining_nan()
            self.cycle_status.set("Simulation off — manual values enabled")

    def _show_simulation_values(self, sample):
        self.values["SOC (%)"].set(str(sample["soc"]))
        self.values["Remaining time (s, NaN = no data)"].set(
            str(sample["remaining_seconds"])
        )
        self.values["Voltage (V)"].set(f'{sample["volts"]:.2f}')
        self.values["Current (A)"].set(f'{sample["amps"]:.1f}')
        self.values["Temperature (°C)"].set(str(sample["temperature"]))
        arrow = "▼" if sample["mode"] == "Discharging" else "▲"
        limit = "10%" if sample["mode"] == "Discharging" else "100%"
        self.cycle_status.set(f'{arrow} {sample["mode"]} — next limit: {limit}')

    def toggle(self):
        if self.device.is_open:
            self.stop()
            return
        try:
            self.device.open()
            self.button.configure(text="Stop and disconnect")
            self.send_frames()
        except Exception as error:
            self.device.close()
            messagebox.showerror("GCAN connection error", str(error))
            self.status.set("Disconnected")

    def _payloads(self):
        try:
            amps = float(self.values["Current (A)"].get())
            remaining_seconds = resolve_remaining_seconds(
                self.values["Remaining time (s, NaN = no data)"].get(),
                amps,
                self.static_remaining_nan.get() and not self.simulation_enabled.get(),
            )
            return encode_frames(
                float(self.values["SOC (%)"].get()),
                remaining_seconds,
                float(self.values["Voltage (V)"].get()),
                amps,
                float(self.values["Temperature (°C)"].get()),
            )
        except ValueError as error:
            raise ValueError(f"Invalid input: {error}") from error

    def send_frames(self):
        if not self.device.is_open:
            return
        try:
            if self.simulation_enabled.get():
                self._show_simulation_values(self.simulation.step())
            data_285, data_385 = self._payloads()
            self.device.send(FRAME_285, data_285)
            self.device.send(FRAME_385, data_385)
            self.status.set(f"Sent 0x285 and 0x385: {data_285.hex(' ')} / {data_385.hex(' ')}")
            self.timer_id = self.after(SEND_INTERVAL_MS, self.send_frames)
        except Exception as error:
            self.stop()
            messagebox.showerror("CAN transmission error", str(error))

    def stop(self):
        if self.timer_id is not None:
            self.after_cancel(self.timer_id)
            self.timer_id = None
        self.device.close()
        self.button.configure(text="Connect and start")
        self.status.set("Disconnected")

    def shutdown(self):
        self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    SimulatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
