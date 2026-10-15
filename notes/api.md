# REST API

Base: `https://api.sense.com/apiservice/api/v1/` (a test env, `testscarlet.sense.com`, is also referenced in the client).

## Authentication

```
POST /authenticate
Content-Type: application/x-www-form-urlencoded

email=...&password=...
```

Response keys: `access_token`, `refresh_token`, `user_id`, `account_id`, `authorized`, `bridge_server`,
`monitors[]`, `settings`, `totp_enabled`, `date_created`.

`monitors[0]` keys: `id`, `serial_number`, `hardware_type`, `online`, `time_zone`, `attributes`
(address, home size/type, cost settings, survey progress, …), `solar_configured`, `solar_connected`,
`aux_port`, `ethernet_supported`, `zigbee_supported`, `signal_check_completed_time`, `data_sharing`.

- ⚠️ `monitors[0].online` was `false` while the monitor was clearly online and streaming. Don't trust it;
  the WebSocket `hello` message has the real state.
- MFA accounts: `POST authenticate/mfa` with `totp`, `mfa_token`, `client_time` (from the client code; untested).
- Token renewal: `POST renew` (with `refresh_token`; untested).
- Authenticated REST calls use `Authorization: bearer <access_token>`.

Headers the Android client sends on every request (we mirror them):

```
Sense-Client-Version: 2026.14-10839
X-Sense-Protocol: 13
X-Sense-Client-Type: android
X-Sense-UI-Language: en
X-Sense-UI-First-Day-Of-Week: ...
X-Sense-Device-Id: ...    (some calls)
```

## Endpoint inventory

Extracted statically from the Android client, version 2026.14-10839. `{id}` is a path parameter
(monitor, user, device, …). HTTP methods aren't listed: the client uses Ktor's request builder, and
methods aren't recoverable by simple grep in the obfuscated build. Capturing the app's traffic will fill
them in. "Service" is the client-side class that calls the endpoint, which hints at its purpose.

**Interesting for a replacement app / detection:**
`devices/overview`, `devices/{id}` (detail), `devices/{id}/merge`, `devices/{id}/supersede`,
`devices/progressive/{id}`, `devices/user_device_measurement/{id}` (the hidden "Watt Check" feature),
`device_inventory`, `inventory_survey`, `ndt/enable|disable` (network device detection),
`history/usage` (+ `averages`, `solar`), `history/cumulative_comparison`, `monitors/{id}/data`,
`peak`, `sidecar/config`, `status`, `monitors/devicetypes/info`.

| Path | Client service |
|---|---|
| `app/accounts/{id}` | AccountManagerRestService |
| `app/history/usage` | SenseRestService |
| `app/monitors/devicetypes/info` | SenseRestService |
| `app/monitors/{id}/attributes` | SenseRestService |
| `app/monitors/{id}/data_sharing/{id}` | SenseRestService |
| `app/monitors/{id}/detection/solar/disable` | SenseRestService |
| `app/monitors/{id}/detection/solar/enable` | SenseRestService |
| `app/monitors/{id}/device_inventory` | SenseRestService |
| `app/monitors/{id}/devices/overview` | SenseRestService |
| `app/monitors/{id}/devices/progressive/{id}` | SenseRestService |
| `app/monitors/{id}/devices/user_device_measurement/{id}` | WattCheckService |
| `app/monitors/{id}/devices/{id}` | SenseRestService |
| `app/monitors/{id}/devices/{id}/control` | DevicesService, SenseRestService |
| `app/monitors/{id}/devices/{id}/merge` | SenseRestService |
| `app/monitors/{id}/devices/{id}/notifications` | SenseRestService |
| `app/monitors/{id}/devices/{id}/notifications/{id}` | SenseRestService |
| `app/monitors/{id}/devices/{id}/supersede` | SenseRestService |
| `app/monitors/{id}/energy_actions` | TipsService |
| `app/monitors/{id}/history/cumulative_comparison` | UsageService |
| `app/monitors/{id}/history/usage` | UsageService |
| `app/monitors/{id}/history/usage/averages` | UsageService |
| `app/monitors/{id}/history/usage/solar` | UsageService |
| `app/monitors/{id}/ignore_aux_port` | AccountManagerRestService |
| `app/monitors/{id}/integrations` | SenseRestService |
| `app/monitors/{id}/integrations/hue` | ConnectedDevicesService |
| `app/monitors/{id}/integrations/{id}` | ConnectedDevicesService, SenseRestService |
| `app/monitors/{id}/inventory/add` | DeviceInventoryService |
| `app/monitors/{id}/inventory_survey` | DeviceInventoryService |
| `app/monitors/{id}/ndt/disable` | SenseRestService |
| `app/monitors/{id}/ndt/enable` | SenseRestService |
| `app/monitors/{id}/overview` | MonitorService, SenseRestService |
| `app/monitors/{id}/peak` | SenseRestService |
| `app/monitors/{id}/peak/{id}` | SenseRestService |
| `app/monitors/{id}/predicate_notifications` | GoalsService, SenseRestService |
| `app/monitors/{id}/predicate_notifications/help` | GoalsService, SenseRestService |
| `app/monitors/{id}/predicate_notifications/progress` | GoalsService, SenseRestService |
| `app/monitors/{id}/predicate_notifications/{id}` | GoalsService, SenseRestService |
| `app/monitors/{id}/predicate_notifications?fetch_all=true` | GoalsService, SenseRestService |
| `app/monitors/{id}/rate_zones` | RateZoneRestService |
| `app/monitors/{id}/rate_zones/{id}` | RateZoneRestService |
| `app/monitors/{id}/reset_data` | AccountManagerRestService |
| `app/monitors/{id}/sidecar/config` | SenseRestService |
| `app/monitors/{id}/solar/status` | SenseRestService |
| `app/monitors/{id}/solar_specs` | SenseRestService |
| `app/monitors/{id}/status` | SenseRestService |
| `app/monitors/{id}/timezone` | AccountManagerRestService |
| `app/monitors/{id}/upgrade_monitor` | SenseRestService |
| `authenticate` | AccountManagerRestService |
| `authenticate/mfa` | — |
| `monitors/{id}/data` | UsageService |
| `monitors/{id}/network_tests` | ConnectionTestService |
| `monitors/{id}/network_tests/notification_dismiss` | AccountManagerRestService |
| `monitors/{id}/now_headline` | NowService |
| `monitors/{id}/solar_setup` | SenseRestService |
| `monitors/{id}/start_btle` | SenseRestService |
| `renew` | AccountRestService |
| `users/{id}` | AccountManagerRestService |
| `users/{id}/email` | AccountManagerRestService |
| `users/{id}/iterable_token` | AccountManagerRestService |
| `users/{id}/message_preferences` | MessagePreferencesService |
| `users/{id}/mfa/totp` | AccountManagerRestService |
| `users/{id}/mfa/totp/disable` | AccountManagerRestService |
| `users/{id}/mfa/totp/verify` | AccountManagerRestService |
| `users/{id}/onboarding/milestone` | UserMilestoneService |
| `users/{id}/password` | AccountManagerRestService |
| `users/{id}/policies` | AccountManagerRestService |
| `users/{id}/pushinfo2` | AccountRestService |
| `users/{id}/register_meter` | MeterSetupService |
| `users/{id}/register_monitor` | MonitorService |
| `users/{id}/resend_email_verification` | — |
| `users/{id}/settings` | AccountManagerRestService |
| `users/{id}/system_registration/{id}` | MeterSetupService |
| `users/{id}/timeline` | TimelineService |
| `users/{id}/utility/meter/enable_ap` | MeterSetupService |
| `users/{id}/utility/otp/generate` | MeterSetupService |
| `users/{id}/utility/otp/verify` | MeterSetupService |
| `users/{id}/zendesk_token` | AccountManagerRestService |
| `users/{id}?with_monitors=true` | — |
