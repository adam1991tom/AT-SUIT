# Changelog

Versions follow [semantic versioning](https://semver.org). To release, set
`VERSION`, add a section here, merge to main, then push a tag `vX.Y.Z`; the
release workflow publishes the image and the GitHub release.

## 0.6.4

- **Backstage is a studio clock.** `/timer/<room>?view=backstage` (and any screen set to Backstage) is now a radio-studio style screen: a big clock with sixty second dots filling round the face and the site's time in the middle, an outer ring showing how much of the stage timer is left (amber, then red), and an ON STAGE / PAUSED / OVERTIME lamp. Beside it: exactly what the stage screen is showing (the timer in the Standard style and colours, when it started and ends, and any message, second line, blink, clock or blackout), now and next with planned times, the running order, and the room's help calls. A new help call shows straight away in red with who asked and when, the screen's edge flashes for its first two minutes, and it turns amber once someone is on it; calls open in other rooms are counted underneath. No sign-in needed, like the other timer screens.

## 0.6.3

- **The second line turns off where you turned it on.** *Show text* is now a toggle: it reads *Hide text* while that text is on stage, and pressing it again (or with the box empty) takes the line off. The second line's header has a clear *Turn off* button (*Show again* brings it back), and the countdown controls only show when there is a countdown.
- **Tidier timer screens.** The status bar leaves out what has nothing to say yet (no cue, not started, nothing elapsed) instead of showing dashes, and keeps the end time on the right. In the workspace, the stage message box and its Show / Hide sit on one line.

## 0.6.2

- **Emoji in chat.** A 😊 button beside the message box opens a picker (faces, hands and show-day signs like 🎤 🔇 ⚠️ 🆘) and drops the emoji where the cursor is. Any message can get a reaction: ☺︎+ offers 👍 ✅ 👀 ❤️ 😂 🙏 or the full picker; tap a reaction again to take it away, hover it to see who.
- **Better attachments.** Attach several files at once, or paste or drop them onto the chat; they wait under the box (✕ removes one) and go with the next message, with or without text. Pictures show in the chat, other files show their size. Deleting a message deletes its files from the server too, and so does the automatic clear-out of old messages.
- **Admins can delete direct-message chats.** Admin → Chat lists every direct-message chat (who and how much, never what was said) and deletes one, or all, for both people, with its files and reactions. An admin in a direct-message chat also gets *Delete chat* at the top.

## 0.6.1

- **GO starts what is loaded.** Pressing a quick timer (with *Start on tap* off) and then GO now starts that timer; before, GO moved past it and every screen went to `--:--`. A cue loaded with *Next* also starts on GO, and GO after that moves on as before.
- **Screens and the workspace respond at once.** Every timer button updates the workspace from the server's reply instead of waiting for the live feed, older updates can no longer overwrite newer ones, and the Standard view shows a loaded time before GO instead of a blank.
- **The workspace layout is yours.** Everything on the workspace (room timer, quick timers, messages, second line, cue list, Add a screen, captions, sessions and any pinned window) is a tile. *Layout* turns on editing: drag ⠿ to move a tile, ◂ ▸ ▴ ▾ or the corner to resize it, ✕ to hide it, *Show a hidden part* to bring it back, or pick a ready-made layout (Standard, Compact timer, Big timer). Each laptop keeps its own. The timer tile is smaller by default and its clock scales to the tile.

## 0.6.0

- **Add a screen with a code.** A new Linux screen (or any browser on `/screen`) shows a big six-digit code. Any tech types it into *Add a screen* beside the timer in the workspace and picks what it shows: a timer view, a built or uploaded view, captions (audience screen, transparent overlay or the new subtitle bar), a test pattern or a web page. The screen joins the room and shows it straight away. The screen agent (0.3.0) installs without an enrolment code and goes back to showing a code if it is removed; `--code` still works.
- **Captions work like AT LiveCaption.** Per room, for any tech (Captions → Caption settings, or `/static/captions-control.html`): mic gain and graphic EQ with a live analyser, level meter after processing, custom vocabulary with a boost strength, music/filler noise shown as [MUSIC], spelled-out acronyms joined, a live preview that marks words the engine wasn't sure of, click-a-word corrections that add to the vocabulary, the last 200 captions, transcripts you start and stop at any time with SRT and WebVTT export, and each caption screen's look (font, size, weight, alignment, colours, opacity, lines, position, hold, fade, AI disclaimer), pushed live to the screens. Caption screens use LiveCaption's rolling lines: wrapped to the screen width, long sentences roll on without waiting for a pause, old lines hold then fade. New vocabulary reaches rooms already captioning at their next pause. See `captions-parity.md` in the v0.6 notes for the full comparison.
- **The HCC view is now called Standard** (its id stays `hcc`, so screens and links keep working). Same look, logo still uploadable.
- **BDNG is an imported view**: the client's Ontime view exactly as built (`docs/views/bdng.html`, the original HTML, CSS and JS in one file, no logos inside). Import it in Admin → Timers with one button (or upload the file like any view); it runs unchanged on the room's Ontime-compatible feed, and reads its two logos and bottom text ("BDNG Official Timekeeping Sponsor") from Admin → Timers. Screens still set to `bdng` show it, or the Standard view until it is imported.
- **Timer views: Ontime-style status bar** along the bottom of the stage, Standard, clock and backstage views (time now, running/paused/overtime, the cue, started, elapsed, expected end). On by default, switchable per view in Admin → Timers (and in the view builder), or `?status=0/1` on a screen's address. Standard looks exactly as before above it (checked pixel for pixel against the venue's Ontime view). Imported views can have it too, as a strip over their bottom edge (off unless ticked).
- **Second line under the timer**: a second countdown or a short text, set from the workspace, the API (`/api/timers/<room>/secondary/...`) or Companion. Standard, stage, minimal and overlay show it; so do imported Ontime views like BDNG (secondary message / aux timer 1).
- The **next cue is no longer shown** on the stage views (still in the workspace; a built view can turn it on). The **room name** sits small in the bottom-left corner, only just visible.
- **Workspace timer: quick buttons** for 3 to 60 minutes (start on tap, or load only) and a big **Clock** button; **quick messages** to the speaker (tap to show, tap again to hide, optional blink), editable by admins.
- Captions window: send a test line, clear the screens, links to the caption screen and overlay, engine state, latest transcript.
- Companion module (`companion-module/`): presets 3 to 60, +/-, control, blink, clock, blackout, messages, AT Overlay and caption tests, with live feedback and a time-left variable.
- Admins and techs land on the workspace; the console Dashboard shows live room timers; the Companion guide is served at `/guide/companion`.
- Main PC no longer shows urgent chat toasts.
- **AT Overlay** can sit top centre, now the default, and follows the room the tech signed in to.
- **Screens preview** window: pin live thumbnails of the room's timer views, caption screens and Linux screens.
- **Pin any window** to a dashboard grid under the timer, reorder it and make it wider.
- Workspace **Links** window has search and groups (this room, tools, everything else) for the imported Homarr board.

- **Screen test (from AT-ScreenTest).** `/screentest` is a browser page of test patterns for any display: solid colours with a slow pulse, colour bars, grey and RGB gradients, a pixel checkerboard that inverts, crosshatch and geometry, sharpness and text, a motion line, an overscan border with the 5% safe area, and the LED tile map (rows by columns, tile coordinates, a corner mark to spot a rotated panel, and a highlight that walks tile by tile). Move the mouse for the controls; arrow keys change pattern. A Linux screen or kiosk can be sent to any pattern from the workspace's Screens window, Timers → Screens, or Companion (`screentest:ledmap` and so on).

## 0.5.0

- **AT-SUIT logo and brand.** The app, the Windows app, its tray icon, the favicons and the sign-in pages use the new AT-SUIT logo, and the default accent is the brand orange. `branding/` has the logo files, app icons, social images, the brand guide (HTML and PDF) and `make_brand.py`, which rebuilds them all. Headings use the brand font (Saira), the console sidebar is navy with an orange marker on the current page, pages show the spinning gear while they start, and the Windows app's setup, pop-ups and a new "can't reach the server" page carry the logo.
- **Tech workspace redesign.** The room timer fills the screen. Captions, sessions, screens, overlays and links open as windows you can move, resize and close; the layout is remembered. Chat is a small dock in the corner, and the red **Help** button at the top drops down with the message and the room's open calls.
- **Notifications** for chat, help calls, the timer (warning, danger, overtime), new presenter files, captions stopping and the connection dropping: a bell list and a note in the window, and silent pop-ups on the backup PC. The main PC still shows nothing.
- **Captions** in their own window, with a live overview next to the timer. **Linux screens** in the room can be switched from the workspace to any view or any web page.
- **Timers.** Built-in **HCC** and **BDNG** views; every view follows HCC's warning, danger, blink, message and overtime behaviour. A **Clock** button shows the time of day on every screen. **Blink** and **Blackout** are buttons. A **view builder** in Timers → Views. An **overlay** view for the AT Overlay window.
- **AT Overlay built in.** AT-SUIT Node can float the room timer (or any web page) over the laptop's screen in a see-through, click-through window. Turn it on, move it or change it from any tech laptop in the room, e.g. from the backup PC onto the main PC.
- **Companion.** Presets for 3 to 60 minutes, blink, clock, blackout and stage message endpoints, and [docs/COMPANION.md](docs/COMPANION.md) with ready-made buttons and every endpoint an API key can call.
- **Admin.** Node setup is split by machine (Windows tech app, Linux screens, room sync, older agents) with download links. A new **Info** tab (version, build, nodes, server, storage) with diagnostics to download or copy. The **Licence** tab shows every detail and only admins see anything about the licence. Import, backup and audit are one tab. Logo URL and support contact are gone from General.

## 0.4.1

- **Tech laptops: name, room, main or backup.** No more username and password on AT-SUIT Node: the tech types their name, picks the room and says whether this is the **Main PC** (no pop-ups or notifications at all) or the **Backup PC** (silent pop-ups on top of everything). Chat and help calls carry their name. It resets each morning or on Sign out; Nodes in the console shows who is on each laptop and which it is.

## 0.4.0

- **The venue's Ontime views run as they are**: HCC and both BDNG views were tested against AT-SUIT. Views now live at `/room/<room>/external/<view>/`, so a view's own `?room=` setting (BDNG uses it for the room name) is left alone. Ontime's `/data/settings` is answered too.
- **Flash at danger**: the Ontime automation the venue used (blink the timer at danger, stop blinking when the next timer starts) is built in, per room, and switched on automatically when an Ontime project with that automation is imported. It always flashes its own room, so the SD1 automation that flashed CC can't happen.
- The stage timer blinks the clock too when Blink is on, like Ontime.
- **Presenters** (from AT-Presenter), a new page in the console: events and their running order by day, a link for each presenter (no account) to upload slides from their phone and check in, file review with versions and notes the presenter sees, show files per session in running order (walk-in video, stings, the final deck), schedule import from spreadsheets and CSV (PDF and Word through a local AI model) with a check-and-edit step before anything is saved, and a CSV of the whole schedule.
- **Sessions in this room** in the tech workspace: today's sessions, who's here, their files to approve or reject, the show files, and "Send this day to the timer" to make the room's cue list from the sessions.
- **Room sync** for presentation laptops: `room-sync/atsuit_room_sync.py` (Python only, nothing to install) keeps a folder per session with the approved presenter file and the show files, using the room's code from Presenters → Settings. It removes files that are withdrawn or replaced.
- Licences that list every module of the first release now cover modules added since; new licences can say `*`.

## 0.3.0

- **Cue lists, like Ontime**: each room's timer runs a running order. Add, edit, reorder and skip cues (title, duration, planned start, colour, note, count down / count up / clock); GO, next, previous, load, ±1 minute, stage messages with blink and blackout. At zero a cue can keep going, stop, load the next or play the next, handled by the server. Bring in an existing Ontime project file (v3 or v4).
- **Custom Ontime views**: upload a view's folder (zip with `index.html`) in Timers → Views. It is served at `/external/<view>/?room=<room>` and gets the same live data Ontime sends, so views made for Ontime work unchanged.
- **Remote screens**: Linux laptops and all-in-ones are display-only screens at `/screen`. Pick the room and view on the screen itself (tap the top-left corner 5 times) or route it from Timers → Screens: a timer view, captions, a custom view or any web page.
- **Linux screen agent** (`screen-agent/`): one-line install. HDMI rule: with a display plugged in the picture goes only to that display, otherwise to the built-in screen. Reports name, IP, displays and agent version; restart, update and (optionally) reboot from the dashboard; updates itself.
- Timers page in the console is now a preview of every room plus screen routing; techs run timers from their workspace.
- Overlay control moved to the tech workspace, for that room's LiveOverlay laptops. Admin → Overlay laptops is where they are added.
- Stage timers count down smoothly between updates.

## 0.2.0

- **AT-SUIT Node for Windows**: the tech workspace as an installed app, no browser. A laptop enrols once (or from a `C:\ProgramData\AT-SUIT\node.json` preset for silent roll-outs) and opens straight into the workspace. The captions mic works over plain http, so laptops need no certificate. Closing hides it to the tray; it updates itself from the AT-SUIT server when it next quits.
- **Room of the day**: a tech laptop isn't tied to a room. The tech picks the room after signing in, and the choice resets each morning at 05:00 site time. Kiosks keep the room an admin gives them.
- **Silent pop-ups, backup laptop only**: off by default and switched on per laptop. Pop-ups sit on top of everything, including full-screen slides, never take focus and never make a sound (all app audio is muted, no Windows toasts, no native dialogs). Tested on Windows in CI.
- Admin → Node setup: download link for the Windows app and an upload for new app releases.
- Chat's "Direct message" picker is now in the page instead of a browser prompt.

## 0.1.0

First release.

- One server app: crew chat with DMs, attachments and help requests; room timers with stage screens; server-side live captions with audience and overlay screens; node enrolment, heartbeats and commands; AT LiveOverlay control; link dashboard.
- Setup wizard and Admin pages for everything: sites, rooms, people, links, nodes, modules, branding, licence, API keys, imports, audit log, backups.
- Tech workspace at `/node` for every laptop, with the laptop's mic as the caption source.
- Works with the existing Device Suite kiosk agents (`/heartbeat`, `/agent/poll`, `/agent/ack`).
- Imports from AT-RoomComms (accounts keep their passwords), Device Suite `rooms.txt` and `state.json`, and Homarr links.
- Offline licences, evaluation mode, multi-site.
- Docker image, compose file with optional https, install/update/backup/restore scripts, node agent.
