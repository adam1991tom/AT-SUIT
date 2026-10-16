# Hosting AT-SUITE for customers

How to run AT-SUITE as a hosted service: one cloud server in the EU, each customer venue in its
own sealed AT-SUITE, reached at `https://<venue>.<your domain>`. The laptops at the venue still do
the sensitive work themselves (captions and the timer run on the laptop and carry on if the
internet drops); the cloud server holds chat, the venue's laptops and screens, accounts and
passwords, the licence, updates, the tech guide and the AI helper.

Strict-NDA venues can still run AT-SUITE on their own server, as before (docs/INSTALL.md).

## How it fits together

```
 venue laptops ──https──▶ Cloudflare ──▶ cloud server (Hetzner, EU)
 and screens              (padlock,       ┌──────────────────────────────────────┐
                           blocks          │ front door (Caddy): only Cloudflare  │
                           attacks)        │   grandhall.atsuit.app ─▶ AT-SUITE A  │
                                           │   arena.atsuit.app     ─▶ AT-SUITE B  │
                                           │ each AT-SUITE: own container, data,   │
                                           │   private network, licence, logins   │
                                           │ nightly encrypted backup ─▶ Storage Box
                                           └──────────────────────────────────────┘
```

- **One AT-SUITE per venue.** Every venue runs its own copy of the normal AT-SUITE server in its
  own container, with its own data volume and its own private network. Venues share nothing:
  one venue's sign-in, chat, laptops and data can't reach another's, even from inside the
  server. The containers run read-only, not as root, with a memory and CPU cap each
  (768 MB and one CPU by default; about 70 MB is used while idle).
- **Hosted mode** (`ATSUIT_HOSTED=1`, set by atcloud):
  - A new venue can only be set up from its **setup link**, which holds a one-time code. A stranger
    who finds the address can't claim it.
  - The venue's admins see "Updates are looked after by your AT-SUITE provider". Nothing updates
    by itself; you move venues to a new version with `atcloud update`, which never happens during a show.
  - The server never makes captions (`ATSUIT_ASR=0`): laptops do, with a speech model they fetch
    from their venue once (`ATSUIT_LAPTOP_MODELS=1`). There is one shared, read-only copy of that
    model on the server. New venues start with private captions on, so captions stay on the
    laptops; a venue admin can change that.
  - Caption transcripts are never in backups.
- **Tech guide.** The AI helper's built-in show-day guide (`server/atsuit/knowledge`) ships
  with AT-SUITE, and techs can read it at `/guide/help`. Your own articles for every venue go in
  `/srv/atcloud/kb`, in the same format: Markdown, `# Title`, then one `## Problem` per fix with
  a `Keywords:` line. A file with the same name as a built-in one replaces it.
- **AI helper model (optional).** `atcloud ai on` runs one private model (Ollama, `llama3.2:3b`
  by default) on the server for every venue. Questions never leave your server. Without it the
  helper still lists what has worked before and the matching help articles. It needs about 3 GB
  of memory, so use a server with 16 GB if you turn it on.

## Accounts to create

| What | Where | Roughly |
|---|---|---|
| Cloud server | [Hetzner Cloud](https://console.hetzner.cloud): **CX32** or **CPX31** (4 CPU, 8 GB), Ubuntu 24.04, in Falkenstein, Nuremberg or Helsinki (EU, good for GDPR). Add your SSH key when you create it. | a few £ to £15 a month |
| Backups | Hetzner **Storage Box** BX11 (1 TB), same region. | about £4 a month |
| Domain | Any registrar, or buy it in Cloudflare. A short name, for example `atsuit.app`. | about £10 to £20 a year |
| Cloudflare | Free plan. Add the domain to it. | free |

Check current prices on each site; these are rough. An idle venue uses about 70 MB of memory and
each is capped at 768 MB, so an 8 GB server holds a few dozen venues comfortably.
Start a second server, or a dedicated one for a big client, when it fills up.

## Setting up the server (once)

1. **Create the server** in Hetzner Cloud (above). In Hetzner Cloud → Firewalls, add a firewall
   that allows SSH (22) from anywhere and HTTPS (443) only from
   [Cloudflare's addresses](https://www.cloudflare.com/ips/), and attach it to the server.
2. **Sign in and run the setup script**:
   ```bash
   ssh root@SERVER-IP
   git clone https://github.com/adam1991tom/AT-SUITE.git
   AT-SUITE/cloud/bootstrap.sh --domain atsuit.app
   ```
   It installs Docker, automatic security updates (with no automatic reboots), a firewall,
   SSH keys-only, a swap file, the `atcloud` tool and the nightly backup, then starts the front door.
3. **Cloudflare**:
   - DNS → add an `A` record, name `*` (and one for the bare domain if you like), pointing at the
     server's IP, **Proxied** (orange cloud).
   - SSL/TLS → Overview → **Full (strict)**. Edge Certificates → **Always Use HTTPS** on.
   - SSL/TLS → Origin Server → **Create Certificate** for `*.atsuit.app` and `atsuit.app`.
     Paste the certificate into `/srv/atcloud/certs/origin.pem` and the private key into
     `/srv/atcloud/certs/origin.key`, then run `atcloud edge`.
   - Network → **WebSockets** on (it is by default).
4. **Backups**: `atcloud storagebox u123456` (your Storage Box user name from Hetzner Robot;
   turn on "SSH support" for the box first). It asks for the Storage Box password once to put the
   server's key there. Backups then run every night at 03:30, encrypted, kept 7 days and 4 weeks.
   **Copy `/srv/atcloud/restic-password` into your password manager.** Without it no backup can be read.
5. **Speech model** for laptop captions: `atcloud models` (once, about 200 MB).
6. **The AT-SUITE image**: if the package at `ghcr.io/adam1991tom/at-suit` is private, run
   `docker login ghcr.io` with a token that can read packages. Set the version new venues get in
   `/srv/atcloud/atcloud.env` (`ATCLOUD_VERSION=1.11.0`).
7. **Windows tech app**: `atcloud app --release v1.11.0` puts that release's app where every
   venue's laptops update from.

## Every day

```bash
atcloud new grandhall --name "Grand Hall" --licence grandhall-key.txt
# → prints https://grandhall.atsuit.app/setup#code=…  Send that link to the customer.
atcloud list                       # every venue: running, version, set up, licence, laptops
atcloud info grandhall
atcloud licence grandhall new-key.txt   # renewals and plan changes
atcloud suspend grandhall          # unpaid: their address says paused, data kept
atcloud resume grandhall
atcloud update --all --version 1.11.1   # copy first, health check, rolled back if not healthy;
                                        # a venue with a show on is skipped (run again later)
atcloud app --release v1.11.1      # the matching Windows app for the laptops
atcloud backup                     # a backup now (it also runs every night)
atcloud snapshots grandhall
atcloud restore grandhall --yes    # put the last backup back (or --snapshot ID)
atcloud remove grandhall --yes     # last backup, then the venue and its data are deleted
atcloud check                      # exit 1 if a venue isn't healthy (for monitoring)
atcloud logs grandhall
```

Licence keys are made as before with `tools/licence.py` on your own computer; the venue's
laptop limit comes from its key.

**Monitoring**: point a free uptime monitor (for example UptimeRobot or Better Stack) at
`https://<venue>.atsuit.app/api/health` for each venue.

## On the venue's side

The customer opens the setup link, names the venue and rooms and makes their admin account
(the licence step is skipped when you gave the key with `--licence`). Then, as on their own server:
**Laptops & screens → Add laptops** has the Windows app and the enrolment code, and each laptop
is pointed at `https://grandhall.atsuit.app`. No certificate to install: Cloudflare's padlock is
trusted everywhere, so browsers allow the microphone.

What doesn't work from the cloud: things where the server reaches into the venue's own network
(SSH control of old kiosk machines, and helper servers). Everything the laptops and screens do
themselves works.

## Testing it without any accounts

`cloud/test/e2e.sh IMAGE:TAG` runs the whole kit on one machine's Docker: two venues behind the
front door, kept apart, set up from the setup link, paused and resumed, backed up and restored,
updated and rolled back, and removed. CI runs it on every pull request (`.github/workflows/cloud.yml`).
