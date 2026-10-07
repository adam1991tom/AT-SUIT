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
   links only show to admins. Admin → Import brings in the old Homarr board
   and Device Suite `rooms.txt`.
5. **Admin → Node setup**: the enrolment code and the Windows app. Install
   **AT-SUIT Node** on each tech laptop and enter the server address
   (`http://SERVER:PORT`), the code and the laptop's name. That is a one-off;
   the laptop stays enrolled. Each day a tech signs in and picks the room
   they're in; the choice resets every morning at 05:00 (site time). Kiosks
   can still use `http://SERVER/node` in a browser; give them a room in
   Nodes and they keep it.
   **Pop-ups**: on the backup laptop only, open **This laptop** and tick
   *Show pop-ups on this laptop*. Chat for the tech's room, crew-wide
   messages, direct messages, urgent messages and help calls then appear
   bottom-right, on top of everything including full-screen slides. They
   never make a sound and never take the keyboard. Leave it off on the
   laptop that is on the projector.
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
10. **Admin → General**: product name, colour, logo, which modules are on,
   chat retention, and whether old kiosk agents are accepted.
11. **Admin → API keys**: a key for Companion.

## Remote screens

A screen is a Linux laptop or all-in-one that only shows things: a room's
timer, the backstage running order, captions, a custom view or a web page.

**Install** (once, as the user logged in to the desktop, with an X11
session and automatic login on):

```
curl -fsSL http://SERVER:PORT/screen-agent/install.sh | bash -s -- \
     --server http://SERVER:PORT --code ENROLMENT-CODE --name HD-STAGE-1 --allow-power
```

It installs Chromium, adds the screen under that name and starts it full
screen at every login. Leave out `--allow-power` if the dashboard shouldn't
be able to reboot it. Any browser can also be a screen: open
`http://SERVER:PORT/screen` and enter a name and the enrolment code.

**HDMI rule**: when an HDMI (or DisplayPort, DVI, VGA) display is plugged
in, the picture goes only to that display and the built-in screen goes
dark. Unplug it and the built-in screen comes back.

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

## Companion

Use Companion's Generic HTTP module. Every request needs the header
`X-API-Key: <key>`. Room numbers are in the console address bar
(`/timer/3` is room 3).

| Button | Method and path | Body |
|---|---|---|
| Start/pause | `POST /api/timers/3/toggle` | |
| Reset | `POST /api/timers/3/reset` | |
| Set 20 min | `POST /api/timers/3/set` | `{"duration_ms":1200000}` |
| Add a minute | `POST /api/timers/3/add` | `{"delta_ms":60000}` |
| Stage message | `POST /api/timers/3/message` | `{"message":"Wrap up","message_visible":true}` |
| Overlay show | `POST /api/overlays/targets/1/action` | `{"overlay":"1","action":"show"}` |
| Caption test | `POST /api/captions/3/test` | `{"text":"Caption check"}` |

The full API is at `/api/docs` on the server.

## Licences (for resellers)

The vendor key pair already exists: its public half is in
`server/atsuit/vendor_pubkey.txt` and ships in every image. The private half is
never in the repo; keep it offline and backed up, because losing it means no
new licences can be issued for existing installs. Pass it with `--key`.
`tools/licence.py issue --key <private key> --licensee "Venue Ltd" --nodes 40 --sites 2 --days 365`
(use 0 for unlimited) prints a key the customer pastes into Admin → Licence. Licences are checked
offline. Without one, AT-SUIT runs in evaluation mode: every module, one site,
five nodes.
