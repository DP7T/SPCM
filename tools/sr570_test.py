"""Check the RS-232 link to the SR570 preamp.

The SR570 can't answer, so the test is visual: watch the front panel.

    python tools/sr570_test.py            # lists serial ports
    python tools/sr570_test.py COM4       # runs the test on COM4

What should happen on the SR570:
  1. the bias LED turns on and the bias goes to +0.500 V
  2. after 3 s it goes to -0.500 V
  3. after 3 s it returns to 0 V and the bias switches off
Disconnect your device first (the bias appears at the SR570 input).
"""
import sys
import time

import serial
from serial.tools import list_ports


def send(ser, cmd):
    print("  ->", cmd)
    ser.write((cmd + "\r\n").encode("ascii"))
    ser.flush()
    time.sleep(0.05)
    echo = ser.read(ser.in_waiting or 0)
    if echo:
        print("     echo:", echo)


if len(sys.argv) < 2:
    print("Serial ports on this computer:")
    for p in list_ports.comports():
        print(f"  {p.device:8s} {p.description}  [{p.hwid}]")
    print("\nUsage: python tools/sr570_test.py COMx")
    sys.exit()

port = sys.argv[1]
ser = serial.Serial(port, 9600, bytesize=8, parity="N", stopbits=2, timeout=0.5)
print(f"Opened {port} (9600 8N2). Watch the SR570 front panel.")
send(ser, "BSLV500")
send(ser, "BSON1")
time.sleep(3)
send(ser, "BSLV-500")
time.sleep(3)
send(ser, "BSLV0")
send(ser, "BSON0")
ser.close()
print("Done. If the panel changed, the link works. "
      "If you saw an 'echo:' line, the cable and driver are fine as well.")
