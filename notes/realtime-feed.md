# Realtime feed (WebSocket)

The live data path. This is what the Sense app's "Now" screen and our collector read.

## Connecting

```
wss://clientrt.sense.com/monitors/{monitor_id}/realtimefeed?access_token={access_token}
```

- `monitor_id` and `access_token` come from `POST /authenticate` (see [api.md](api.md)).
- **Pass the token in the query string only.** Adding `Authorization` or the `X-Sense-*` headers
  the REST client uses made the handshake hang until timeout (observed 2026-10-15). Plain query auth works.
- The app calls this protocol "bridgelink" internally (`com.sense.bridgelink.*`). Despite the name,
  it is a cloud relay, not a local link to the monitor.
- The authenticate response also has `bridge_server` (`wss://mb1.home.sense.com` for us).
  Purpose unconfirmed; `clientrt.sense.com` is what the app connects to.
- Messages arrive at ~2 Hz. Sense has been known to throttle or idle long-lived feeds;
  the collector reconnects with backoff.

## Message envelope

Every message is `{"type": "<type>", "payload": {...}}`.

| type | when | payload |
|---|---|---|
| `hello` | on connect | `online`, `connection_state` (`ONLINE`), `connection_state_time` |
| `monitor_info` | on connect | `features`: comma list of monitor capabilities (see below) |
| `data_change` | on connect + when cloud state changes | checksums/versions for devices, overview, settings; `pending_events` (new device found, goals) |
| `device_states` | on connect + changes | `states: [{device_id, mode, state}]`, mode `active`/`off`/`none`, state `online`/`offline` |
| `realtime_update` | ~2 Hz | the live data, below |
| `new_timeline_event` | device on/off etc. | `items_added: [{time, type: "DeviceOn", body: "Microwave turned on", destination: "device:<id>", ...}]` |

Other types referenced in the client model code but not yet observed: `error`, `solar_status`, `recent_history`.

## `realtime_update` payload

Observed fields (no solar, no generator, no battery on this install):

| field | meaning |
|---|---|
| `w` | total home power, W (float) |
| `d_w` | total home power, W (rounded int) |
| `grid_w` | grid draw, W (int); negative when exporting |
| `channels` | **power per leg, `[L1, L2]`, W.** Not in most third-party integrations. |
| `voltage` | `[L1, L2]` volts |
| `hz` | line frequency |
| `frame` | **monitor's AC-cycle counter.** Increments 60/s on a 60 Hz grid. See below. |
| `epoch` | unix seconds (monitor/cloud clock) |
| `c` | unknown small int (14 in our captures). Possibly a sequence/channel count. |
| `defaultCost` | electricity cost setting, cents/kWh |
| `power_flow` | e.g. `{"grid": ["home"]}`; topology for solar/battery display |
| `devices` | `[{id, name, icon, w, ...}]` currently-on devices as Sense attributes them |
| `deltas` | **step events detected on the monitor.** Present in ~1 of every 15 messages. See below. |

Fields in the client model (`RealTimeUpdate`) that only show up with other hardware or features:
`solar_w`, `solar_c`, `solar_pct`, `d_solar_w`, `battery_pct`, `d_battery_w`, `generator_pct`,
`d_generator_w`, `gen_state`, `aux`, `amps`, `ao_w` (always-on watts), `tou_alert`, `backup_power`, `_stats`
(with `brcv`/`mrcv`/`msnd`, apparently bridge/monitor receive/send timestamps).

## `frame`: the monitor's clock

`frame` counts AC cycles. Verified: a microwave run timed at 30 s on a phone spanned
`25798290 - 25796520 = 1770` frames = **29.5 s at 60/s**. Consecutive `realtime_update`s are ~30 frames apart.

Use `frame` (not `epoch` or receive time) for anything timing-sensitive: it comes from the monitor's
sampling, it's monotonic, and it doesn't suffer cloud or network jitter.

## `deltas`: step events

```json
"deltas": [{"frame": 25796520, "channel": 1, "start_frame": 25796520, "w": 299.4}]
```

- `channel`: 0 = L1, 1 = L2
- `w`: signed step size, W (positive = something turned on)
- `start_frame` / `frame`: when the step began/was confirmed (equal in everything we've seen so far)

These are the monitor's own on-board edge detections, at cycle resolution. Observed down to ~14 W.
About 8 per minute in a quiet house. This is the raw material for doing our own appliance detection
without touching the hardware. See [findings.md](findings.md) for real signatures.

A 240 V appliance should produce simultaneous deltas on both channels (not yet observed; to confirm).

## `monitor_info.features` (one real monitor)

```
MONITOR_RESET, DEVICE_DELETION, DEVICE_STATE_UPDATE, NDI, UPDATE_CONFIG_ACK, HUE, NETEVENT_WM,
TPL_CTRL, WEMO_CTRL, SMARTPLUGS, HS300, AO_SANS_NDI, CONNECTION_TEST, GENERATOR, SSI, SPLIT_PANEL,
DEDICATED_CIRCUIT, RTS_HISTORICAL, WISER_HOME_DEVICES, RTU_WATTS_SIGNED, REGISTRATION_TOKEN,
WISER_RELAY, MULTI_TENANT_RESET, SIDECAR_SOLAR, SIDECAR_EV, SIDECAR_VSHP
```

Guesses worth checking: `NDI` = network device identification (the app's "Enable Network Detection");
`TPL_CTRL`/`HS300`/`SMARTPLUGS` = TP-Link Kasa plug integration; `SIDECAR_*` = add-on CT sensors for
solar/EV/variable-speed heat pump; `RTU_WATTS_SIGNED` = signed watts in realtime updates.
