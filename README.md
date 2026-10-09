![AT-SUIT](branding/social/readme-banner.png)

# AT-SUIT

One server app for running live events: crew chat, room timers, live
captions, node (laptop and kiosk) management, overlay control and the venue
dashboard. It replaces AT-RoomComms, AT Device Suite / AT Ops, the Fleet
Dashboard, the Homarr "USER CONTROL" board, the per-room Ontime views on
kiosks, and AT-LiveCaption's speech engine.

- **The server does the heavy lifting.** Speech recognition, storage, chat,
  timers and fleet control all run in one Docker container.
- **Every laptop is a node.** Tech laptops run **AT-SUIT Node**, a Windows app
  enrolled once, with their room's chat, the room timer, live captions (their
  mic is the audio source), help requests and the room's links. Each day the
  tech types their name, picks the room and says whether it's the main PC
  (nothing ever pops up) or the backup (silent pop-ups on top of everything).
  No password: the laptop's own enrolment signs them in.
- **Configured on site.** A setup wizard on first run, then everything (rooms,
  people, links, nodes, branding, modules, licence) is set in the console's Venue and Site sections.
  No config files to edit.

## Install

One command on a fresh Linux server (installs Docker if needed). It installs the
newest release from the public releases repository and runs the published image:

```bash
curl -fsSL https://github.com/adam1991tom/AT-SUIT-releases/releases/latest/download/get.sh | sudo bash
```

Or from a clone of the source (for development; the source repository needs access):

```bash
git clone https://github.com/adam1991tom/AT-SUIT.git && cd AT-SUIT
./install.sh                 # http on port 8080
./install.sh --port 8180     # another port, e.g. next to the old apps
./install.sh --tls           # also https on 8443, so browsers allow the microphone
```

Or skip the build and run the published image: `./install.sh --pull` (or
`--pull 0.1.0` for a fixed version). Images are at
`ghcr.io/adam1991tom/at-suit`; make the package public in GitHub, or run
`docker login ghcr.io` on the server first.

Then open `http://SERVER-IP:PORT/` and follow the setup wizard. See
[docs/SETUP-GUIDE.md](docs/SETUP-GUIDE.md) to go from a bare server to a
venue ready for its first show, [docs/INSTALL.md](docs/INSTALL.md) for https, migration from the old apps,
backups and updates, and [docs/ADMIN.md](docs/ADMIN.md) for day-to-day setup.

## Pages

| Page | Who | What |
|---|---|---|
| `/` | admins, techs | console: dashboard, chat, help, timer preview and screens, presenters, captions, nodes, admin |
| `/node` | techs on their laptop | the tech workspace |
| `/timer/<room>` | stage screens | full-screen countdown, no sign-in (`?view=minimal`, `clock`, `backstage`, `preview` (speaker preview), `hcc` (Standard), `overlay`, `built:<name>`; imported Ontime views such as BDNG at `/room/<room>/external/<name>/`) |
| `/screentest` | anyone | display test patterns (colour bars, gradients, checkerboard, geometry, sharpness, motion, overscan, LED tile map) for any screen; no sign-in. Screens can be sent to a pattern from the workspace or Timers → Screens |
| `/screen` | Linux screens | display-only screen: shows the room and view picked on it or routed from the console |
| `/room/<room>/external/<view>/` | screens, OBS | custom Ontime-style views uploaded in Timers → Views |
| `/present/<link>` | presenters | their own page: upload slides, see review notes, check in. No account; each presenter has their own link |
| `/captions/<room>` | audience screens | captions, no sign-in |
| `/captions/<room>/overlay` | OBS / vMix | transparent caption overlay |
| `/api/docs` | integrators | REST API (Companion, automation); Companion set-up and button list in [docs/COMPANION.md](docs/COMPANION.md) |

## Repository

```
server/atsuit/        FastAPI app (modules/, static/ web UI, asr.py speech engine)
server/tests/         pytest suite (set ATSUIT_TEST_MODELS to include the real speech model)
node-app/             AT-SUIT Node, the Windows tech workspace app (Electron; npm test runs it against a live server)
node-agent/           optional Python agent for laptops: heartbeat, commands, mic streaming
screen-agent/         Linux screen agent and install.sh: kiosk Chromium, HDMI rule, self-update
branding/             logo, icons, brand guide and make_brand.py
tools/licence.py      vendor tool to make keys and issue licences
deploy/Caddyfile      https front end (compose profile "tls")
docs/DESIGN.md        architecture, module map, migration and build order
```

## Releases

`ATSUIT` (the default branch) is always releasable and publishes
`ghcr.io/adam1991tom/at-suit:edge`. To release, bump `VERSION`, add a section
to [CHANGELOG.md](CHANGELOG.md), merge, then push a tag `vX.Y.Z` or publish a
release `vX.Y.Z` from the `ATSUIT` branch on GitHub. CI then publishes `:X.Y.Z` and `:latest` and
creates the GitHub release with the Windows app installer, the node agent and
the install files attached. With the `RELEASES_TOKEN` secret set it also
publishes the same release to the public `AT-SUIT-releases` repository, which
is where servers and laptops update from. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

AT-SUIT is proprietary (see [LICENSE](LICENSE)): using it needs a licence key
from AT NET, and nothing works until one is installed. Keys are checked offline
and carry the subscription's end date; there are warnings before it ends and 14
days of grace after, and it never locks during a show. Issuing keys and the
plans are in [docs/ADMIN.md](docs/ADMIN.md#licences-for-resellers).
Versions before 1.0.4 were published under the MIT licence.

## Development

```bash
cd server && pip install -r requirements.txt -r requirements-asr.txt -r requirements-dev.txt
ATSUIT_DATA=./data uvicorn atsuit.main:app --reload --port 8080
python -m pytest -q
```

The workspace's windows are built with Svelte in `web/`. The built files live
in `server/atsuit/static/ui/` and are committed, so the server and the Docker
image need no build step. After changing anything in `web/`:

```bash
cd web && npm ci && npm test && npm run build   # then commit server/atsuit/static/ui
```
