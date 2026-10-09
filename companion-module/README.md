# AT-SUIT module for Bitfocus Companion

Buttons for the room timer without typing a single URL: presets 3 to 60 minutes, +/- a minute, start/pause/reset/GO, Blink, Clock, Blackout, stage messages and the quick messages (press again to hide), the second line under the timer (a second countdown or a text), AT Overlay on a laptop and caption tests. Buttons light up from the live timer (running, danger, blink, clock, blackout, message) and the time left is a variable, `$(at-suit:time_left)` (and the second line, `$(at-suit:secondary)`).

## Install (Companion 3.x or 4.x)

1. On a machine with Node 22: `cd companion-module && yarn install --production` (or `npm install`).
2. Copy this folder into Companion's developer modules folder (Settings, "Developer modules path"), then restart Companion.
3. Add a connection, **AT-SUIT**. Fill in the server address and port, an API key (Admin, API keys) and the room id.
4. Open the Presets tab: Timer presets, Timer, Display, Messages, Quick messages and Second line are ready to drag onto a page.

The same things are available without the module as plain HTTP calls: see `docs/COMPANION.md`.

## Test

`npm test` runs the checks on the request building. It is built against `@companion-module/base` 1.12 and has not yet been loaded into a running Companion.
