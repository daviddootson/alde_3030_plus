# CLAUDE.md — Alde 3030 Plus Project Context

This file gives you full context for working on this project. Read it completely before doing anything.

---

## What This Project Is

A Raspberry Pi Zero 2W reverse-engineering project that bridges an Alde Compact 3030 Plus caravan heater to Home Assistant via MQTT. The heater has two separate LIN buses. Both have been reverse engineered and are monitored/controlled by two Python scripts running as systemd services.

The Pi lives in the caravan. The owner (David) works on it remotely from a Windows PC ("fun") at `C:\Users\david\`.

---

## Hardware

- **Device:** Alde Compact 3030 Plus caravan heater
- **Pi:** Raspberry Pi Zero 2W
- **Pi hostname:** `alde.local`
- **Pi IP:** `192.168.12.10`
- **Pi user:** `alde`
- **SSH key:** `C:\Users\david\.ssh\id_ed25519` (ed25519)
- **Two TJA1020 LIN transceivers** wired to the Pi

### Wiring
| Signal | Pi Physical Pin | GPIO |
|--------|----------------|------|
| Yellow bus TX/RX | Pin 8 / Pin 10 | GPIO14 / GPIO15 |
| Yellow bus GND | Pin 9 | — |
| Yellow bus 3.3V (SLP) | Pin 17 | — |
| Red bus data | Pin 11 | GPIO17 |
| Red bus GND | Pin 9 | — |
| Red bus 3.3V (SLP) | Pin 17 | — |

---

## The Two Buses

### Yellow Bus (CI-bus) — `alde_mqtt.py`
- **Interface:** Hardware UART `/dev/ttyAMA0`
- **Baud:** 19200
- **Direction:** Bidirectional — read state AND send control commands
- **Frame IDs:** ID_CONTROL=0x1A, ID_INFO=0x5B (wire: 0x1B), ID_DIAG=0x3C
- **Checksum:** Enhanced LIN (frame ID included)
- **Script version:** v8
- **Service:** `alde.service` — `Restart=on-failure`, `RestartSec=10`
- **What it publishes:** Zone 1 temp, setpoint, gas on/off, electric power level, water mode, pump state, outdoor temp, AC input state, error code, valve open, panel on/busy

### Red Bus (internal) — `alde_red_bus_mqtt.py`
- **Interface:** pigpio bit-bang on GPIO17
- **Baud:** 9600
- **Direction:** Passive listen only — never transmits
- **Frame:** 0x55 (protected ID, raw 0x15), 9 data bytes + checksum
- **Temperatures:**
  - `glycol_C = (data[0] + data[1]*256) / 10.0` (valid: 0–120°C)
  - `hot_water_C = (data[2] + data[3]*256) / 10.0` (valid: 0–90°C)
- **State machine:** IDLE → GOT_BREAK (on 0x00) → GOT_SYNC (on 0x55) → DATA (accumulate 9 bytes); gap >10ms resets to IDLE
- **Service:** `alde_red_bus_mqtt.service` — `Restart=always`, `RestartSec=10`, `StartLimitIntervalSec=0`
- **Requires:** pigpiod running (script calls `subprocess.run(['sudo','pigpiod'])` as fallback)

---

## MQTT

- **Broker:** `homeassistant.local:1883`
- **User:** `round`
- **Both scripts share:** `MQTT_CLIENT = 'alde_3030_plus'` and identical `DEVICE` block with `identifiers: ["alde_3030_plus"]`
- **This causes both scripts to appear as a single "Alde 3030 Plus" device in Home Assistant** — intentional design

### Key topics
- `alde/available` — availability
- `alde/climate/state` — yellow bus climate state
- `alde/red_bus/glycol_temp` — red bus glycol temperature
- `alde/red_bus/hot_water_temp` — red bus hot water temperature

---

## Repository Structure

```
alde_3030_plus/
├── alde_mqtt.py              # Yellow bus script (v8)
├── alde_red_bus_mqtt.py      # Red bus script
├── alde.service              # Yellow bus systemd service
├── alde_red_bus_mqtt.service # Red bus systemd service
├── alde_dashboard.yaml       # Home Assistant dashboard
├── README.md                 # Full setup and wiring docs
├── PROTOCOL.md               # Full protocol decode for both buses
└── tools/                    # Development/debug tools
```

GitHub: `https://github.com/daviddootson/alde_3030_plus`

---

## Current Fault (as of 2026-11-13)

**Red bus is working. Yellow bus entities are unavailable in Home Assistant.**

### What is known:
- `sudo systemctl status alde.service` shows the service **active and running** since 17:53 today
- Only log entry is a harmless DeprecationWarning: `paho-mqtt Callback API version 1 is deprecated`
- Service has been up 5+ hours without crashing
- Red bus script on same Pi is publishing correctly to HA

### Likely causes to investigate:
1. **UART reassigned to Bluetooth** — a kernel/OS update may have broken the `/dev/ttyAMA0` assignment. Check `ls -la /dev/serial*` and `cat /boot/firmware/config.txt | grep uart`
2. **MQTT not publishing** — script running but silently failing to send. Check with `mosquitto_sub -h homeassistant.local -t "alde/#" -u round -P "$MQTT_PASS"`
3. **LIN bus not responding** — heater not sending frames, script stuck waiting. Check full logs with `sudo journalctl -u alde.service --since "17:53:00" --no-pager`
4. **paho-mqtt version incompatibility** — the deprecation warning may indicate a breaking API change in a recent paho-mqtt update

### Recommended first steps:
```bash
# Full logs since service start
sudo journalctl -u alde.service --since "17:53:00" --no-pager

# Check UART device
ls -la /dev/serial* /dev/ttyAMA*

# Check what's on the MQTT bus
mosquitto_sub -h homeassistant.local -t "alde/#" -u round -P "$MQTT_PASS" -v &
sleep 10
kill %1

# Check paho-mqtt version
pip3 show paho-mqtt
```

---

## Pi Setup Notes

- **OS:** Debian 13 (Trixie)
- **pigpio:** Built from source (not in apt repos for Trixie)
- **Python packages:** `paho-mqtt`, `pigpio`, `pyserial`
- **Scripts location:** `/home/alde/`
- **Service files location:** `/etc/systemd/system/`
- The `alde` user needs to be in the `dialout` group for UART access

## Known Issues / Future Work

- `alde.service` uses `Restart=on-failure` — should be changed to `Restart=always` with `StartLimitIntervalSec=0` to match red bus service
- Future: EcoFlow power monitoring integration
- Future: Temperature-based automations combining red bus hot water temp with EcoFlow power data
