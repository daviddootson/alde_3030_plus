# Alde 3030 Plus CI-Bus Protocol Documentation

This document describes the reverse-engineered protocol used on the Alde 3030 Plus yellow CI-bus connector. This was determined through logic analyser captures and experimentation.

## Physical Layer

- **Connector**: RJ12 6P6C (yellow connector on panel)
- **Bus type**: LIN (Local Interconnect Network)
- **Baud rate**: 19200 bps
- **Format**: 8N1 (8 data bits, no parity, 1 stop bit)
- **Bus voltage**: ~10.5V idle (pulled high via internal transceiver)
- **Pin 2**: GND
- **Pin 4**: LIN bus signal

## LIN Frame Structure

Each LIN frame consists of:
```
BREAK (dominant for ≥13 bit times) + SYNC (0x55) + FRAME_ID + DATA + CHECKSUM
```

### Break Generation
At 19200 baud, a break requires at least 13 × 52µs = 677µs. In practice we drop to 1200 baud and send 0x00 (8.33ms) which satisfies this requirement with plenty of margin.

### Checksum
All frames use **enhanced checksum** (includes frame ID):
```python
def lin_checksum_enhanced(frame_id, data):
    total = frame_id + sum(data)
    while total > 0xFF:
        total = (total & 0xFF) + (total >> 8)
    return (~total) & 0xFF
```

> ⚠️ Classic checksum (data only) does not work — the panel silently rejects control frames with classic checksum.

## Frame IDs

| Raw ID | Full ID (with parity) | Direction | Purpose |
|--------|----------------------|-----------|---------|
| 0x3C | 0x3C | Master → Slave | LIN diagnostic request |
| 0x3D | 0x7D | Slave → Master | LIN diagnostic response |
| 0x1A | 0x1A | Master → Slave | Control frame (send commands) |
| 0x1B | 0x5B | Slave → Master | Info frame (read status) |

## Registration / Handshake

Before sending control frames, send a LIN diagnostic request:

```
Frame ID: 0x3C
Payload:  10 06 B2 00 DE 41 03 00
```

| Byte | Field | Value | Meaning |
|------|-------|-------|---------|
| 0 | NAD | 0x10 | Node address of panel |
| 1 | PCI | 0x06 | Single frame, 6 data bytes |
| 2 | SID | 0xB2 | Read By Identifier |
| 3 | Identifier | 0x00 | Product Info |
| 4-5 | Supplier ID | 0xDE 0x41 | 0x41DE = Alde (little-endian) |
| 6-7 | Function ID | 0x03 0x00 | 0x0003 = Alde 3030+ (little-endian) |

No response is required from the panel. Sending this frame unlocks command acceptance.

**Known Function IDs:**
- 0x0001 = Alde Compact 3020
- 0x0002 = Alde 3030
- 0x0003 = Alde 3030+

## Control Frame 0x1A (Master → Panel)

Send: `BREAK + 0x55 + 0x1A + 8 bytes + checksum`

| Byte | Field | Bits | Encoding |
|------|-------|------|----------|
| b[0] | Reserved | — | Always 0x00 |
| b[1] | Reserved | — | Always 0x00 |
| b[2] | Reserved | — | Always 0x00 |
| b[3] | Zone 1 control | 0-5 | Setpoint: `(temp - 5) / 0.5`, range 5–30°C |
| | | 6 | Gas enable: 0=off, 1=on |
| | | 7 | Gas valve: 0=closed, 1=open |
| b[4] | Zone 2 + electric | 0-5 | Zone 2 setpoint (same encoding) |
| | | 6-7 | Electric power: 0=off, 1=1kW, 2=2kW, 3=3kW |
| b[5] | System flags | 0 | Panel on: 0=off, 1=on |
| | | 1 | Panel busy |
| | | 2 | Error present |
| | | 3-4 | Water mode: 0=off, 1=on, 2=boost |
| | | 5 | AC input available |
| | | 7 | Pump running |
| b[6] | Reserved | — | Always 0xFF |
| b[7] | Reserved | — | Always 0xFF |

### Example: 20°C setpoint, gas on, valve open, 2kW electric, panel on, water normal

```
b[3] = 0xDE  (setpoint=20°C: raw=30=0x1E, gas=1, valve=1: 0x1E|0x40|0x80)
b[4] = 0x9E  (setpoint=20°C: raw=30=0x1E, electric=2kW: 0x1E|0x80)
b[5] = 0x09  (panel_on=1, water_mode=1=normal: 0x01|0x08)
Full frame: 00 00 00 DE 9E 09 FF FF + checksum
```

## Info Frame 0x1B (Panel → Master)

Send header only: `BREAK + 0x55 + 0x5B`

The panel responds immediately with 9 bytes (8 data + 1 checksum).

**Echo handling**: Because LIN is a single-wire bus, the master receives its own transmitted bytes as echo. After sending the 0x5B header (3 bytes: 00 55 5B), read 12 bytes total — the first 3 are the echo and bytes 3–11 are the panel's response.

| Byte | Field | Encoding |
|------|-------|----------|
| b[0] | Zone 1 actual temp | `val × 0.5 - 42` °C |
| b[1] | Zone 2 actual temp | 0xFE = zone unused |
| b[2] | Outdoor temp | `val × 0.5 - 42` °C |
| b[3] | Zone 1 setpoint + gas | Same encoding as control frame b[3] |
| b[4] | Zone 2 + electric | Same encoding as control frame b[4] |
| b[5] | System status | Same encoding as control frame b[5] |
| b[6] | Reserved | 0x00 |
| b[7] | Reserved | 0x00 |

### Special Temperature Values

| Value | Meaning |
|-------|---------|
| 0xFB | Below sensor range (< -42°C) |
| 0xFC | Above sensor range (> 83°C) |
| 0xFD | No sensor detected |
| 0xFE | Zone unused / not installed |
| 0xFF | Invalid reading |

## Notes

### Water Mode
The protocol supports water modes 0–3 in bits 3-4 of b[5]. In practice only 0 (off), 1 (on) and 2 (boost) are observed on the CI-bus. The panel's "Auto" mode is implemented as panel-side intelligence and does not appear as a distinct value on the bus.

### Outdoor Temperature Resolution
Despite the 0.5°C encoding resolution, the Alde outdoor sensor has approximately 1°C physical resolution. Steps of 1.0–1.5°C between readings are normal and not a protocol issue.

### Single Zone Systems
Zone 2 (b[1] of info frame) always returns 0xFE (zone unused) on single-zone installations. Zone 2 setpoint bytes can be set to any valid value — they are ignored by single-zone systems.

### Remote Control Panel Setting
The panel has a "Remote Control" option in System Configuration. This does not need to be enabled for the integration to work. If enabled, the panel shows a "Remote control missing or not working" error — this is cosmetic only and does not block command acceptance.
