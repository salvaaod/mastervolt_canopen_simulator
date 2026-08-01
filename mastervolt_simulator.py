"""Small Tkinter transmitter for two Mastervolt-style CAN frames."""

from __future__ import annotations

import ctypes
import os
import struct
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk


FRAME_285 = 0x285
FRAME_385 = 0x385
SEND_INTERVAL_MS = 5_000


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
        self.timer_id = None
        self.values = {
            "SOC (%)": tk.StringVar(value="80"),
            "Time (min)": tk.StringVar(value="120"),
            "Voltage (V)": tk.StringVar(value="24.00"),
            "Current (A)": tk.StringVar(value="0"),
            "Temperature (°C)": tk.StringVar(value="25"),
        }
        self.status = tk.StringVar(value="Disconnected")
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
            ttk.Spinbox(self, textvariable=variable, from_=low, to=high, increment=step, width=14).grid(
                row=row, column=1, sticky="ew", pady=3
            )
        self.button = ttk.Button(self, text="Connect and start", command=self.toggle)
        self.button.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 6))
        ttk.Label(self, textvariable=self.status).grid(row=7, column=0, columnspan=2)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

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
