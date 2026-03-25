#!/usr/bin/env python3
"""
Alde 3030 Plus - Yellow CI-Bus Read/Modify/Restore Test v1
===========================================================
1. Read current panel state
2. Wait 3 seconds
3. Increase setpoint by 1 degree, confirm change
4. Wait 3 seconds
5. Restore original setpoint, confirm restored
6. Print summary and exit
"""

import serial
import time

PORT = '/dev/ttyAMA0'
BAUD = 19200

ID_CONTROL = 0x1A
ID_INFO    = 0x5B
ID_DIAG    = 0x3C
ID_DIAG_R  = 0x7D

DIAG_PAYLOAD = bytes([0x10, 0x06, 0xB2, 0x00, 0xDE, 0x41, 0x03, 0x00])

def lin_checksum_classic(data):
    total = sum(data)
    while total > 0xFF:
        total = (total & 0xFF) + (total >> 8)
    return (~total) & 0xFF

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

def send_frame_with_data(ser, frame_id, data):
    send_break(ser)
    ser.write(bytes([0x55, frame_id]))
    checksum = lin_checksum_enhanced(frame_id, data)
    ser.write(data + bytes([checksum]))
    ser.flush()

def send_header_only(ser, frame_id):
    send_break(ser)
    ser.write(bytes([0x55, frame_id]))
    ser.flush()

def flush_bytes(ser, n_bytes, extra_ms=5):
    wait = (n_bytes * (1.0 / BAUD) * 10) + extra_ms / 1000
    deadline = time.time() + wait
    flushed = bytearray()
    while time.time() < deadline:
        b = ser.read(1)
        if b:
            flushed.extend(b)
    return bytes(flushed)

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
    if b == 0xFE: return "zone unused"
    if b == 0xFF: return "invalid"
    return f"{b * 0.5 - 42:.1f}°C"

def read_state(ser):
    """Send 0x5B header, read and decode 0x1B response"""
    send_header_only(ser, ID_INFO)
    raw = read_bytes(ser, count=12, timeout=0.150)
    if len(raw) >= 12:
        response = raw[3:12]
        data = response[:8]
        cs = response[8]
        if cs == lin_checksum_enhanced(ID_INFO, data):
            b3 = data[3]; b4 = data[4]; b5 = data[5]
            wm = {0:'off', 1:'normal', 2:'boost', 3:'auto'}
            return {
                'zone1_temp':   data[0] * 0.5 - 42,
                'outdoor_temp': data[2] * 0.5 - 42,
                'setpoint1':    (b3 & 0x3F) * 0.5 + 5,
                'setpoint1_raw': b3 & 0x3F,
                'gas_active':   (b3 >> 6) & 1,
                'valve_open':   (b3 >> 7) & 1,
                'setpoint2_raw': b4 & 0x3F,
                'electric_kw':  (b4 >> 6) & 3,
                'panel_on':     (b5 >> 0) & 1,
                'water_mode':   wm.get((b5 >> 3) & 3, '?'),
                'pump_running': (b5 >> 7) & 1,
                'raw_b3': b3,
                'raw_b4': b4,
                'raw_b5': b5,
            }
    return None

def build_control(state, new_setpoint=None):
    """Build 0x1A payload from current state, optionally overriding setpoint"""
    sp1 = new_setpoint if new_setpoint is not None else state['setpoint1']
    sp1_raw = int((sp1 - 5) / 0.5)
    b3 = sp1_raw | (state['gas_active'] << 6) | (state['valve_open'] << 7)
    b4 = state['raw_b4']  # keep zone2 and electric unchanged
    b5 = state['raw_b5']  # keep all system flags unchanged
    return bytes([0x00, 0x00, 0x00, b3, b4, b5, 0xFF, 0xFF])

def send_control(ser, payload):
    """Send 0x1A control frame and flush echo"""
    send_frame_with_data(ser, ID_CONTROL, payload)
    flush_bytes(ser, n_bytes=12, extra_ms=5)

def wait_for_change(ser, expected_sp, timeout=5.0):
    """Poll until setpoint matches expected value or timeout"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = read_state(ser)
        if state and abs(state['setpoint1'] - expected_sp) < 0.1:
            return state
        time.sleep(0.3)
    return None

def main():
    print("Alde Yellow CI-Bus - Read/Modify/Restore Test v1")
    print("="*55)
    print()

    ser = serial.Serial(PORT, BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.005)

    # Registration
    send_frame_with_data(ser, ID_DIAG, DIAG_PAYLOAD)
    flush_bytes(ser, n_bytes=12, extra_ms=5)
    time.sleep(0.050)

    # Step 1: Read current state
    print("Step 1: Reading current panel state...")
    state = None
    for _ in range(5):
        state = read_state(ser)
        if state:
            break
        time.sleep(0.3)

    if not state:
        print("  ERROR: Could not read panel state")
        ser.close()
        return

    original_sp = state['setpoint1']
    new_sp = round(original_sp + 1.0, 1)

    print(f"  zone1={state['zone1_temp']:.1f}°C  setpoint={original_sp:.1f}°C  "
          f"gas={state['gas_active']}  elec={state['electric_kw']}kW  "
          f"pump={state['pump_running']}")
    print(f"  outdoor={state['outdoor_temp']:.1f}°C  water={state['water_mode']}")
    print()

    # Step 2: Wait 3 seconds
    print("Step 2: Waiting 3 seconds...")
    time.sleep(3)
    print()

    # Step 3: Increase setpoint by 1 degree
    print(f"Step 3: Sending setpoint {original_sp:.1f}°C → {new_sp:.1f}°C...")
    payload_new = build_control(state, new_setpoint=new_sp)
    send_control(ser, payload_new)
    print(f"  Sent: {' '.join(f'{b:02X}' for b in payload_new)}")

    confirmed = wait_for_change(ser, new_sp, timeout=5.0)
    if confirmed:
        print(f"  ✓ Confirmed: setpoint={confirmed['setpoint1']:.1f}°C")
    else:
        print(f"  ✗ Setpoint did not change within 5 seconds")
    print()

    # Step 4: Wait 3 seconds
    print("Step 4: Waiting 3 seconds...")
    time.sleep(3)
    print()

    # Step 5: Restore original setpoint
    print(f"Step 5: Restoring setpoint {new_sp:.1f}°C → {original_sp:.1f}°C...")
    payload_orig = build_control(state, new_setpoint=original_sp)
    send_control(ser, payload_orig)
    print(f"  Sent: {' '.join(f'{b:02X}' for b in payload_orig)}")

    restored = wait_for_change(ser, original_sp, timeout=5.0)
    if restored:
        print(f"  ✓ Confirmed: setpoint={restored['setpoint1']:.1f}°C")
    else:
        print(f"  ✗ Setpoint did not restore within 5 seconds")
    print()

    # Summary
    print("="*55)
    print("SUMMARY:")
    print(f"  Original setpoint : {original_sp:.1f}°C")
    print(f"  Modified setpoint : {new_sp:.1f}°C  {'✓' if confirmed else '✗'}")
    print(f"  Restored setpoint : {original_sp:.1f}°C  {'✓' if restored else '✗'}")
    if confirmed and restored:
        print("  RESULT: PASS - full read/modify/restore cycle working")
    else:
        print("  RESULT: FAIL - see above")

    ser.close()

if __name__ == '__main__':
    main()
