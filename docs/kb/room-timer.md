# Running the room timer

Each room has one timer with a cue list (the running order). Techs build and run it from their workspace.

## Building the cue list
Add cues and drag them into order with ↑ ↓. Each cue's end action says what happens at zero: keep going into overtime, stop, load the next cue, or play the next one. An Ontime project file can be imported into a room.

## Running the show
**GO** starts the next cue. Quick timers, start and pause, more or less time, messages, blink, clock and blackout are all in the timer window.

## If the network drops
The timer buttons keep working on the laptop. The workspace and the AT Overlay on that laptop show the result at once, and a bar at the bottom says the timer is running on this laptop. When the connection is back, every press goes to the screens as of when it was made. If someone changed the timer at the server during the drop, the latest change wins and you are told.

## Stage screens
Put `/timer/<room>` on a stage screen. Views include minimal, clock, backstage and overlay. The console's **Timers** page previews every room.
