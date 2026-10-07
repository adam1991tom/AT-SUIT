# Changelog

Versions follow [semantic versioning](https://semver.org). To release, set
`VERSION`, add a section here, merge to main, then push a tag `vX.Y.Z`; the
release workflow publishes the image and the GitHub release.

## 0.1.0

First release.

- One server app: crew chat with DMs, attachments and help requests; room timers with stage screens; server-side live captions with audience and overlay screens; node enrolment, heartbeats and commands; AT LiveOverlay control; link dashboard.
- Setup wizard and Admin pages for everything: sites, rooms, people, links, nodes, modules, branding, licence, API keys, imports, audit log, backups.
- Tech workspace at `/node` for every laptop, with the laptop's mic as the caption source.
- Works with the existing Device Suite kiosk agents (`/heartbeat`, `/agent/poll`, `/agent/ack`).
- Imports from AT-RoomComms (accounts keep their passwords), Device Suite `rooms.txt` and `state.json`, and Homarr links.
- Offline licences, evaluation mode, multi-site.
- Docker image, compose file with optional https, install/update/backup/restore scripts, node agent.
