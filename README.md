# Mastervolt CAN frame simulator

A small Windows Tkinter application that sends two **standard 11-bit CAN data
frames** through a GCAN USBCAN-II adapter every five seconds. It only transmits
the frames; it does not implement CANopen NMT, heartbeat, SDO, or PDO services.

## Payloads

Every value is encoded as a signed 16-bit little-endian integer. Both payloads
are padded to eight bytes.

| CAN ID | Bytes | Value | Range / scaling |
|---|---:|---|---|
| `0x285` | 0-1 | SOC | 0 to 100 % |
| `0x285` | 2-3 | Time | -1 to 32767 minutes |
| `0x285` | 4-5 | Voltage | 0 to 32.00 V, raw value = V x 100 |
| `0x285` | 6-7 | Current | -300 to 300 A |
| `0x385` | 0-1 | Temperature | -10 to 70 °C |
| `0x385` | 2-7 | Padding | zero |

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

The defaults select device index 0, CAN channel 0, USBCAN-II (`device type 4`),
and 250 kbit/s (`Timing0=0x01`, `Timing1=0x1C`). Change `DeviceConfig` in the
script if your adapter or network differs.

## Test without hardware

```console
python -m unittest -v
```
