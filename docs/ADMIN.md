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
5. **Admin → Node setup**: the enrolment code. On each laptop open
   `http://SERVER/node`, enter its name and the code, and choose Tech
   workspace, Kiosk screen or Caption source. In Nodes, give each laptop its
   room.
6. **Captions**: turn captions on per room, add vocabulary (speaker names,
   brand names, jargon), choose whether to save transcripts. A tech presses
   "Send my mic" in their workspace, or runs the node agent with `--mic`.
   Put `/captions/<room>` on the audience screen or `/captions/<room>/overlay`
   in OBS or vMix as a browser source.
7. **Timers**: each room has one. Put `/timer/<room>` on the stage screen.
8. **Overlays**: add each laptop running AT LiveOverlay with its address
   (`http://IP:8765`) and the API token from its tray menu → Remote Control.
9. **Admin → General**: product name, colour, logo, which modules are on,
   chat retention, and whether old kiosk agents are accepted.
10. **Admin → API keys**: a key for Companion.

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

`tools/licence.py keygen` makes the vendor key pair once. Put the public key
in `server/atsuit/vendor_pubkey.txt` before building images for customers.
`tools/licence.py issue --licensee "Venue Ltd" --nodes 40 --sites 2 --days 365`
prints a key the customer pastes into Admin → Licence. Licences are checked
offline. Without one, AT-SUIT runs in evaluation mode: every module, one site,
five nodes.
