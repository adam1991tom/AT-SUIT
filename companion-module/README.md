# AT-SUIT module for Bitfocus Companion

Buttons for the room timer without typing a single URL: presets 3 to 60 minutes, +/- a minute, start/pause/reset/GO, Blink, Clock, Blackout, stage messages and the quick messages (press again to hide), the second line under the timer (a second countdown or a text), AT Overlay on a laptop and caption tests. Buttons light up from the live timer (running, danger, blink, clock, blackout, message) and the time left is a variable.

Variables are named after the connection's label, which is `AT-SUIT` unless you rename it: `$(AT-SUIT:time_left)`, `$(AT-SUIT:running)`, `$(AT-SUIT:title)`, `$(AT-SUIT:message)`, `$(AT-SUIT:cue)` and `$(AT-SUIT:secondary)`. The presets use whatever the label is.

## Install (Companion 3.x, 4.x and 5.x; tested in 5.0.7)

1. Copy this folder into Companion's developer modules folder (Settings, "Developer modules path"). Companion installs its dependencies from `yarn.lock` (Yarn 4, `yarn workspaces focus --production`). To install them yourself on a machine with Node 22: `corepack enable && yarn workspaces focus --production`.
2. Restart Companion, or reload developer modules.
3. Add a connection, **AT-SUIT**. Fill in the server address and port, an API key (Admin, API keys) and the room id.
4. Open the Presets tab: Timer presets, Timer, Display, Messages, Quick messages and Second line are ready to drag onto a page.

The same things are available without the module as plain HTTP calls: see `docs/COMPANION.md`.

## Test

`npm test` runs the checks on the request building. It is built against `@companion-module/base` 1.12.
