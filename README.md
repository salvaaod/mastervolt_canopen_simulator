# Mastervolt CAN frame simulator

A small Windows Tkinter application that sends two **standard 11-bit CAN data
frames** through a GCAN USBCAN-II adapter every five seconds. It only transmits
the frames; it does not implement CANopen NMT, heartbeat, SDO, or PDO services.

## Payloads

Multi-byte values are little endian. Both payloads are eight bytes long.

| CAN ID | Bytes | Value | Range / scaling |
|---|---:|---|---|
| `0x285` | 0-1 | SOC (signed 16-bit) | 0 to 100 %, raw value = % x 10 |
| `0x285` | 2-3 | Voltage (signed 16-bit) | 0 to 32.00 V, raw value = V x 100 |
| `0x285` | 4-5 | Temperature (signed 16-bit) | -10.0 to 70.0 °C, raw value = °C x 10 |
| `0x285` | 6-7 | Current (signed 16-bit) | -300.0 to 300.0 A, raw value = A x 10 |
| `0x385` | 0-3 | Remaining time (float32) | Minutes; NaN means no data |
| `0x385` | 4-7 | Padding | zero |

## Run

1. On Windows, install Python 3 with Tk support and the GCAN driver.
2. Connect and terminate the CAN bus correctly.
3. Keep `ECanVci.dll` beside `mastervolt_simulator.py`. The DLL and Python must
   have matching 32/64-bit architectures.
4. Run:

   ```powershell
   python mastervolt_simulator.py
   ```

5. Enter the desired values and select **Connect and start**. The first pair is
   sent immediately, then both frames are sent every five seconds. Values edited
   while running are used for the next transmission.

## Battery simulation mode

Select **Enable 24 V / 75 Ah battery simulation** to replace the manual fields
with an 8-cell LiFePO4 model. It always begins at 100% and discharges to 20%
over 30 minutes, then charges to 100% over 30 minutes and repeats. Each
five-second transmission advances the profile by five seconds. The discharge
power averages approximately 2,880 W, so its real 30-minute CAN-sample integral
is 1,440 Wh (80% of the nominal 1,800 Wh battery energy).

The discharge profile combines an office base load with smooth HVAC cycles and
shorter equipment-load variations. Current is calculated from requested power,
battery voltage, and pack resistance so downstream voltage/current integration
matches the energy profile after deciamp and centivolt CAN rounding. Charging
uses 96% efficiency and current taper. Voltage follows an 8-cell LiFePO4
open-circuit curve plus 2 mΩ pack-resistance sag or charge lift.
Temperature responds to I²R heating, cooling, and ambient variation. During
discharge, the time field counts down to the 20% limit. During charge, when
current is zero or positive, the time field is `NaN` (no remaining-time data).
Negative current means discharge and positive current means charge. The
direction indicator and all five transmitted values update before every CAN
transmission. Clear the check box to restore manual editing.

The defaults select device index 0, CAN channel 0, USBCAN-II (`device type 4`),
and 250 kbit/s (`Timing0=0x01`, `Timing1=0x1C`). Change `DeviceConfig` in the
script if your adapter or network differs.

## Test without hardware

```console
python -m unittest -v
```
