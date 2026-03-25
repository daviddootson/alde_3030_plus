#!/usr/bin/env python3
"""
Alde 3030 Plus - MQTT Bridge v8
================================
Changes from v7:
- Gas heating changed from binary_sensor to switch
- Water mode 'normal' renamed to 'on', auto removed
- Added new sensors: valve_open, panel_on, panel_busy, ac_input
- All fields now exposed as entities
"""

import serial
import time
import json
import threading
import paho.mqtt.client as mqtt

# ── Serial ───────────────────────────────────────────────────────────────────
PORT = '/dev/ttyAMA0'
BAUD = 19200

# ── MQTT ─────────────────────────────────────────────────────────────────────
MQTT_HOST   = 'homeassistant.local'
MQTT_PORT   = 1883
MQTT_USER   = 'round'
MQTT_PASS   = 'Ilovetabsha1!'
MQTT_CLIENT = 'alde_3030_plus'

# ── Device ───────────────────────────────────────────────────────────────────
DEVICE = {
    "identifiers": ["alde_3030_plus"],
    "name":        "Alde 3030 Plus",
    "model":       "Compact 3030 Plus",
    "manufacturer":"Alde",
    "sw_version":  "8.0"
}

# ── Topics ───────────────────────────────────────────────────────────────────
AVAIL_TOPIC  = 'alde/available'

# Climate
CLIMATE_DISC  = 'homeassistant/climate/alde_3030/alde_3030/config'
CLIMATE_STATE = 'alde/climate/state'
CLIMATE_ATTR  = 'alde/climate/attributes'
CMD_TEMP      = 'alde/climate/cmd/temperature'
CMD_MODE      = 'alde/climate/cmd/mode'

# Electric power select
ELEC_DISC     = 'homeassistant/select/alde_3030/alde_electric/config'
ELEC_STATE    = 'alde/electric/state'
CMD_ELEC      = 'alde/electric/cmd'

# Water mode select
WATER_DISC    = 'homeassistant/select/alde_3030/alde_water/config'
WATER_STATE   = 'alde/water/state'
CMD_WATER     = 'alde/water/cmd'

# Gas switch
GAS_DISC      = 'homeassistant/switch/alde_3030/alde_gas/config'
GAS_STATE     = 'alde/gas/state'
CMD_GAS       = 'alde/gas/cmd'

# Sensors
OUTDOOR_DISC  = 'homeassistant/sensor/alde_3030/alde_outdoor/config'
OUTDOOR_STATE = 'alde/outdoor/state'

# Binary sensors (read only)
PUMP_DISC     = 'homeassistant/binary_sensor/alde_3030/alde_pump/config'
PUMP_STATE    = 'alde/pump/state'

VALVE_DISC    = 'homeassistant/binary_sensor/alde_3030/alde_valve/config'
VALVE_STATE   = 'alde/valve/state'

AC_DISC       = 'homeassistant/binary_sensor/alde_3030/alde_ac/config'
AC_STATE      = 'alde/ac/state'

ERROR_DISC    = 'homeassistant/binary_sensor/alde_3030/alde_error/config'
ERROR_STATE   = 'alde/error/state'

# ── LIN ──────────────────────────────────────────────────────────────────────
ID_CONTROL = 0x1A
ID_INFO    = 0x5B
ID_DIAG    = 0x3C
DIAG_PAYLOAD = bytes([0x10, 0x06, 0xB2, 0x00, 0xDE, 0x41, 0x03, 0x00])

ELEC_OPTIONS  = ['Off', '1kW', '2kW', '3kW']
WATER_OPTIONS = ['off', 'on', 'boost']

# ── Shared state ─────────────────────────────────────────────────────────────
current_state = None
pending_cmd   = None
state_lock    = threading.Lock()
cmd_lock      = threading.Lock()

# ── LIN helpers ──────────────────────────────────────────────────────────────
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
    ser.write(data + bytes([lin_checksum_enhanced(frame_id, data)]))
    ser.flush()

def send_header_only(ser, frame_id):
    send_break(ser)
    ser.write(bytes([0x55, frame_id]))
    ser.flush()

def flush_bytes(ser, n_bytes, extra_ms=5):
    wait = (n_bytes * (1.0 / BAUD) * 10) + extra_ms / 1000
    deadline = time.time() + wait
    buf = bytearray()
    while time.time() < deadline:
        b = ser.read(1)
        if b:
            buf.extend(b)
    return bytes(buf)

def read_bytes(ser, count, timeout=0.150):
    buf = bytearray()
    deadline = time.time() + timeout
    while time.time() < deadline and len(buf) < count:
        b = ser.read(1)
        if b:
            buf.extend(b)
            deadline = time.time() + 0.020
    return bytes(buf)

# ── Panel read/write ──────────────────────────────────────────────────────────
def read_state(ser):
    send_header_only(ser, ID_INFO)
    raw = read_bytes(ser, count=12, timeout=0.150)
    if len(raw) >= 12:
        response = raw[3:12]
        data = response[:8]
        cs   = response[8]
        if cs == lin_checksum_enhanced(ID_INFO, data):
            b3 = data[3]; b4 = data[4]; b5 = data[5]
            wm = {0:'off', 1:'on', 2:'boost', 3:'boost'}
            return {
                'zone1_temp':   round(data[0] * 0.5 - 42, 1),
                'outdoor_temp': round(data[2] * 0.5 - 42, 1),
                'setpoint':     round((b3 & 0x3F) * 0.5 + 5, 1),
                'gas_active':   (b3 >> 6) & 1,
                'valve_open':   (b3 >> 7) & 1,
                'electric_kw':  (b4 >> 6) & 3,
                'water_mode':   wm.get((b5 >> 3) & 3, 'off'),
                'pump_running': (b5 >> 7) & 1,
                'panel_on':     (b5 >> 0) & 1,
                'panel_busy':   (b5 >> 1) & 1,
                'error':        (b5 >> 2) & 1,
                'ac_input':     (b5 >> 5) & 1,
                'raw_b3': b3, 'raw_b4': b4, 'raw_b5': b5,
            }
    return None

def build_payload(state, new_setpoint=None, new_gas=None, new_elec=None, new_water=None):
    sp   = new_setpoint if new_setpoint is not None else state['setpoint']
    gas  = new_gas      if new_gas      is not None else state['gas_active']
    elec = new_elec     if new_elec     is not None else state['electric_kw']

    sp_raw = max(0, min(0x3F, int((sp - 5) / 0.5)))
    b3 = sp_raw | (gas << 6) | (state['valve_open'] << 7)
    b4 = (state['raw_b4'] & 0x3F) | (elec << 6)

    b5 = state['raw_b5']
    if new_water is not None:
        wm_map = {'off': 0, 'on': 1, 'boost': 2}
        wm_val = wm_map.get(new_water, (b5 >> 3) & 3)
        b5 = (b5 & 0b11100111) | (wm_val << 3)

    return bytes([0x00, 0x00, 0x00, b3, b4, b5, 0xFF, 0xFF])

def send_control(ser, payload):
    send_frame_with_data(ser, ID_CONTROL, payload)
    flush_bytes(ser, n_bytes=12, extra_ms=5)

# ── MQTT Discovery ────────────────────────────────────────────────────────────
def publish_discovery(client):

    # Climate
    client.publish(CLIMATE_DISC, json.dumps({
        "unique_id": "alde_3030_climate",
        "object_id": "alde_3030",
        "current_temperature_topic":    CLIMATE_STATE,
        "current_temperature_template": "{{ value_json.current_temperature }}",
        "temperature_command_topic":    CMD_TEMP,
        "temperature_state_topic":      CLIMATE_STATE,
        "temperature_state_template":   "{{ value_json.temperature }}",
        "mode_command_topic":           CMD_MODE,
        "mode_state_topic":             CLIMATE_STATE,
        "mode_state_template":          "{{ value_json.mode }}",
        "modes": ["off", "heat"],
        "min_temp": 5, "max_temp": 30, "temp_step": 0.5, "precision": 0.5,
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online", "payload_not_available": "offline",
        "json_attributes_topic": CLIMATE_ATTR,
        "optimistic": False,
        "device": DEVICE
    }), retain=True)

    # Electric power select
    client.publish(ELEC_DISC, json.dumps({
        "name": "Electric Power",
        "unique_id": "alde_3030_electric",
        "object_id": "alde_electric",
        "state_topic": ELEC_STATE, "command_topic": CMD_ELEC,
        "options": ELEC_OPTIONS,
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online", "payload_not_available": "offline",
        "device": DEVICE
    }), retain=True)

    # Water mode select
    client.publish(WATER_DISC, json.dumps({
        "name": "Water Mode",
        "unique_id": "alde_3030_water",
        "object_id": "alde_water",
        "state_topic": WATER_STATE, "command_topic": CMD_WATER,
        "options": WATER_OPTIONS,
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online", "payload_not_available": "offline",
        "device": DEVICE
    }), retain=True)

    # Gas switch
    client.publish(GAS_DISC, json.dumps({
        "name": "Gas",
        "unique_id": "alde_3030_gas",
        "object_id": "alde_gas",
        "state_topic":   GAS_STATE,
        "command_topic": CMD_GAS,
        "payload_on":  "ON", "payload_off": "OFF",
        "state_on":    "ON", "state_off":   "OFF",
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online", "payload_not_available": "offline",
        "device": DEVICE
    }), retain=True)

    # Outdoor temperature sensor - listed after AC Input
    client.publish(OUTDOOR_DISC, json.dumps({
        "name": "Outdoor Temperature",
        "unique_id": "alde_3030_outdoor",
        "object_id": "alde_outdoor",
        "state_topic": OUTDOOR_STATE,
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online", "payload_not_available": "offline",
        "device": DEVICE
    }), retain=True)

    # Binary sensors
    for disc, state_t, name, uid, obj, dc in [
        (PUMP_DISC,  PUMP_STATE,  "Circulation Pump", "alde_3030_pump",  "alde_pump",  "running"),
        (VALVE_DISC, VALVE_STATE, "Gas Valve",        "alde_3030_valve", "alde_valve", "opening"),
        (AC_DISC,    AC_STATE,    "AC Input",         "alde_3030_ac",    "alde_ac",    "power"),
        (ERROR_DISC, ERROR_STATE, "Error",            "alde_3030_error", "alde_error", "problem"),
    ]:
        client.publish(disc, json.dumps({
            "name": name,
            "unique_id": uid,
            "object_id": obj,
            "state_topic": state_t,
            "payload_on": "ON", "payload_off": "OFF",
            "device_class": dc,
            "availability_topic": AVAIL_TOPIC,
            "payload_available": "online", "payload_not_available": "offline",
            "device": DEVICE
        }), retain=True)

    print("[MQTT] All discovery configs published")

# ── Publish state ─────────────────────────────────────────────────────────────
def publish_state(client, state):
    mode = 'heat' if (state['gas_active'] or state['electric_kw'] > 0) else 'off'

    client.publish(CLIMATE_STATE, json.dumps({
        'current_temperature': state['zone1_temp'],
        'temperature':         state['setpoint'],
        'mode':                mode,
    }), retain=True)
    client.publish(CLIMATE_ATTR, json.dumps({
        'outdoor_temperature': state['outdoor_temp'],
        'gas_active':          bool(state['gas_active']),
        'valve_open':          bool(state['valve_open']),
        'electric_kw':         state['electric_kw'],
        'water_mode':          state['water_mode'],
        'pump_running':        bool(state['pump_running']),
        'error':               bool(state['error']),
    }), retain=True)

    client.publish(ELEC_STATE,    ELEC_OPTIONS[state['electric_kw']],      retain=True)
    client.publish(WATER_STATE,   state['water_mode'],                     retain=True)
    client.publish(GAS_STATE,     'ON' if state['gas_active'] else 'OFF',  retain=True)
    client.publish(OUTDOOR_STATE, state['outdoor_temp'],                   retain=True)
    client.publish(PUMP_STATE,    'ON' if state['pump_running'] else 'OFF',retain=True)
    client.publish(VALVE_STATE,   'ON' if state['valve_open'] else 'OFF',  retain=True)
    client.publish(AC_STATE,      'ON' if state['ac_input'] else 'OFF',    retain=True)
    client.publish(ERROR_STATE,   'ON' if state['error'] else 'OFF',       retain=True)

# ── MQTT callbacks ────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Connected to {MQTT_HOST}")
        publish_discovery(client)
        client.subscribe(CMD_TEMP)
        client.subscribe(CMD_MODE)
        client.subscribe(CMD_ELEC)
        client.subscribe(CMD_WATER)
        client.subscribe(CMD_GAS)
        client.publish(AVAIL_TOPIC, "online", retain=True)
    else:
        print(f"[MQTT] Connection failed rc={rc}")

def on_message(client, userdata, msg):
    global pending_cmd
    topic   = msg.topic
    payload = msg.payload.decode().strip()
    print(f"[MQTT] CMD {topic}: {payload}")
    with cmd_lock:
        if pending_cmd is None:
            pending_cmd = {}
        if topic == CMD_TEMP:
            try:    pending_cmd['setpoint'] = float(payload)
            except: print(f"[MQTT] Invalid temperature: {payload}")
        elif topic == CMD_MODE:
            if payload == 'heat': pending_cmd['gas'] = 1
            elif payload == 'off': pending_cmd['gas'] = 0
        elif topic == CMD_ELEC:
            elec_map = {'Off': 0, '1kW': 1, '2kW': 2, '3kW': 3}
            if payload in elec_map:
                pending_cmd['elec'] = elec_map[payload]
        elif topic == CMD_WATER:
            if payload in WATER_OPTIONS:
                pending_cmd['water'] = payload
        elif topic == CMD_GAS:
            if payload == 'ON':  pending_cmd['gas'] = 1
            elif payload == 'OFF': pending_cmd['gas'] = 0

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global current_state, pending_cmd

    print("Alde 3030 Plus - MQTT Bridge")
    print("="*50)

    ser = serial.Serial(PORT, BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.005)

    send_frame_with_data(ser, ID_DIAG, DIAG_PAYLOAD)
    flush_bytes(ser, n_bytes=12, extra_ms=5)
    time.sleep(0.050)

    client = mqtt.Client(client_id=MQTT_CLIENT)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.will_set(AVAIL_TOPIC, "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    print("Waiting for MQTT connection...")
    for _ in range(20):
        if client.is_connected():
            break
        time.sleep(0.5)

    if not client.is_connected():
        print("ERROR: Could not connect to MQTT broker")
        ser.close()
        return

    print("Reading initial panel state...")
    initial_state = None
    for attempt in range(10):
        initial_state = read_state(ser)
        if initial_state:
            publish_state(client, initial_state)
            print(f"  zone1={initial_state['zone1_temp']}°C  "
                  f"sp={initial_state['setpoint']}°C  "
                  f"gas={initial_state['gas_active']}  "
                  f"elec={initial_state['electric_kw']}kW  "
                  f"water={initial_state['water_mode']}  "
                  f"pump={initial_state['pump_running']}  "
                  f"outdoor={initial_state['outdoor_temp']}°C  "
                  f"ac={initial_state['ac_input']}  "
                  f"err={initial_state['error']}")
            break
        print(f"  Attempt {attempt+1} failed, retrying...")
        time.sleep(0.5)

    if not initial_state:
        print("WARNING: Could not read initial state")

    print("Running... Press Ctrl+C to stop")
    print()

    last_published = (
        initial_state['zone1_temp'], initial_state['setpoint'],
        initial_state['gas_active'], initial_state['electric_kw'],
        initial_state['pump_running'], initial_state['water_mode'],
        initial_state['outdoor_temp'], initial_state['error'],
        initial_state['valve_open'], initial_state['ac_input'],
    ) if initial_state else None

    try:
        while True:
            with cmd_lock:
                cmd = pending_cmd
                pending_cmd = None

            if cmd:
                state = read_state(ser)
                if state:
                    payload = build_payload(state,
                        new_setpoint = cmd.get('setpoint'),
                        new_gas      = cmd.get('gas'),
                        new_elec     = cmd.get('elec'),
                        new_water    = cmd.get('water'))
                    print(f"[CMD] {cmd}")
                    send_control(ser, payload)

                    # Poll quickly after command to catch panel response fast
                    for _ in range(5):
                        time.sleep(0.3)
                        state = read_state(ser)
                        if state:
                            with state_lock:
                                current_state = state
                            key = (
                                state['zone1_temp'], state['setpoint'],
                                state['gas_active'], state['electric_kw'],
                                state['pump_running'], state['water_mode'],
                                state['outdoor_temp'], state['error'],
                                state['valve_open'], state['ac_input'],
                            )
                            if key != last_published:
                                publish_state(client, state)
                                last_published = key
                                print(f"[CMD CONFIRM] sp={state['setpoint']}°C  "
                                      f"gas={state['gas_active']}  "
                                      f"elec={state['electric_kw']}kW  "
                                      f"water={state['water_mode']}")
                                break

            state = read_state(ser)
            if state:
                with state_lock:
                    current_state = state

                key = (
                    state['zone1_temp'], state['setpoint'],
                    state['gas_active'], state['electric_kw'],
                    state['pump_running'], state['water_mode'],
                    state['outdoor_temp'], state['error'],
                    state['valve_open'], state['ac_input'],
                )

                if key != last_published:
                    publish_state(client, state)
                    last_published = key
                    print(f"[STATE] zone1={state['zone1_temp']}°C  "
                          f"sp={state['setpoint']}°C  "
                          f"gas={state['gas_active']}  "
                          f"elec={state['electric_kw']}kW  "
                          f"water={state['water_mode']}  "
                          f"pump={state['pump_running']}  "
                          f"valve={state['valve_open']}  "
                          f"ac={state['ac_input']}  "
                          f"err={state['error']}")

            time.sleep(0.5)  # reduced from 2.0 for faster response

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.publish(AVAIL_TOPIC, "offline", retain=True)
        client.loop_stop()
        client.disconnect()
        ser.close()

if __name__ == '__main__':
    main()
