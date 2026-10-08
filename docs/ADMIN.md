<img src="../branding/logo/atsuit-wordmark.png" alt="AT-SUIT" width="200">

# Setting up a venue

Everything here is done in the browser. Nothing needs a file edited.

1. **Setup wizard** (first visit): organisation, venue, rooms, first admin,
   licence key (optional).
2. **Admin → People**: add an account for each tech. Techs can chat, run
   timers, send captions and control nodes. Viewers can only read. An
   account can be limited to one site.
3. **Admin → Sites & rooms**: rename, reorder or add rooms. Add a second site
   (venue) if your licence allows it; each site has its own rooms, chat and
   enrolment code.
4. **Admin → Links**: the dashboard. Room links (Companion buttons, Ontime
   views, kiosk pages) show up in that room's tech workspace. Admin-board
   links only show to admins. Admin → Import, backup & audit brings in the
   old Homarr board and Device Suite `rooms.txt`.
5. **Admin → Node setup**: the enrolment code (with Copy and New code) at
   the top, then one tab per kind of machine, each with its download:
   *Windows tech app* (installer, install steps, `node.json` for IT, publish
   a new version), *Linux screens* (the one-line install command,
   `install.sh` and the agent), *Room sync* for presentation laptops (when
   Presenters is on) and *Older agents* (the Python node agent and the
   Device Suite kiosk agent). Install
   **AT-SUIT Node** on each tech laptop and enter the server address
   (`http://SERVER:PORT`), the code and the laptop's name. That is a one-off;
   the laptop stays enrolled. Each day the tech types their name, picks the
   room and chooses **Main PC** or **Backup PC**; no password, the laptop's
   enrolment signs them in, and chat and help calls show their name. It
   resets every morning at 05:00 (site time), or when they press Sign out.
   Nodes in the console shows who is on each laptop and whether it's main
   or backup. Kiosks can still use `http://SERVER/node` in a browser; give
   them a room in Nodes and they keep it.
   **Main PC** (the one on the projector): no pop-ups and no notifications
   of any kind. **Backup PC**: chat for the tech's room, crew-wide messages,
   urgent messages and help calls appear bottom-right, on top of everything
   including full-screen slides. They never make a sound and never take the
   keyboard. Switch between the two in **This laptop**.
6. **Captions**: turn captions on per room, add vocabulary (speaker names,
   brand names, jargon), choose whether to save transcripts. A tech presses
   "Send my mic" in their workspace, or runs the node agent with `--mic`.
   Put `/captions/<room>` on the audience screen or `/captions/<room>/overlay`
   in OBS or vMix as a browser source.
7. **Timers**: each room has one, with a cue list (the running order).
   Techs build and run it from their workspace: add cues, drag them into
   order with ↑ ↓, GO starts the next cue. Each cue's *end action* says
   what happens at zero: keep going into overtime, stop, load the next cue,
   or play the next. An Ontime project file can be imported into a room.
   The console's **Timers** page is a live preview of every room, with no
   controls, plus the list of screens.
   **Custom views** (the HTML views made for Ontime): Timers → Views → upload
   the view's folder as a .zip with `index.html` at the top (or one .html
   file). It shows at `/external/<view>/?room=<room id>` with the room's live
   timer, and appears in every screen's view list.
8. **Screens** (Linux laptops and all-in-ones): see *Remote screens* below.
9. **Overlays**: in Admin → Overlay laptops, add each laptop running AT
   LiveOverlay with its room, address (`http://IP:8765`) and the API token
   from its tray menu → Remote Control. Techs then show, hide and switch
   that room's overlays from their workspace.
10. **Admin → General**: product name, organisation, colour, which modules
   are on, chat retention, and whether old kiosk agents are accepted.
11. **Admin → Info**: everything about this install in one place: version,
   build number and commit, this server (name, addresses, uptime, time
   zone), nodes online by kind and main/backup PCs, apps published, rooms,
   accounts, modules, captions engine, storage and disk space, and the last
   backup. **Download diagnostics** saves it as JSON for a support ticket,
   or **Copy** it; neither includes passwords, keys or tokens.
12. **Admin → Licence** (admins only): who it's licensed to, edition,
   serial, issue and expiry dates with days left, modules, sites and nodes
   used against the limits, whether the signature checks out, the vendor
   key and the key itself to copy. Techs never see licence details.
13. **Admin → Import, backup & audit**: download a backup (database,
   uploads and encryption key), import from the old tools, and the audit
   log of who changed what.
14. **Admin → API keys**: a key for Companion.

## Remote screens

A screen is a Linux laptop or all-in-one that only shows things: a room's
timer, the backstage running order, captions, a custom view or a web page.

**Install** (once, as the user logged in to the desktop, with an X11
session and automatic login on):

```
curl -fsSL http://SERVER:PORT/screen-agent/install.sh | bash -s -- \
     --server http://SERVER:PORT --name HD-STAGE-1 --allow-power
```

It installs Chromium and starts the screen full screen at every login. Leave
out `--allow-power` if the dashboard shouldn't be able to reboot it.

**Pairing (the simple way).** A screen that isn't added yet boots to a big
six-digit code. The tech, on their own laptop in the workspace:

1. finds **Add a screen** next to the timer,
2. types the code from the screen,
3. picks what it shows: any timer view (built in, built in Timers → Views or
   uploaded), captions (audience screen, transparent overlay or subtitle bar),
   a test pattern, or *A web page…*,
4. optionally names it (otherwise the computer's name, or `SCREEN-<code>`),
   and presses **Add screen**.

The screen joins the tech's room and switches to that layout within a couple
of seconds. Any tech can do this; no admin or enrolment code is needed.
Codes last 15 minutes and the screen shows a fresh one by itself. Pairing a
screen again (re-imaged, or removed from the dashboard) reuses its name and
gives it a new key; a screen that is deleted in the console goes back to
showing a code. Any browser can be a screen the same way: open
`http://SERVER:PORT/screen`.

**Enrolment code (scripted installs).** Add `--code ENROLMENT-CODE` (Admin →
Node setup) to the install command and the screen is added straight away
without pairing; on a browser screen, *Use an enrolment code instead* at the
bottom of the pairing page does the same.

**HDMI rule**: when an HDMI (or DisplayPort, DVI, VGA) display is plugged
in, the picture goes only to that display and the built-in screen goes
dark. Unplug it and the built-in screen comes back.

**Captions on a screen**: pick a captions layout when pairing (or later in
the workspace's Screens window). How the captions look (font, size, colours,
lines, position, hold and fade, the AI disclaimer) is set per room by any tech
in Captions → Caption settings → Appearance, separately for the audience
screen, the overlay and the subtitle bar; screens change as soon as it's
saved.

**Choosing what it shows**: on the screen, tap the top-left corner 5 times
(or press P) and pick the room and view. From the console, Timers →
Screens: pick the room and what it shows, or *Web page…* for any address.
The same table shows each screen's address, which display it is using, its
agent version and whether it needs an update, and when it was last seen,
with Identify, Restart (the browser), Update and Reboot buttons.

## Updating laptops

**AT-SUIT Node (Windows app).** Each release on GitHub has
`AT-SUIT-Node-Setup-X.Y.Z.exe`, its `.blockmap` and `latest.yml`. Upload all
three in Admin → Node setup → Windows app release. Laptops download it in
the background and install it the next time the app closes, never during a
show. For a silent roll-out, put
`{"server": "http://SERVER:PORT", "enrol_code": "CODE"}` in
`C:\ProgramData\AT-SUIT\node.json` and run the installer with `/S`; the
app enrols itself under the PC's name on first start. The installer isn't
code-signed yet, so Windows SmartScreen asks once ("More info → Run anyway").

**Node agent (Python).**

Laptops running the node agent update themselves: when the server has a newer
agent (a new AT-SUIT release, or one uploaded in Admin → Node setup), each
agent downloads it on its next heartbeat, checks it and restarts. Start an
agent with `--no-self-update` to pin it.

**Screen agent (Linux).** Same: screens update themselves from the server
on their next check-in after an AT-SUIT update. The Update button in
Timers → Screens makes one check now.

## Presenters

Presenters in the console is the AT-Presenter workflow.

1. **New event**: name, client, colour and dates. An admin makes events; techs work in them.
2. **Running order**: add sessions by hand, or **Import** a spreadsheet or CSV with column headings (Room, Date or Day, Start, End or a "09:00 - 09:45" Time, Session or Title, Speaker or Presenter, Email, Phone). Check and edit the rows, then **Add these sessions**. Rooms not set up at the site are shown in amber and the sessions come in without a room. PDF and Word running orders need a local AI model (Settings → Schedule AI, through Ollama).
3. **Presenter links**: every presenter gets their own link (Copy link). On it they upload their slides from a phone or laptop, see whether the AV team approved them (with the note if not) and tap "I'm here" on the day. **New link** stops the old one working.
4. **File review**: approve or reject each upload. A rejection's note shows on the presenter's page; their next upload becomes a new version.
5. **Show files**: the show's own files per session (walk-in video, stings, the final deck), in running order.
6. **On the day** the tech workspace shows the room's sessions with who's here and their files. **Send this day to the timer** replaces the room's cue list with one cue per session.

**Room sync.** The presentation laptop in each room keeps a folder with every session's approved file and show files. Make the room's code in Presenters → Settings, then on the laptop (Python 3.8 or later, nothing else):

```
python atsuit_room_sync.py --server http://10.100.70.100:8180 --code ABCD-2345
```

The first run saves the settings, so after that `python atsuit_room_sync.py` is enough (put it in the laptop's startup). Download the tool from the link in Presenters → Settings. For the https address with AT-SUIT's own certificate, add `--cafile` with that certificate. Files are stored on the server in `/data/presenter-files`; set `ATSUIT_PRESENTER_FILES` to keep them on a NAS mount instead.

## Companion

How to make a key, set up Companion's Generic HTTP module, a ready list of
buttons (timer presets 3 to 60 minutes, +/−1, blink, messages, clock,
overlays, captions) and every endpoint a key can call:
**[COMPANION.md](COMPANION.md)**.

Every request is a POST with the header `X-API-Key: <key>` (Admin → API
keys). Room 3 here; the room number is in its stage screen address
(`/timer/3`).

| Button | POST | Body |
|---|---|---|
| 5 min, started | `/api/timers/3/preset/5` | |
| Start/pause | `/api/timers/3/toggle` | |
| Add a minute | `/api/timers/3/add` | `{"delta_ms":60000}` |
| Blink | `/api/timers/3/blink/toggle` | |
| Stage message | `/api/timers/3/message/show` | `{"text":"Wrap up"}` |
| Clock | `/api/timers/3/clock/toggle` | |

## Licences (for resellers)

The vendor key pair already exists: its public half is in
`server/atsuit/vendor_pubkey.txt` and ships in every image. The private half is
never in the repo; keep it offline and backed up, because losing it means no
new licences can be issued for existing installs. Pass it with `--key`.
`tools/licence.py issue --key <private key> --licensee "Venue Ltd" --nodes 40 --sites 2 --days 365`
(use 0 for unlimited) prints a key the customer pastes into Admin → Licence. Licences are checked
offline. Without one, AT-SUIT runs in evaluation mode: every module, one site,
five nodes.
