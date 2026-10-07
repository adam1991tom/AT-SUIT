# Changelog

Versions follow [semantic versioning](https://semver.org). To release, set
`VERSION`, add a section here, merge to main, then push a tag `vX.Y.Z`; the
release workflow publishes the image and the GitHub release.

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
