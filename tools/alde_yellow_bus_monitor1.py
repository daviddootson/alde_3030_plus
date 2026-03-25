#!/usr/bin/env python3
"""
Alde 3030 Plus - Yellow CI-Bus Monitor v1
==========================================
Listen-only mode - sends 0x5B header to request status,
reads 0x1B response from panel. No 0x1A control frames sent.
Panel retains full control. Changes on panel display in real time.
"""

import serial
import time

PORT = '/dev/ttyAMA0'
BAUD = 19200

ID_INFO = 0x5B  # request status from panel

def lin_checksum_enhanced(frame_id, data):
    total = frame_id + sum(data)
    while total > 0xFF:
        total = (total & 0xFF) + (total >> 8)
    return (~total) & 0xFF

def send_break(ser):
    ser.baudrate = 1200
    ser.write(bytes([0x00]))
    ser.flush()
    time.sleep(0.002)
    ser.baudrate = BAUD

def send_header_only(ser, frame_id):
    send_break(ser)
    ser.write(bytes([0x55, frame_id]))
    ser.flush()

def read_bytes(ser, count, timeout=0.150):
    buf = bytearray()
    deadline = time.time() + timeout
    while time.time() < deadline and len(buf) < count:
        b = ser.read(1)
        if b:
            buf.extend(b)
            deadline = time.time() + 0.020
    return bytes(buf)

def decode_temp(b):
    if b == 0xFB: return "< -42°C"
    if b == 0xFC: return "> 83°C"
    if b == 0xFD: return "no sensor"
    if b == 0xFE: return "zone unused"
    if b == 0xFF: return "invalid"
    return f"{b * 0.5 - 42:.1f}°C"

def decode_info_frame(data):
    b3 = data[3]; b4 = data[4]; b5 = data[5]
    wm = {0:'off', 1:'normal', 2:'boost', 3:'auto'}
    return {
        'zone1_temp':   decode_temp(data[0]),
        'zone2_temp':   decode_temp(data[1]),
        'outdoor_temp': decode_temp(data[2]),
        'setpoint1':    f"{(b3&0x3F)*0.5+5:.1f}°C",
        'gas_active':   (b3>>6)&1,
        'valve_open':   (b3>>7)&1,
        'setpoint2':    f"{(b4&0x3F)*0.5+5:.1f}°C",
        'electric_kw':  (b4>>6)&3,
        'panel_on':     (b5>>0)&1,
        'panel_busy':   (b5>>1)&1,
        'error':        (b5>>2)&1,
        'water_mode':   wm.get((b5>>3)&3, '?'),
        'ac_input':     (b5>>5)&1,
        'ac_auto':      (b5>>6)&1,
        'pump_running': (b5>>7)&1,
    }

def main():
    print("Alde Yellow CI-Bus Monitor v1")
    print("="*50)
    print(f"Port: {PORT} at {BAUD} baud")
    print("Listen only - panel retains full control")
    print("Press Ctrl+C to stop")
    print()

    ser = serial.Serial(PORT, BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.005)

    last_state = None
    cycle = 0

    try:
        while True:
            cycle += 1

            # Send 0x5B header only - request status
            send_header_only(ser, ID_INFO)

            # Read 12 bytes: 3 echo + 9 response
            raw = read_bytes(ser, count=12, timeout=0.150)

            if len(raw) >= 12:
                response = raw[3:12]
                data = response[:8]
                cs   = response[8]
                enhanced = lin_checksum_enhanced(ID_INFO, data)

                if cs == enhanced:
                    state = decode_info_frame(data)

                    # Only print when something changes
                    if state != last_state:
                        print(f"[{time.strftime('%H:%M:%S')}] Change detected:")
                        print(f"  zone1={state['zone1_temp']:8s}  setpoint={state['setpoint1']:8s}  gas={state['gas_active']}  elec={state['electric_kw']}kW")
                        print(f"  water={state['water_mode']:6s}  pump={state['pump_running']}  error={state['error']}  panel={'on' if state['panel_on'] else 'off'}")
                        print(f"  outdoor={state['outdoor_temp']}")
                        print()
                        last_state = state

            time.sleep(0.300)

    except KeyboardInterrupt:
        print(f"\nStopped after {cycle} cycles.")
    finally:
        ser.close()

if __name__ == '__main__':
    main()
