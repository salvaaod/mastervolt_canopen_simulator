"""Small Tkinter transmitter for two Mastervolt-style CAN frames."""

from __future__ import annotations

import ctypes
import math
import os
import random
import struct
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk


FRAME_285 = 0x285
FRAME_385 = 0x385
SEND_INTERVAL_MS = 5_000
SIMULATION_STEP_MINUTES = 5.0


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
    """Accelerated, variable 24 V / 300 Ah LiFePO4 charge-cycle model."""

    capacity_ah: float = 300.0
    soc: float = 100.0
    mode: str = "Discharging"
    temperature: float = 25.0
    elapsed_minutes: float = 0.0
    rng: random.Random = field(default_factory=random.Random)

    def reset(self):
        """Every newly enabled simulation starts full and discharging."""
        self.soc = 100.0
        self.mode = "Discharging"
        self.temperature = 25.0
        self.elapsed_minutes = 0.0

    def _current(self) -> float:
        phase = self.elapsed_minutes / 60.0
        variation = 5.0 * math.sin(phase * 1.7) + 2.5 * math.sin(phase * 5.1)
        variation += self.rng.uniform(-2.0, 2.0)
        if self.mode == "Discharging":
            # A changing domestic load averaging about 30 A.
            return max(12.0, min(50.0, 30.0 + variation))

        # Approximate CC/CV charging: taper progressively for the final 10%.
        taper = 1.0 if self.soc < 90.0 else max(0.12, (100.0 - self.soc) / 10.0)
        return -max(5.0, min(55.0, (46.0 + variation) * taper))

    def _voltage(self, current: float) -> float:
        normalized = max(0.0, min(1.0, (self.soc - 20.0) / 80.0))
        # An 8-cell LiFePO4 plateau with steeper knees near either limit.
        open_circuit = 25.55 + 1.05 * normalized
        open_circuit += 1.25 * normalized**8 - 0.45 * (1.0 - normalized) ** 7
        loaded = open_circuit - current * 0.012
        ripple = 0.04 * math.sin(self.elapsed_minutes / 7.0)
        return max(23.0, min(29.2, loaded + ripple))

    def step(self, minutes: float = SIMULATION_STEP_MINUTES) -> dict[str, float | int | str]:
        """Advance the model and return values ready for the CAN fields."""
        current = self._current()
        self.soc -= current * (minutes / 60.0) / self.capacity_ah * 100.0
        self.elapsed_minutes += minutes

        if self.mode == "Discharging" and self.soc <= 20.0:
            self.soc = 20.0
            self.mode = "Charging"
            current = self._current()
        elif self.mode == "Charging" and self.soc >= 100.0:
            self.soc = 100.0
            self.mode = "Discharging"
            current = self._current()

        target_temp = 25.0 + abs(current) * 0.09
        ambient_wave = 1.2 * math.sin(self.elapsed_minutes / 180.0)
        self.temperature += (target_temp + ambient_wave - self.temperature) * 0.08
        boundary = 20.0 if self.mode == "Discharging" else 100.0
        hours = abs(boundary - self.soc) / 100.0 * self.capacity_ah / max(abs(current), 0.1)
        return {
            "soc": round(self.soc),
            "time_minutes": min(32767, round(hours * 60.0)),
            "volts": round(self._voltage(current), 2),
            "amps": round(current),
            "temperature": round(self.temperature),
            "mode": self.mode,
        }


def encode_frames(soc: int, time_minutes: int, volts: float, amps: int, temp: int):
    """Return both eight-byte payloads using signed 16-bit little endian fields."""
    if not 0 <= soc <= 100:
        raise ValueError("SOC must be between 0 and 100 %")
    if not -1 <= time_minutes <= 32767:
        raise ValueError("Time must be between -1 and 32767 minutes")
    if not 0 <= volts <= 32:
        raise ValueError("Voltage must be between 0 and 32.00 V")
    if not -300 <= amps <= 300:
        raise ValueError("Current must be between -300 and 300 A")
    if not -10 <= temp <= 70:
        raise ValueError("Temperature must be between -10 and 70 °C")

    raw_volts = round(volts * 100)
    data_285 = struct.pack("<hhhh", soc, time_minutes, raw_volts, amps)
    data_385 = struct.pack("<h", temp) + bytes(6)
    return data_285, data_385


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
            "Time (min)": tk.StringVar(value="120"),
            "Voltage (V)": tk.StringVar(value="24.00"),
            "Current (A)": tk.StringVar(value="0"),
            "Temperature (°C)": tk.StringVar(value="25"),
        }
        self.status = tk.StringVar(value="Disconnected")
        self.simulation_enabled = tk.BooleanVar(value=False)
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
        limits = [(0, 100, 1), (-1, 32767, 1), (0, 32, 0.01), (-300, 300, 1), (-10, 70, 1)]
        for row, ((label, variable), (low, high, step)) in enumerate(zip(self.values.items(), limits), 1):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            input_widget = ttk.Spinbox(
                self, textvariable=variable, from_=low, to=high, increment=step, width=14
            )
            input_widget.grid(row=row, column=1, sticky="ew", pady=3)
            self.inputs.append(input_widget)
        ttk.Checkbutton(
            self,
            text="Enable 24 V / 300 Ah battery simulation",
            variable=self.simulation_enabled,
            command=self.toggle_simulation,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 2))
        ttk.Label(self, textvariable=self.cycle_status, font=("TkDefaultFont", 10, "bold")).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        self.button = ttk.Button(self, text="Connect and start", command=self.toggle)
        self.button.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        ttk.Label(self, textvariable=self.status, wraplength=390).grid(row=9, column=0, columnspan=2)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

    def toggle_simulation(self):
        enabled = self.simulation_enabled.get()
        for input_widget in self.inputs:
            input_widget.configure(state="disabled" if enabled else "normal")
        if enabled:
            self.simulation.reset()
            self._show_simulation_values(self.simulation.step(0))
        else:
            self.cycle_status.set("Simulation off — manual values enabled")

    def _show_simulation_values(self, sample):
        self.values["SOC (%)"].set(str(sample["soc"]))
        self.values["Time (min)"].set(str(sample["time_minutes"]))
        self.values["Voltage (V)"].set(f'{sample["volts"]:.2f}')
        self.values["Current (A)"].set(str(sample["amps"]))
        self.values["Temperature (°C)"].set(str(sample["temperature"]))
        arrow = "▼" if sample["mode"] == "Discharging" else "▲"
        limit = "20%" if sample["mode"] == "Discharging" else "100%"
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
            return encode_frames(
                int(self.values["SOC (%)"].get()),
                int(self.values["Time (min)"].get()),
                float(self.values["Voltage (V)"].get()),
                int(self.values["Current (A)"].get()),
                int(self.values["Temperature (°C)"].get()),
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
