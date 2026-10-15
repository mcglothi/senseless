# Deploy plan: Senseless on babbage (draft for review)

Status: APPROVED with changes, 2026-10-15. Author: Claude Code. Reviewer: Codex (review summary at bottom).

## Context

- Senseless = this repo. Today: `senseless collect` (Sense cloud WebSocket → SQLite + daily raw jsonl.gz). Python 3.12, `websockets`, `requests`.
- babbage = TrueNAS SCALE host (10.10.10.10), Docker managed by Dockge (`/mnt/.ix-apps/app_mounts/dockge/stacks/<stack>/`).
- Existing `sense-exporter` (custom Python, Sense WS → Prometheus on :9801) lives inside the big `monitoring` Dockge stack. Its `.env` is rendered by Ansible (`homelab-platform/ansible/roles/monitoring_stack/templates/monitoring.env.j2`) from Bitwarden item `sense.com`.
- Infra-as-code repo: `~/code/homelab-platform` (`stacks/dockge/<svc>/compose.yml`, `ansible/playbooks/*`, `ansible/roles/*`, `monitoring/prometheus/{prometheus.yml,rules/*.yml}`).
- House checklist: AIKB `home-lab/services/new-service-checklist.md` (13 sections, every applicable box must be checked).

## App changes (senseless repo)

1. **HTTP server** inside the collector process (aiohttp, same asyncio loop), port **8780**:
   - `GET /healthz`: 200 if WS connected and last message < 30 s old, else 503
   - `GET /metrics`: Prometheus text. Superset of sense-exporter's metrics so it can replace it later:
     `senseless_up`, `senseless_last_message_age_seconds`, `senseless_power_watts`, `senseless_leg_power_watts{leg}`,
     `senseless_voltage_volts{leg}`, `senseless_frequency_hz`, `senseless_device_power_watts{device}`,
     `senseless_deltas_total{leg,direction}`, `senseless_messages_total{type}`, `senseless_reconnects_total`, `senseless_db_bytes`
   - `GET /api/status`, `GET /api/deltas?minutes=N`, `GET /api/readings?minutes=N`: JSON
   - `GET /`: minimal status page (current W per leg, last N deltas, connection state). No JS framework.
2. **Dockerfile**: `python:3.12-slim` + uv, non-root (UID/GID configurable, default 568 = TrueNAS `apps`),
   `HEALTHCHECK` hitting `/healthz`, data at `/data`, `SENSELESS_DATA=/data`.
3. **CI**: GitHub Actions builds multi-arch (amd64/arm64) image → `ghcr.io/mcglothi/senseless:<tag>` on git tag `v*`.
   Public repo, so friends can `docker pull`. babbage pins the exact tag (checklist: no `latest`).
4. `docker-compose.example.yml` in the repo for other users.

## Infra (homelab-platform repo + babbage)

| # | Checklist section | Plan |
|---|---|---|
| 1 | Storage | ZFS dataset `Containers/Senseless` → `/mnt/Containers/Senseless`, chown `apps:apps` (568), bind to `/data`. Snapshots via existing TrueNAS periodic snapshot task on `Containers` (verify it's recursive). |
| 2 | Container | New **separate** Dockge stack `senseless` (not inside `monitoring`). `stacks/dockge/senseless/compose.yml` in homelab-platform. Image pinned `ghcr.io/mcglothi/senseless:0.1.0`. Port `8780:8780`. `restart: unless-stopped`. Log driver default (promtail picks up docker logs, to verify). |
| 2b | Deploy automation | New playbook `deploy-senseless.yml` + role `senseless_stack`: ensure ZFS dataset + ownership, copy compose, render `.env` from Bitwarden `sense.com` (same lookup as monitoring role), `docker compose up -d`. |
| 3 | DNS | `*.home.timmcg.net` wildcard rewrite already → 10.10.10.10. No new rewrite. Verify on primary (10.10.10.10) and secondary (10.10.0.22). |
| 4 | NPM | Proxy host `sense.home.timmcg.net` → `http://10.10.10.10:8780`, cert id 1 (`npm-1` wildcard), force SSL, HTTP/2, HSTS, block exploits, websocket upgrade on. Done via a **generic** `npm_proxy_host` role (parametrized clone of `npm_proxy_ensure_minio`), NPM creds from Bitwarden. Add to DNS map in `network-dns.md`. |
| 5 | Credentials | Reuse Bitwarden `sense.com` (already used by monitoring role). No new secrets. Note the naming deviation (`sense.com` vs `<Service>/...`). |
| 6 | Authentik | **Open question.** Only `ai.home` uses forward auth today. Data is household power usage (reveals occupancy patterns). Proposal: forward-auth on `/` and `/api/*` via NPM advanced config (copy ai.home's snippet); Prometheus and Homepage hit `10.10.10.10:8780` directly, bypassing NPM. |
| 7 | Netbox | Service record on babbage: senseless, tcp/8780, tag `monitoring`. Check `register_services.yml` / `service_register` role. |
| 8 | Ansible | babbage is already inventoried. Backup: ZFS snapshots + (optional) add dataset to `backup_verify`. |
| 9 | Observability | Prometheus job `senseless` (15 s scrape, `10.10.10.10:8780`). Alert rules: `SenselessDown` (up==0 5m), `SenselessFeedStale` (last_message_age > 120 s for 5m), `SenselessNoDeltas` (no deltas 2h, warning). Grafana: add per-leg + deltas panels to `SENSE::POWER` (uid `sense-power-001`), or new `SENSELESS::EVENTS` dashboard. Verify Loki gets container logs. |
| 10 | Homepage | Card in homepage-react (`www.home.timmcg.net`), Energy/Monitoring section: link, icon, live W/L1/L2 from `/api/status`. |
| 11 | Diagrams | `homelab-logical.d2` add Senseless under monitoring; re-render SVG. No network diagram change (no new DNS/VLAN). |
| 12 | Docs | AIKB `home-lab/services/senseless.md`, homelab-platform `docs/services/senseless.md` + mkdocs nav, `access-reference.md`, services `README.md` index, `monitoring.md` cross-link. |
| 13 | Notify | AIKB runtime capture + IM. |

## Coexistence with sense-exporter

Run both for now (two independent WS sessions to Sense worked fine in testing). Once Senseless's `/metrics`
covers everything and Grafana/Homepage are pointed at it, retire `sense-exporter` from the monitoring stack
(separate change).

## Questions for reviewer

1. SQLite in WAL mode on ZFS with a single writer: fine, or go straight to Postgres/Timescale? (Est. ~170k readings/day.)
2. Separate stack vs adding to `monitoring` stack?
3. GHCR image via CI vs `build:` on babbage like sense-exporter?
4. Authentik forward auth: worth it for this, given LAN-only DNS?
5. Anything missing from the checklist mapping, or any failure mode in the collector (token expiry, Sense idling the WS, clock/frame handling, disk growth of raw archive) that should be handled before deploy?

## Review outcome (Codex, 2026-10-15)

Accepted:
- SQLite/WAL, separate stack, GHCR CI: confirmed.
- Collector hardening before 24/7: stale-feed watchdog + exit-after-unhealthy, auth inside retry loop and off the
  event loop, re-auth only on 401/403, per-message error isolation, `generation` key for frame resets, `recv_ts`,
  SIGTERM cleanup, timer-based commits, raw retention, atomic 0600 token cache under `/data`. All covered by tests
  (falsified by reverting) and live drills (bogus token → 401 → re-auth; forced stale → reconnect; SIGTERM → exit 0, valid gzip).
- Authentik forward auth on the NPM host.
- Pin image by digest; verify GHCR package is publicly pullable.
- Live prometheus.yml has drifted from the repo copy: reconcile before deploying from the repo.

Deviations / accepted risks:
- Direct port 8780 stays open on the LAN for Prometheus and Homepage, same as `sense-exporter`'s :9801 today.
  Codex recommended a private network or machine auth. Follow-up: optional bearer token for `/metrics` + `/api`.
- `service_register` doesn't do Netbox and still writes the legacy Pi-hole list; wildcard DNS makes the latter a no-op.
  Netbox registration done separately.
- `backup_verify` is a placeholder. Relying on ZFS snapshots for now; real restore test is a follow-up.
