# Notes

What we know about the Sense energy monitor's software interfaces, written for both people and AI agents.

| File | What's in it |
|---|---|
| [realtime-feed.md](realtime-feed.md) | The WebSocket feed: connecting, every message type, `frame` and `deltas` explained |
| [api.md](api.md) | REST authentication, client headers, full endpoint inventory |
| [senseless-api.md](senseless-api.md) | Senseless's own HTTP API: routes, parameters, responses and write protection |
| [energy-providers-spec.md](energy-providers-spec.md) | Provider backend contract: interval importers, tariff packs, normalized bills |
| [energy-providers.md](energy-providers.md) | Migration/compatibility notes and contributing an importer or tariff pack |
| [billing-import-spec.md](billing-import-spec.md) | CMP PDF/hourly meter CSV import contract, privacy rules, billing insights and cycle adjustments |
| [detection.md](detection.md) | Load detection: steps, runs, persistent patterns, naming hints and limits |
| [cycles.md](cycles.md) | Program activity cycles: detection rules, persistent cycle patterns, replay results and limits |
| [findings.md](findings.md) | Dated observations from real hardware, incl. a labeled microwave signature |
| [methodology.md](methodology.md) | How it was reverse-engineered, so you can redo it on a new app version |
| [open-questions.md](open-questions.md) | What we don't know yet + roadmap |

Conventions:
- **Confirmed vs. guessed.** Anything not verified against real traffic is marked as a guess or "untested".
- **Date your findings** and say how you confirmed them.
- **No account data.** No tokens, IDs, serials, addresses. Use `tools/sanitize.py` on captures.
- App version these notes are based on: `com.sense.androidclient` **2026.14-10839**.
