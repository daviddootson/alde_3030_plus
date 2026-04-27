#!/usr/bin/env python3
"""
Alde 3030 Plus - Red Bus MQTT Bridge
======================================
Passively listens on the red internal LIN bus (9600 baud, GPIO17)
via pigpio bit-bang and publishes glycol and hot water temperatures
to MQTT / Home Assistant via auto-discovery.

Frame decoded: 0x55 (protected ID, raw 0x15), ENHANCED checksum
  glycol_C    = (data[0] + data[1]*256) / 10.0
  hot_water_C = (data[2] + data[3]*256) / 10.0

MQTT topics:
  alde/red/available         'online' / 'offline' (LWT + birth)
  alde/red/glycol_temp       float °C  (retained)
  alde/red/hot_water_temp    float °C  (retained)

HA discovery topics (retained):
  homeassistant/sensor/alde_red/alde_glycol/config
  homeassistant/sensor/alde_red/alde_hot_water/config

Robustness (mirrors alde_mqtt.py):
  - will_set before connect so broker publishes 'offline' on drop
  - on_connect republishes discovery + last known values + 'online'
  - retain=True on every publish so HA repopulates after restart
  - loop_start() handles reconnect transparently in background
  - pigpiod started automatically if not running
  - Designed to run under systemd (see alde_red_bus_mqtt.service)
"""

import pigpio
import time
import json
import threading
import subprocess
import paho.mqtt.client as mqtt

# ── GPIO / LIN ────────────────────────────────────────────────────────────────
GPIO_PIN = 17
BAUD     = 9600

# ── MQTT ──────────────────────────────────────────────────────────────────────
MQTT_HOST   = 'homeassistant.local'
MQTT_PORT   = 1883
MQTT_USER   = 'round'
MQTT_PASS   = 'Ilovetabsha1!'
MQTT_CLIENT = 'alde_red_bus_mqtt'

# ── Device (shared with yellow bus so sensors group together in HA) ────────────
DEVICE = {
    "identifiers":  ["alde_3030_plus"],
    "name":         "Alde 3030 Plus",
    "model":        "Compact 3030 Plus",
    "manufacturer": "Alde",
    "sw_version":   "8.0"
}

# ── Topics ────────────────────────────────────────────────────────────────────
AVAIL_TOPIC  = 'alde/red/available'

GLYCOL_DISC  = 'homeassistant/sensor/alde_red/alde_glycol/config'
GLYCOL_STATE = 'alde/red/glycol_temp'

HW_DISC      = 'homeassistant/sensor/alde_red/alde_hot_water/config'
HW_STATE     = 'alde/red/hot_water_temp'

# ── Shared state ──────────────────────────────────────────────────────────────
last_glycol    = None
last_hot_water = None
state_lock     = threading.Lock()

# ── LIN checksum ──────────────────────────────────────────────────────────────
def chksum_enhanced(pid, data):
    total = pid + sum(data)
    while total > 0xFF:
        total = (total & 0xFF) + (total >> 8)
    return (~total) & 0xFF

# ── MQTT discovery ────────────────────────────────────────────────────────────
def publish_discovery(client):
    client.publish(GLYCOL_DISC, json.dumps({
        "name":                "Glycol Temperature",
        "unique_id":           "alde_red_glycol",
        "object_id":           "alde_glycol",
        "state_topic":         GLYCOL_STATE,
        "device_class":        "temperature",
        "unit_of_measurement": "°C",
        "state_class":         "measurement",
        "availability_topic":  AVAIL_TOPIC,
        "payload_available":   "online",
        "payload_not_available": "offline",
        "device": DEVICE
    }), retain=True)

    client.publish(HW_DISC, json.dumps({
        "name":                "Hot Water Temperature",
        "unique_id":           "alde_red_hot_water",
        "object_id":           "alde_hot_water",
        "state_topic":         HW_STATE,
        "device_class":        "temperature",
        "unit_of_measurement": "°C",
        "state_class":         "measurement",
        "availability_topic":  AVAIL_TOPIC,
        "payload_available":   "online",
        "payload_not_available": "offline",
        "device": DEVICE
    }), retain=True)

    print("[MQTT] Discovery configs published")

# ── Publish temperatures ──────────────────────────────────────────────────────
def publish_state(client, glycol, hot_water):
    client.publish(GLYCOL_STATE, f"{glycol:.1f}",   retain=True)
    client.publish(HW_STATE,     f"{hot_water:.1f}", retain=True)

# ── MQTT callbacks ────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Connected to {MQTT_HOST}")
        publish_discovery(client)
        client.publish(AVAIL_TOPIC, "online", retain=True)
        # Republish last known values immediately so HA entities
        # are live the moment the broker reconnects
        with state_lock:
            g  = last_glycol
            hw = last_hot_water
        if g is not None:
            publish_state(client, g, hw)
            print(f"[MQTT] Republished on connect: glycol={g:.1f} hot_water={hw:.1f}")
    else:
        print(f"[MQTT] Connection failed rc={rc}")

# ── Frame processor ───────────────────────────────────────────────────────────
def process_0x55(data_bytes, chksum, client):
    global last_glycol, last_hot_water

    if chksum_enhanced(0x55, data_bytes) != chksum:
        return  # checksum fail — discard silently

    glycol    = (data_bytes[0] + data_bytes[1] * 256) / 10.0
    hot_water = (data_bytes[2] + data_bytes[3] * 256) / 10.0

    # Sanity range — discard obviously corrupt frames
    if not (0.0 <= glycol <= 120.0) or not (0.0 <= hot_water <= 90.0):
        print(f"[WARN] Out of range: glycol={glycol} hot_water={hot_water} — discarded")
        return

    with state_lock:
        changed        = (glycol != last_glycol or hot_water != last_hot_water)
        last_glycol    = glycol
        last_hot_water = hot_water

    if changed:
        print(f"[TEMP] glycol={glycol:.1f}°C  hot_water={hot_water:.1f}°C")
        if client.is_connected():
            publish_state(client, glycol, hot_water)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Alde 3030 Plus - Red Bus MQTT Bridge")
    print("=" * 50)

    # ── Start pigpiod ─────────────────────────────────────────────────────────
    subprocess.run(['sudo', 'pigpiod'], capture_output=True)
    time.sleep(1)

    pi = pigpio.pi()
    if not pi.connected:
        print("ERROR: pigpio not connected — is pigpiod running?")
        return

    pi.bb_serial_read_open(GPIO_PIN, BAUD, 8)
    print(f"[GPIO] bit-bang open on GPIO{GPIO_PIN} @ {BAUD} baud")

    # ── MQTT ──────────────────────────────────────────────────────────────────
    client = mqtt.Client(client_id=MQTT_CLIENT)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.will_set(AVAIL_TOPIC, "offline", retain=True)
    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    print("Waiting for MQTT connection...")
    for _ in range(20):
        if client.is_connected():
            break
        time.sleep(0.5)

    if not client.is_connected():
        print("ERROR: Could not connect to MQTT broker")
        pi.bb_serial_read_close(GPIO_PIN)
        pi.stop()
        return

    print("Listening on red bus...")
    print()

    # ── LIN frame state machine ───────────────────────────────────────────────
    buf         = []
    last_t      = time.time()
    sm_state    = 'IDLE'
    current_pid = None

    try:
        while True:
            count, data = pi.bb_serial_read(GPIO_PIN)
            now = time.time()

            if count and data:
                for b in data:
                    gap    = now - last_t
                    last_t = now

                    # Gap > 10ms = frame boundary — reset state machine
                    if gap > 0.010 and sm_state == 'DATA':
                        buf         = []
                        current_pid = None
                        sm_state    = 'IDLE'

                    if sm_state == 'IDLE':
                        if b == 0x00:
                            sm_state = 'GOT_BREAK'

                    elif sm_state == 'GOT_BREAK':
                        if b == 0x55:
                            sm_state = 'GOT_SYNC'
                        elif b == 0x00:
                            pass  # multiple break bytes, stay
                        else:
                            sm_state = 'IDLE'

                    elif sm_state == 'GOT_SYNC':
                        current_pid = b
                        buf         = []
                        sm_state    = 'DATA'

                    elif sm_state == 'DATA':
                        buf.append(b)
                        if len(buf) == 9:
                            if current_pid == 0x55:
                                process_0x55(buf[:8], buf[8], client)
                            buf         = []
                            current_pid = None
                            sm_state    = 'IDLE'

            time.sleep(0.005)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.publish(AVAIL_TOPIC, "offline", retain=True)
        client.loop_stop()
        client.disconnect()
        pi.bb_serial_read_close(GPIO_PIN)
        pi.stop()

if __name__ == '__main__':
    main()
