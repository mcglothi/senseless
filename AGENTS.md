# Agent guide: senseless

Local-first tooling for the Sense home energy monitor. Read `notes/README.md` first; `notes/realtime-feed.md`
is the core protocol reference.

## Layout

- `senseless/auth.py`: login (`POST /authenticate`), token cache. Creds from `SENSE_EMAIL`/`SENSE_PASSWORD`,
  or optional Bitwarden fallback (`SENSE_BW_ITEM`, `~/.bw_session`).
- `senseless/collector.py`: WebSocket feed → `Store`, raw archive, reconnect/backoff.
- `senseless/store.py`: SQLite schema + writes. `frame` is the timing key, not `ts`.
- `tools/sanitize.py`: strip IDs/location from captures.
- `samples/`: sanitized captures. `microwave-30s.jsonl` is labeled ground truth (see `notes/findings.md`).
- `data/`, `captures/*.auth.json`: local only, gitignored. Never commit them.

## Rules

- **Never commit account data**: tokens, monitor/user/device IDs, serials, addresses, raw unsanitized captures.
  Sanitize, then grep the output for the real IDs before committing.
- **No decompiled Sense source in this repo.** Describe behavior in `notes/`; don't paste their code.
- **Mark guesses.** In notes, distinguish verified-against-traffic from inferred-from-client-code.
- Feed connection: token in the query string only (headers break the handshake). See notes.
- When you confirm something new, add a dated entry to `notes/findings.md` and tick it off in `open-questions.md`.
- Run with `uv run senseless ...`. Python ≥ 3.12.
