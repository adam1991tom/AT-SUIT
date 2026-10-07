# Changelog

Versions follow [semantic versioning](https://semver.org). To release, set
`VERSION`, add a section here, merge to main, then push a tag `vX.Y.Z`; the
release workflow publishes the image and the GitHub release.

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
