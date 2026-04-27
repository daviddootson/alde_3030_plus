# Alde 3030 Plus - Home Assistant Integration

Control and monitor your Alde Compact 3030 Plus caravan heating system from Home Assistant via MQTT, using a Raspberry Pi Zero 2 connected to the panel's CI-bus and internal red bus.

![Home Assistant Dashboard](dashboard_screenshot.png)

## Features

- 🌡️ Real-time indoor and outdoor temperature monitoring
- 🎯 Setpoint control from Home Assistant
- 🔥 Gas heating on/off control
- ⚡ Electric power control (Off / 1kW / 2kW / 3kW)
- 💧 Water heating mode control (Off / On / Boost)
- 🌊 Glycol and hot water temperature from the internal red bus
- 📊 Circulation pump, gas valve, AC input and error status
- 🔄 Full bidirectional — panel and HA stay in sync
- 💾 Survives reboots of either the Pi or Home Assistant
- 🚀 Sub-second response to commands

---

## Architecture Overview

This integration uses **two independent scripts**, each connecting to a different physical bus on the Alde panel:

| Script | Bus | Interface | Role |
|--------|-----|-----------|------|
| `alde_mqtt.py` | Yellow CI-bus | `/dev/ttyAMA0` (hardware UART) | Control + status (bidirectional) |
| `alde_red_bus_mqtt.py` | Red internal bus | GPIO17 (bit-bang UART via pigpio) | Glycol + hot water temperatures (read-only) |

Both scripts run as independent systemd services and communicate via the shared Mosquitto MQTT broker. **Both scripts advertise themselves to Home Assistant using the same device identifier (`alde_3030_plus`), so all entities — from both buses — appear as a single unified "Alde 3030 Plus" device in HA.** There is no visible seam between the two data sources.

This architecture provides clean fault isolation: if one script crashes or is restarted, the other continues running unaffected.

---

## Hardware Required

| Component | Notes |
|-----------|-------|
| Raspberry Pi Zero 2 W | Any Pi with hardware UART will work |
| TJA1020 LIN transceiver | LIN/UART transceiver board — for yellow CI-bus |
| TJA1020 LIN transceiver (2nd) | Second board for red bus passive listen |
| RJ12 6P6C cable | To connect to the Alde yellow connector |
| Jumper wires | To connect Pi GPIO to TJA1020 boards |

### Alde Yellow Connector Pinout

The Alde 3030 Plus panel has two RJ12 connectors. We connect to the **yellow** one (the external CI-bus) for primary control:

```
RJ12 Yellow Connector (looking into socket)
┌─────────────────┐
│ 6  5  4  3  2  1│
└─────────────────┘
        │
        └───── Pin 4: LIN bus signal (10.5V idle) ← connect here
```

> ⚠️ All other pins are unused. Do not connect anything to them.

### Alde Red Connector Pinout

The red RJ12 connector exposes the internal LIN bus. We connect passively — listen only, never transmit:

```
RJ12 Red Connector (looking into socket)
┌─────────────────┐
│ 6  5  4  3  2  1│
└─────────────────┘
     │   │
     │   └── Pin 4: LIN bus signal ← connect to 2nd TJA1020
     └─────── Pin 5: 12V supply
Pin 2: GND
```

### Wiring Diagram

The TJA1020 has connections on two sides — the UART side connects to the Pi, and the LIN bus side connects to the Alde panel.

```
Raspberry Pi Zero 2          TJA1020 #1 (Yellow bus)    Alde Connectors
───────────────────          ───────────────────────    ───────────────
Pin 1  (3.3V)  ────────────► SLP
Pin 8  (TXD)   ────────────► RX
Pin 10 (RXD)   ◄──────────── TX
Pin 9  (GND)   ────────────► GND
                             LIN ◄──────────────────── Yellow connector Pin 4

                TJA1020 #2 (Red bus)
                ────────────────────
Pin 1  (3.3V)  ────────────► SLP
Pin 17 (GPIO17)◄──────────── TX                        (listen only — RX not connected)
Pin 9  (GND)   ────────────► GND
                             LIN ◄──────────────────── Red connector Pin 4

Buck Converter
──────────────
Input 12V  ◄── Red connector Pin 5 (12V)
Input GND  ◄── Red connector Pin 2 (GND)
Output 5V  ──► Pi Zero 2 micro USB (power)
```

**Key points:**
- **SLP pin held high** (3.3V) keeps the TJA1020 active — never pull this low or the transceiver will sleep
- **Common ground** — Pi GND and red bus GND share a common reference via the buck converter. This is essential for reliable UART communication
- **Red bus is passive** — the second TJA1020's TX pin feeds GPIO17 for bit-bang receive only. The Pi never transmits on the red bus
- **Pi is powered** from a 12V→5V buck converter connected to the red bus, via micro USB. No separate power supply needed

---

## Protocol Details

### Yellow CI-Bus

| Parameter | Value |
|-----------|-------|
| Baud rate | 19200 bps |
| Format | 8N1 |
| Checksum | Enhanced (includes frame ID) |
| Interface | `/dev/ttyAMA0` (Pi hardware UART) |

| Frame | Raw ID | Wire ID | Direction | Purpose |
|-------|--------|---------|-----------|---------|
| Diagnostic | 0x3C | 0x3C | Pi → Panel | Registration handshake |
| Control | 0x1A | 0x1A | Pi → Panel | Send setpoint and settings |
| Info | 0x1B | 0x5B | Pi → Panel (header) / Panel → Pi (data) | Read panel status |

### Red Internal Bus

| Parameter | Value |
|-----------|-------|
| Baud rate | 9600 bps |
| Format | 8N1 |
| Checksum | Enhanced (includes frame ID) |
| Interface | GPIO17 (pigpio bit-bang) |

| Frame | Protected ID | Purpose |
|-------|-------------|---------|
| Temperature | 0x55 (raw 0x15) | Glycol and hot water temperatures |

For full protocol documentation see [PROTOCOL.md](PROTOCOL.md).

---

## Raspberry Pi Setup

### 1. Operating System

Install Raspberry Pi OS Lite (64-bit) using Raspberry Pi Imager. Enable SSH and set your hostname/credentials in the imager before writing.

### 2. Enable Hardware UART

The Pi Zero 2 has Bluetooth on the main UART by default. We need to free it up:

```bash
sudo nano /boot/firmware/config.txt
```

Add at the bottom:
```ini
# Disable Bluetooth to free up hardware UART
dtoverlay=disable-bt

# Optional: reduce clock speed to save power
# UART timing is unaffected by CPU clock changes
arm_freq=600
over_voltage=-4
hdmi_blanking=2
```

Disable the Bluetooth service:
```bash
sudo systemctl disable hciuart
sudo reboot
```

### 3. Disable Serial Console

The serial port is used as a console by default — we need to disable that:

```bash
sudo raspi-config
```

Go to **Interface Options → Serial Port**:
- "Would you like a login shell to be accessible over the serial port?" → **No**
- "Would you like the serial port hardware to be enabled?" → **Yes**

Reboot after making changes.

### 4. Install Dependencies

```bash
pip install paho-mqtt --break-system-packages
```

For the red bus script, `pigpio` is also required. Build it from source on Debian Trixie (the standard package does not work correctly):

```bash
sudo apt install git
git clone https://github.com/joan2937/pigpio.git
cd pigpio
make
sudo make install
```

Enable the pigpio daemon at boot:

```bash
sudo systemctl enable pigpiod
sudo systemctl start pigpiod
```

> ℹ️ The red bus script also calls `pigpiod` automatically at startup as a fallback, but enabling it as a service ensures it is ready immediately on boot.

### 5. Clone This Repository

```bash
cd /home/alde
git clone https://github.com/YOUR_USERNAME/alde-3030-ha.git .
```

---

## Home Assistant Setup

### Prerequisites

- Home Assistant with MQTT integration configured
- Mosquitto MQTT broker (the HA add-on works perfectly)

### MQTT Configuration

Edit both scripts and update the MQTT settings at the top of each file:

**`alde_mqtt.py`:**
```python
MQTT_HOST = 'homeassistant.local'  # or your HA IP address
MQTT_PORT = 1883
MQTT_USER = 'your_mqtt_username'
MQTT_PASS = 'your_mqtt_password'
```

**`alde_red_bus_mqtt.py`:**
```python
MQTT_HOST = 'homeassistant.local'
MQTT_PORT = 1883
MQTT_USER = 'your_mqtt_username'
MQTT_PASS = 'your_mqtt_password'
```

### Test the Connections

Before setting up the services, test each script runs correctly:

```bash
python3 alde_mqtt.py
```

You should see output like:
```
Alde 3030 Plus - MQTT Bridge
==================================================
Waiting for MQTT connection...
[MQTT] Connected to homeassistant.local
[MQTT] All discovery configs published
Reading initial panel state...
  zone1=21.0°C  sp=20.0°C  gas=1  elec=2kW  water=on  pump=1  outdoor=8.0°C  ac=1  err=0
Running... Press Ctrl+C to stop
```

```bash
python3 alde_red_bus_mqtt.py
```

You should see output like:
```
Alde 3030 Plus - Red Bus MQTT Bridge
==================================================
[GPIO] bit-bang open on GPIO17 @ 9600 baud
[MQTT] Connected to homeassistant.local
[MQTT] Discovery configs published
Listening on red bus...

[TEMP] glycol=45.2°C  hot_water=52.8°C
```

The Alde device should appear automatically in Home Assistant under **Settings → Devices & Services → MQTT**, with all entities — from both buses — grouped under a single "Alde 3030 Plus" device.

---

## Running as Services

Both scripts are designed to run as systemd services, starting automatically on boot and restarting if they ever crash. They run independently — restarting one has no effect on the other.

### Yellow Bus Service (`alde.service`)

Create the service file:

```bash
sudo nano /etc/systemd/system/alde.service
```

```ini
[Unit]
Description=Alde 3030 Plus MQTT Bridge
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=alde
WorkingDirectory=/home/alde
ExecStart=/usr/bin/python3 /home/alde/alde_mqtt.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Red Bus Service (`alde_red_bus_mqtt.service`)

```bash
sudo nano /etc/systemd/system/alde_red_bus_mqtt.service
```

```ini
[Unit]
Description=Alde 3030 Plus Red Bus MQTT Bridge
After=network.target pigpiod.service
Wants=network-online.target pigpiod.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=alde
WorkingDirectory=/home/alde
ExecStart=/usr/bin/python3 /home/alde/alde_red_bus_mqtt.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

> ℹ️ The red bus service declares `After=pigpiod.service` to ensure the pigpio daemon is ready before the script starts.

### Enable and Start Both Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable alde.service alde_red_bus_mqtt.service
sudo systemctl start alde.service alde_red_bus_mqtt.service
sudo systemctl status alde.service alde_red_bus_mqtt.service
```

### Useful Commands

```bash
# Check both services are running
sudo systemctl status alde.service alde_red_bus_mqtt.service

# View live logs (yellow bus)
sudo journalctl -u alde.service -f

# View live logs (red bus)
sudo journalctl -u alde_red_bus_mqtt.service -f

# Restart a service manually
sudo systemctl restart alde.service
sudo systemctl restart alde_red_bus_mqtt.service
```

---

## Home Assistant Dashboard

A ready-made dashboard is included in `alde_dashboard.yaml`. To install it:

### Prerequisites

The dashboard requires two HACS custom cards:

- **[ApexCharts Card](https://github.com/RomRider/apexcharts-card)** — for the temperature history graph
- **[Button Card](https://github.com/custom-cards/button-card)** — for the colour-coded status indicators

Install both via HACS before adding the dashboard.

### Installation

1. In Home Assistant go to **Settings → Dashboards**
2. Click **Add Dashboard** and give it a name (e.g. "Alde Heating")
3. Open the dashboard and click the three dots → **Edit Dashboard**
4. Click the three dots again → **Raw Configuration Editor**
5. Replace all content with the contents of `alde_dashboard.yaml`

The dashboard includes:
- Current indoor, setpoint and outdoor temperatures
- 24-hour temperature history graph
- Thermostat control dial
- Gas, electric power and water mode controls
- Status indicators for pump, valve, AC input and error

---

## Entities Created

### From `alde_mqtt.py` (Yellow CI-Bus)

| Entity ID | Display Name | Type | Description |
|-----------|-------------|------|-------------|
| `climate.alde_3030` | Alde 3030 | Climate | Temperature dial and setpoint |
| `switch.alde_gas` | Gas | Switch | Gas heating on/off |
| `select.alde_electric` | Electric Power | Select | Off / 1kW / 2kW / 3kW |
| `select.alde_water` | Water Mode | Select | off / on / boost |
| `sensor.alde_outdoor` | Outdoor Temperature | Sensor | Outdoor temperature (°C) |
| `binary_sensor.alde_pump` | Circulation Pump | Binary sensor | Circulation pump running |
| `binary_sensor.alde_valve` | Gas Valve | Binary sensor | Gas valve open/closed |
| `binary_sensor.alde_ac` | AC Input | Binary sensor | AC mains input present |
| `binary_sensor.alde_error` | Error | Binary sensor | Error flag |

### From `alde_red_bus_mqtt.py` (Red Internal Bus)

| Entity ID | Display Name | Type | Description |
|-----------|-------------|------|-------------|
| `sensor.alde_glycol` | Glycol Temperature | Sensor | Glycol circuit temperature (°C) |
| `sensor.alde_hot_water` | Hot Water Temperature | Sensor | Hot water tank temperature (°C) |

All entities above appear under a **single "Alde 3030 Plus" device** in Home Assistant. Both scripts use the same device identifier (`alde_3030_plus`) in their MQTT discovery payloads, so HA automatically groups them together.

---

## Tested On

- Alde Compact 3030 Plus (single zone)
- Raspberry Pi Zero 2 W
- Home Assistant OS 2026.3.2
- Mosquitto MQTT broker (HA add-on)
- Debian 13 (Trixie) — pigpio built from source

## Compatibility

This integration connects to both the **yellow CI-bus** connector and the **red internal bus** on the Alde 3030 Plus panel. The yellow bus integration may also work with other Alde models that use the same CI-bus protocol — contributions and test reports welcome.

> ℹ️ The Alde Smart Control accessory (Alde's own GSM module) only supports 3010 and 3020 panels. This integration was reverse engineered specifically for the 3030 Plus.

---

## Notes

- **Remote Control setting**: You do not need to enable "Remote Control" in the panel's System Configuration. Commands work either way. If it is enabled, the panel will show a "Remote control missing" error — this is cosmetic and does not affect functionality.
- **Auto water mode**: The panel's Auto water mode is not directly exposed on the CI-bus — there is no distinct "auto" state transmitted. Instead, Auto mode is implemented entirely within the panel: it silently monitors the water temperature and when it decides heating is needed, it sends boost (bits3-4=2) on the CI-bus without any user intervention. When the water reaches temperature it returns to on (bits3-4=1). From the CI-bus perspective Auto mode is invisible — you only see its effect when the panel triggers a boost cycle. If you leave the panel in Auto and see the water mode in HA switching between "on" and "boost" autonomously, that is the panel's auto heating logic at work and is completely normal behaviour.
- **Outdoor temperature resolution**: The Alde outdoor sensor has ~1°C physical resolution despite the protocol supporting 0.5°C steps. Steps of 1.0–1.5°C are normal.
- **Zone 2**: The protocol supports two zones. Zone 2 reports as unused on single-zone systems.
- **Red bus is passive**: The red bus script never transmits — it only listens. There is no risk of interfering with the internal bus.
- **pigpio on Trixie**: The standard `apt` package for pigpio does not work correctly on Debian 13 (Trixie). Build from source as described in the setup section above.

---

## Contributing

Contributions are very welcome! Particularly:

- Testing on other Alde models (3020, 3030 non-plus etc)
- Testing with two-zone systems
- Decoding additional red bus frames
- Home Assistant automation examples using glycol/hot water temps
- ESPHome port
- Docker container

Please open an issue or pull request on GitHub.

---

## Acknowledgements

- [WomoLIN project](https://wiki.womonet.io/protocols/lin/alde/) — CI-bus protocol documentation
- [inetbox.py](https://github.com/danielfett/inetbox.py) — LIN bus implementation reference (Truma)
- [WomoLIN Telegram group](https://t.me/womo_LIN) — community support

---

## Licence

MIT Licence — see [LICENSE](LICENSE) for details.
