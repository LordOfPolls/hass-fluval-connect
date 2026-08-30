# Fluval Connect light ~ Home Assistant integration

Local Bluetooth control for Fluval "Connect"-generation aquarium lights (Roma & Shaker
2.0, Plant Pro 4.0 and relatives) over the Home Assistant host's own adapter.

Reverse-engineered from the FluvalConnect Android app (v1.0.15), confirmed against a
Roma & Shaker 2.0 (product 564, firmware 15).

## Install

Add as a HACS custom repository (category *Integration*), or copy
`custom_components/fluval/` into `config/custom_components/`. 
Then let Bluetooth discovery find the light ~~ or add it from **Settings → Devices & services →
Add integration → Fluval**.

## Entities

| | |
|---|---|
| `light.<name>` | On/off, brightness, RGBW mix, weather effects |
| `select.<name>_mode` | Manual / Auto / Pro |
| `sensor.<name>_next_on` / `_next_off` | Timestamps, computed from the light's own schedule |
| `sensor.<name>_firmware` | Diagnostic, disabled by default |
| `button.<name>_sync_clock` | Push local time to the light's RTC |
| `button.<name>_identify` | Flash the light |

Mode, Pro schedule and Auto sunrise/sunset are attributes on the light entity.


```yaml
automation:
  - trigger:
      - platform: time
        at: sensor.roma_shaker2_0_6ac0b2_next_off
    action:
      - action: switch.turn_off
        target: { entity_id: switch.tank_pump }
```

## Pro schedules

`fluval.set_simple_schedule` writes the usual dark / ramp-up / hold / ramp-down day from
four fields. `fluval.set_pro_schedule` takes the breakpoints directly:

```yaml
action: fluval.set_pro_schedule
target: { entity_id: light.roma_shaker2_0_6ac0b2 }
data:
  points:
    - time: "05:30"
      color: [0, 0, 255]
      brightness: 0
    - time: "07:30"
      color: [255, 255, 182]
      white: 100
      brightness: 80
    - time: "19:30"
      color: [255, 255, 182]
      white: 100
      brightness: 100
    - time: "21:30"
      levels: "0, 0, 0, 0"
```

Both render as forms in the action editor.

## Bluetooth

The light takes **one connection at a time** and stops advertising while connected. This
integration holds the link by default.
The cost is that the phone app cannot connect while Home Assistant is running. 
Turn off **Hold the Bluetooth connection open** in the options to share.
Please note, this will make commands slower. 

**Refresh every** sets the poll interval, 10–3600s, defaulting to 60 held and 300
shared. 

If your signal is too weak to be reliable you could try an ESP32 running ESPHome as a Bluetooth
proxy, with no changes here.


## Licence

MIT. Not affiliated with, or endorsed by, Fluval or Rolf C. Hagen Inc.
