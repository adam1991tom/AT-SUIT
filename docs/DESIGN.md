# AT-SUIT design

AT-SUIT replaces the separate AT apps, Docker containers and dashboards with
one server app and one node client per laptop. This document covers what
exists today, the target architecture, how each existing app maps into a
module, the data migration, deployment, productisation, and the build order.

Status: **v0.1 implemented in this repo** (server core, chat, timers, fleet,
captions, overlays, dashboard, node UI, node agent, packaging). Presenter
management and Ontime retirement are later phases (see the build order).

---

## 1. What runs today

Findings from the read-only snapshots of both venue servers on 2026-10-07.

| What | Where | Port | Runtime | Data |
|---|---|---|---|---|
| Homarr dashboard ("USER CONTROL", ADMIN + USERS boards, 61 links) | ATSERVER 10.100.70.100 | 7575 | Docker | `/DATA/AppData/Homarr` |
| Fleet Dashboard ("IP SCANNER", kiosk Pis over SSH) | ATSERVER | 5000 | Flask, hand-started, **stopped** | `/var/tmp` (gone) |
| Beszel monitoring hub | ATSERVER | 8090 | Docker | `/opt/beszel-work` |
| AT-RoomComms 0.9.2 (branch `chat-overhaul`) | ATSERVER | 5070 | Docker | volume `roomcomms-data` (SQLite + uploads + Fernet key) |
| AT-RoomComms 1.0.0 | ATSERVER1 10.100.70.101 | 5070 | Docker | volume `roomcomms-data` |
| AT Device Suite 2.0.0 ("SCREEN CONFIG", kiosk fleet) | ATSERVER1 | 5050 | systemd Flask | `/opt/device-suite/{state.json,rooms.txt,client-updates}` |
| device-agent-updater | ATSERVER1 | - | systemd timer, 60 s, SSH | log only |
| AT Ops (merge of Device Suite + RoomComms) | ATSERVER1 | 5050 + 5070 | systemd FastAPI, **disabled** | `/opt/at-ops/data` |
| Ontime x20 (one per room) | ATSERVER1 | 4001-4020 | Docker | 20 small volumes |
| Bitfocus Companion | ATSERVER1 | 8000, 16622 | Docker | volume `companion_data` |
| AT-LiveOverlay 4.2 | Windows laptops (seen on .88) | 8765 | .NET 8 WinForms + WebView2 | `%APPDATA%` |
| AT-LiveCaption 2.7 | Windows laptops | 8765 | Python, sherpa-onnx, tray EXE | `%LOCALAPPDATA%` |
| AT-ScreenTest | Windows laptops | - | WPF .NET 8 | none |
| AT-Presenter 0.2 | not deployed | 5091 | Docker FastAPI + Ollama | volume `presenter-data` |

Observations that shape the design:

- **Every server app is already Python/FastAPI or Flask**, single process,
  SQLite or JSON on disk. One Python server is the natural target.
- **AT-Presenter's server is a superset of RoomComms 0.9.x**: same schema
  (accounts, operators, events, rooms, messages, DMs, help, issues, devices)
  plus presenters, cue schedules and room sync. Its password hash format is
  identical, so accounts migrate without password resets.
- **AT Ops already proved the fleet + chat merge**, but it runs two
  uvicorn servers to keep the old ports and has separate auth for each half.
  AT-SUIT keeps its fleet logic and its backwards-compatible machine
  endpoints, and drops the two-port split.
- **LiveCaption and LiveOverlay both default to port 8765**, which is why
  they can't share a laptop today. In AT-SUIT, captions run on the server, so
  the clash disappears.
- **No reverse proxy, no TLS, every app on its own port.** The dashboard
  exists mainly because nothing else ties the ports together.
- **Kiosk rooms config (`rooms.txt`) has 149 lines of pasted code** after the
  room lines, and room KING points at a port nothing listens on. The
  importer only takes `ROOM|URL|LABEL;` rows and reports bad ones.
- **Secrets are scattered**: a hardcoded SSH password in the Fleet Dashboard,
  a Beszel token in shell history, SSH private keys inside
  `~/dashboard-source.zip`. AT-SUIT keeps every secret in its own data
  volume, encrypted where it can be.

---

## 2. Target architecture

```
                         ┌───────────────────────── AT-SUIT server (Docker) ─────────────────────────┐
  Tech laptop (node)     │                                                                            │
  ┌──────────────────┐   │  FastAPI app :8080                                                         │
  │ Browser: /node   │◄──┼─► core      setup wizard, accounts, roles, sites, rooms, settings, licence │
  │  chat, timer,    │WS │   comms     channels, DMs, help requests, attachments (encrypted at rest)  │
  │  captions, help, │   │   timers    server-authoritative room countdowns, presenter views          │
  │  links, overlays │   │   fleet     node registry, heartbeats, kiosk URLs, commands, old-agent API │
  └──────────────────┘   │   captions  audio ingest → ASR worker (sherpa-onnx) → caption fan-out      │
  ┌──────────────────┐   │   overlays  drives AT-LiveOverlay on each laptop over its token API        │
  │ Node agent       │──►│   dashboard link boards (replaces Homarr), live status tiles               │
  │  heartbeat, mic  │   │                                                                            │
  │  audio, commands │   │  SQLite /data/atsuit.db · /data/uploads · /data/models · /data/secret.key  │
  └──────────────────┘   └────────────────────────────────────────────────────────────────────────────┘
  Kiosk screens ────────► /display/<room>, /timer/<room>, /captions/<room> (no login, read-only)
  Companion / OBS ──────► REST API with API keys (timer start/stop, overlay show/hide)
```

**Server does the heavy lifting.** Speech recognition, state, storage, auth,
fan-out, fleet control and every integration run on the server. Adding
rooms or captioned rooms means adding server CPU, not laptop CPU.

**Laptops are nodes: inputs, outputs and a UI.**

- *Input*: the node's microphone or audio interface is the caption source
  for its room. The node only captures, resamples to 16 kHz mono and streams
  PCM over a WebSocket. No model, no Python ASR runtime on laptops.
- *Output*: screens, kiosk pages, the LiveOverlay windows and ScreenTest
  patterns the server tells it to show.
- *UI*: `/node` is the tech's single workspace: their room's chat and the
  all-crew channel, DMs, the room timer with controls, live captions with a
  mic on/off switch, a help button, the room's links (Companion buttons,
  Ontime, kiosk pages), and overlay controls.

**One process, one port, one data volume.** The server is a single FastAPI
app on one port (default 8080), shipped as one Docker image with one compose
service. Optional TLS comes from a bundled Caddy service (see section 6),
which browsers need before they allow microphone access on a page served
from another machine.

**Real-time** goes over one WebSocket per client (`/ws`) with topic
subscriptions (`room:<id>`, `site:<id>`, `captions:<room>`, `timer:<room>`,
`fleet`). Audio ingest uses its own binary WebSocket
(`/ws/audio/<room>`) so caption traffic never queues behind chat.

### Modules

Each module is a FastAPI router plus its tables, registered in
`atsuit/main.py`. A module can be switched off in Admin → Modules, and the
licence can limit which modules are available.

| Module | Replaces | Server owns | Node does |
|---|---|---|---|
| `core` | RoomComms/Presenter setup + accounts, AT Ops login | sites, rooms, accounts and roles (admin, tech, viewer), settings, branding, licence, API keys, audit log | login |
| `comms` | AT-RoomComms 0.9.2/1.0.0, RoomComms Windows client | channels per room and per site, DMs, help requests, attachments, read receipts | chat UI, notifications |
| `timers` | Ontime x20 (phase 3), Ontime views on kiosks | per-room countdown state, messages to stage, presets | timer controls; kiosks show `/timer/<room>` |
| `fleet` | Device Suite, Fleet Dashboard, AT Ops fleet, device-agent-updater | node registry, online state, kiosk URL queue, commands, client release manifest, the old `/heartbeat` `/agent/poll` `/agent/ack` API | heartbeat, applies kiosk URL, runs allowed commands |
| `captions` | AT-LiveCaption | ASR engine and model, vocabulary, transcripts, caption fan-out to audience/overlay pages | streams mic audio |
| `overlays` | Companion → LiveOverlay direct calls | registry of LiveOverlay endpoints and tokens, show/hide/reload/set URL/scene | runs AT-LiveOverlay (unchanged Windows app) |
| `dashboard` | Homarr USER CONTROL | link boards (admin/public), per-room links, status of each node | shows its room's links |
| `presenter` (phase 2) | AT-Presenter | presenter portal, file review, schedule import, room sync | room sync agent |
| `screentest` (phase 4) | AT-ScreenTest | nothing (test patterns are local) | `/screentest` page full-screen on any output |

AT-ScreenTest and AT-LiveOverlay are native Windows apps doing work that has
to happen on the laptop (full-screen patterns, always-on-top windows). They
stay native for now; the server drives LiveOverlay through its existing API,
and ScreenTest gets a browser-based pattern page in phase 4 so kiosks and
non-Windows nodes can use it too.

---

## 3. Data model

SQLite in WAL mode, one file `/data/atsuit.db`, schema versioned by a
`schema_version` table with forward-only migrations in `atsuit/db.py`.

```
sites(id, name, slug, timezone)
rooms(id, site_id, name, short_name, sort, enabled)
accounts(id, username, password_hash, display_name, role, site_id NULL=all, active)
sessions(token, account_id, node_id NULL, created_at, expires_at)
api_keys(id, name, key_hash, scopes, created_at)
settings(key, value)                      -- branding, modules, retention, ...
nodes(id, name, site_id, room_id, kind[tech|kiosk|caption], token_hash,
      ip, mac, version, current_url, last_seen, info_json)
node_commands(id, node_id, kind, payload_json, status, created_at, acked_at)
room_links(id, room_id NULL=site, site_id, label, url, kind, board, sort)
channels(id, site_id, room_id NULL, kind[site|room|dm], name)
messages(id, channel_id, sender_id, sender_name, body_enc, priority, created_at, edited_at, deleted_at)
attachments(id, message_id, original_name, stored_name, mime, size)
message_reads(message_id, account_id, read_at)
help_requests(id, room_id, requested_by, category, description, priority, status, assigned_to, created_at, ...)
timers(room_id PK, title, duration_ms, started_at, paused_remaining_ms, running, message, message_visible, updated_at)
caption_settings(room_id PK, enabled, language, vocabulary, ...)
transcripts(id, room_id, started_at, ended_at, path)
overlay_targets(id, node_id, base_url, token_enc)
audit_log(id, at, actor, action, detail)
```

Message bodies and overlay tokens are encrypted at rest with a Fernet key in
`/data/secret.key`, as RoomComms does today.

---

## 4. Server/node protocol

| Purpose | Endpoint | Auth |
|---|---|---|
| Node enrolment | `POST /api/nodes/enrol {code, name, kind}` → node token | site enrolment code (Admin → Fleet) |
| Heartbeat | `POST /api/nodes/heartbeat` | node token |
| Commands | `GET /api/nodes/commands` / `POST /api/nodes/commands/<id>/ack` | node token |
| Audio | `WS /ws/audio/<room_id>?token=` binary frames of 16 kHz mono int16 | node token or tech session |
| Live updates | `WS /ws?topics=…` | session, node token, or public topics only |
| Agent updates | `GET /api/nodes/agent`, `/api/nodes/agent/file` (also offered in every heartbeat reply) | node token |
| Old kiosk agents | `POST /heartbeat`, `GET /agent/poll/<host>`, `POST /agent/ack`, `/client-update`, `/client-bootstrap` | none (as today), can be switched off |
| Companion / automation | `/api/v1/timers/<room>/start` etc. | API key header `X-API-Key` |

**Nodes pull their own updates.** Every heartbeat reply names the current
node agent version and checksum; an older agent downloads it, checks the
checksum and that it parses, swaps itself in and restarts. The server never
pushes over SSH. Today's device-agent-updater on ATSERVER1 does push over SSH
every 60 seconds and fails on seven laptops and ATPI1 (over 14,000 errors in
its log), which is exactly what pulling avoids: a laptop that's off or
unreachable just updates the next time it checks in. SSH remains only as a
stop-gap for the old Device Suite kiosk agents until they are re-enrolled.

Every existing kiosk keeps working on day one through the old-agent API, so
the fleet can be cut over by pointing the agents at AT-SUIT's address and
port, without reinstalling them.

---

## 5. Data migration

All importers are in Admin → Import (and the `atsuit-import` CLI), run
read-only against copies of the old data, are idempotent, and print a report
of what they took and skipped.

| Source | What comes across | How |
|---|---|---|
| RoomComms `roomcomms.db` + `.encryption_key` (0.9.2 on ATSERVER, 1.0.0 on ATSERVER1) | accounts (password hashes kept), rooms, messages (decrypted with the old key, re-encrypted with the new one), help requests | `docker cp at-roomcomms:/data ./roomcomms-data` then upload the folder as a zip, or run the CLI against it |
| Device Suite `rooms.txt` (only the live copy in `/opt/device-suite`; the older copies in `~` are history) | room kiosk links (Ontime views, Companion emulators) | upload the file; only `ROOM\|URL\|LABEL;` rows, bad rows reported |
| Device Suite `state.json` | known kiosk hosts, IPs, MACs, current URL | upload the file |
| Homarr links (`homarr-links.tsv` export) | dashboard links on ADMIN and USERS boards | upload the TSV |
| AT-Presenter `presenter.db` | (phase 2) sessions, presenters, files | CLI |
| Ontime | stays running; rooms link to it until phase 3 | - |

Cut-over per server, after the side-by-side trial:
1. Back up the old volumes (`docker run --rm -v roomcomms-data:/d -v $PWD:/b alpine tar czf /b/roomcomms.tgz /d`).
2. Import into AT-SUIT, check the import report.
3. Point kiosk agents and the RoomComms Windows client at AT-SUIT.
4. Stop the old container or unit. Keep its volume for 30 days.

---

## 6. Deployment

- **Image**: `python:3.12-slim`, non-root user, health check on
  `/api/health`. Build arg `WITH_ASR=1` (default) adds sherpa-onnx; the
  English streaming model downloads on first start into `/data/models`, or is
  pre-seeded for offline sites with `atsuit-model fetch`.
- **Compose**: one service `atsuit`, one named volume `atsuit-data`,
  port `${ATSUIT_PORT:-8080}`. Optional `tls` profile adds Caddy with
  `tls internal` on 443, so browsers allow the microphone on nodes.
- **Install**: `curl -fsSL …/install.sh | sudo bash` or `./install.sh` from a
  checkout. It checks Docker, writes `.env`, builds or pulls the image,
  starts it, and prints the setup URL. `update.sh` backs up the volume
  before rebuilding. `backup.sh` and `restore.sh` wrap the volume.
- **First run**: the web setup wizard asks for the organisation and site
  name, the first admin account, the rooms, and the licence key (or
  evaluation). Everything after that is configured in the Admin UI on site:
  rooms, accounts, links, nodes, modules, branding, captions, retention.
  No file editing is needed.
- **Side-by-side trial on ATSERVER1**: run AT-SUIT on **port 8180** (and
  8443 for TLS), compose project `atsuit`, volume `atsuit-data`. Nothing
  existing listens there (8000, 4001-4020, 5050, 5070, 16622 and 45876 are
  taken), and nothing existing is touched.

---

## 7. Making it sellable

- **Neutral branding**: product name, logo, colours and support contact are
  settings. Defaults are neutral; "AT" appears only as the product name.
- **Multi-site**: one install can hold several sites (venues), each with its
  own rooms, nodes and enrolment code. Accounts can be limited to one site.
- **Licensing hook**: a licence is a signed JSON document (Ed25519) naming
  the licensee, expiry, max nodes, max sites and enabled modules. The server
  verifies it offline against the vendor public key compiled into the image.
  Without a licence the server runs in evaluation mode (all modules, 5 nodes,
  1 site, a banner). `tools/licence.py` makes the vendor key pair and issues
  licences. Nothing phones home, which suits venues without internet.
- **Repository licence**: this repo is currently MIT, which lets anyone
  resell the code. Selling it means changing to a proprietary licence before
  public releases; that's the owner's decision, so it isn't changed here.
- **Supportability**: `/api/health`, a diagnostics bundle download in Admin
  (versions, settings without secrets, recent logs), audit log, and backups.

---

## 8. Build order

| Phase | Scope | State |
|---|---|---|
| 0 | Design (this doc), repo layout, CI, Docker image, installer | done |
| 1 | Core (setup, accounts, sites, rooms, licence), comms, timers, fleet with old-agent API, dashboard, node UI, captions with server ASR, overlay control, importers for RoomComms, rooms.txt, state.json, Homarr | done in v0.1 |
| 1b | Side-by-side install on ATSERVER1 port 8180, import copies of live data, trial with two laptops in one room | needs the owner's go |
| 2 | Presenter module from AT-Presenter (portal, file review, schedule import via Ollama, room sync) | next |
| 3 | Native timers replace Ontime views on kiosks, Companion module for AT-SUIT (timers + overlays) | |
| 4 | Node desktop wrapper (Windows) bundling node agent + LiveOverlay + ScreenTest; browser ScreenTest page | |
| 5 | Retire old containers per section 5, then Homarr and the Fleet Dashboard | |

### Risks

- Browsers block the microphone on plain `http://` from another machine:
  use the TLS profile or the node agent (Python, no browser) for audio.
- One server running ASR for many rooms: sherpa-onnx streaming Zipformer
  uses roughly one core per active room; the captions page shows load, and
  rooms beyond capacity are refused rather than degrading all of them.
- The old-agent API has no auth (as today); it can be turned off once all
  agents are re-enrolled with tokens.
