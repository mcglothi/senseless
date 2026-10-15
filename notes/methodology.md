# How this was figured out

So you can reproduce it, update it when Sense ships a new app version, or extend it.

## 1. Pull and decompile the Android app

```bash
adb pair <phone-ip>:<pair-port> <code>          # Wireless debugging → Pair with code
adb shell pm list packages | grep -i sense       # → com.sense.androidclient
adb shell pm path com.sense.androidclient        # base.apk + split APKs
adb pull <each path>
jadx -d jadx/ base.apk                           # Java-ish source
apktool d base.apk -o apktool/                   # resources, strings.xml, manifest
```

What we got: native Kotlin + Jetpack Compose, networking via **Ktor** (on OkHttp), obfuscated with R8.
Not React Native or Flutter, so JADX output is the real logic.

## 2. Find the API surface

Ktor has no Retrofit-style `@GET("...")` annotations. Paths are built at runtime like:

```java
"app/monitors/", CodecsKt.encodeURLPath$default(String.valueOf(id), ...), "/devices/overview", uRLBuilder
```

So grep for path-prefix string literals and stitch the pieces together, replacing each
`encodeURLPath(...)` with `{id}`:

```bash
grep -rhoE '"app/monitors/".{0,200}' jadx/sources
```

Base URLs and environment hosts are in `com/sense/network/Environment.java`. Client headers are set in a
Ktor `defaultRequest` block (search for `X-Sense-Protocol`). Service classes that survived obfuscation
(`_SenseRestServiceImpl`, `_UsageServiceImpl`, `_DevicesServiceImpl`, …) group the endpoints by feature.

The realtime feed URL is built in one place (search for `realtimefeed`). Its message models are in
`com/sense/bridgelink/models/` with field names in `@SerialName` annotations.

Check for pinning: grep for `CertificatePinner` outside `okhttp3/`, `sha256/` pins, and
`res/xml/network_security_config.xml`. None present in 2026.14-10839.

## 3. Confirm against the live feed

`senseless collect` does this (raw messages go to `data/raw/rt-YYYY-MM-DD.jsonl.gz`): authenticate,
open the WebSocket, dump every message to JSONL with a receive timestamp. Then:

1. Look at message types and field shapes.
2. **Create a labeled event**: run a known appliance at a known time (we used a microwave for 30 s) and
   find it in the capture. That's how `frame` was proven to be an AC-cycle counter and `deltas` to be step events.

## 4. Share captures safely

Captures contain your monitor ID, user ID and device IDs, and the authenticate response includes your
home address. Run [`tools/sanitize.py`](../tools/sanitize.py) before sharing; it replaces IDs with
stable placeholders and drops location fields, keeping all the power/timing data.

## Tools used

jadx, apktool, adb, Python + `websockets`, and [REA](https://github.com/morluto/rea) (an MCP reverse-engineering
toolkit) driven by Claude Code. The whole first pass took about an afternoon.
