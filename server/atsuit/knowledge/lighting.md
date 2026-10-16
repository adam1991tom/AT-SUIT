# Lighting and stage

## Lights not responding to the console
Keywords: dmx, lights not working, lights not responding, lighting desk, desk, no response, fixture, console, artnet, sacn, universe
1. Check the console is outputting (not in blind or parked) and on the right universe.
2. Check the DMX cable chain from the console: one faulty cable or fixture can stop everything after it. Terminate the end of the DMX line.
3. Check the fixture's DMX address and mode match the patch.
4. Art-Net or sACN: the node and the console must be on the same network and IP range.

### Ask all: Is it every light, or only some?
- Every light: It's the console or its output. → output
- Only some: Look at where the dead ones sit in the DMX chain. → chain

### Ask output: Is the console outputting (not in blind, preview or parked), on the right universe?
- Yes: Check the console's output: swap the first DMX cable, or check the Art-Net or sACN node has power and a link light, and is on the same IP range as the console.
- No: Leave blind or preview, release anything parked, and turn the output on for the universe the lights are on.

### Ask chain: Do the dead fixtures all come after one point in the DMX chain?
- Yes: The cable or fixture just before them is the likely fault: swap that cable, or bypass that fixture with a barrel (DMX coupler).
- No: Check each dead fixture separately. → signal

### Ask signal: Does the dead fixture's display show it's getting DMX (a dot, a light, or "DMX OK")?
- Yes: DMX arrives, so its address or mode doesn't match the patch: set the fixture to the address and mode the console expects.
- No: Check its power, the DMX cable into it and the one before it, and that any 3-pin to 5-pin adapters are wired for DMX.

## Lights flickering
Keywords: flicker, flashing, strobe, unstable, dimmer
1. Flicker on several fixtures at once: a DMX problem (cable, splitter, or a missing terminator).
2. Flicker on one fixture: check its DMX and power, then swap it.
3. Flicker only on camera: see the video entry on flicker from lights.

## Haze and smoke
Keywords: haze, smoke, fog, fire alarm, detectors
1. Get the venue's permission first: haze can set off smoke detectors.
2. The venue may need to isolate detectors in that zone, with a fire watch in place while they're off.
3. Turn haze off well before the detectors are reset.

## Stage lighting for speakers
Keywords: speaker lighting, lectern light, face light, dark face, shadows, camera
1. Light the speaker's face from the front at about 45 degrees on both sides so there are no deep shadows.
2. Check the lectern and stage positions from the camera and the back of the room.
3. Don't light the projection screen: spill washes the image out.

## Save the lighting show
Keywords: show file, backup, console crash, save show, cue stack
1. Save the show to the console and to a USB stick after every change during rehearsal.
2. Write down or photograph the patch so a replacement console could be set up quickly.

## Fixture not patching or doing the wrong thing
Keywords: patch, patching, wrong address, dmx address, fixture mode, personality, wrong channel, moving head wrong, colour wrong, fixture profile, start address
1. Set the fixture's DMX start address and mode on its own menu to match the console's patch exactly.
2. Overlapping addresses make two fixtures fight: the next fixture's start address must be at least this one's start plus its number of channels.
3. Use the console's profile (personality) for that exact mode: the wrong mode makes pan, tilt and colour do odd things.
4. RDM, if the console and fixtures support it, finds and addresses fixtures without climbing.

## DMX dropouts or lights glitching
Keywords: dmx dropout, dmx glitch, lights jumping, lights random, lights twitching, terminator, splitter, artnet dropout, sacn dropout, data cable
1. Use proper DMX cable (110 ohm). Microphone cable works for short runs but glitches on long ones.
2. Put a terminator in the last fixture's DMX out.
3. Keep to about 32 fixtures per line: use an opto-isolated DMX splitter for more, or for long branches.
4. Keep DMX away from dimmer and power cables.
5. Art-Net or sACN glitches: keep lighting on its own network or VLAN and check the switch isn't overloaded.

### Ask where: Is every fixture glitching, or only some on one line?
- All of them: Check the console's output (or the network node) and the first cable; then the network if you use Art-Net or sACN.
- One line: Check that line's terminator, its splitter output and its fixture count. The fault is usually the cable or fixture where the glitching starts.

## Haze or smoke machine not working
Keywords: hazer not working, haze machine, smoke machine, fog machine, no haze, no smoke, fluid, heating up, pump, not producing
1. Check it has fluid (the right fluid for that machine) and the tube is in it.
2. Smoke machines take minutes to heat up and won't fire until they're ready.
3. Check how it's controlled (DMX address, remote or timer) and that the console is sending a level.
4. Haze needs the venue's permission and any detector isolation first: see "Haze and smoke".

### Ask ready: Is the machine's ready (or heat) light on?
- Yes: It's hot. Check the fluid, the tube and the control, and try its own remote or button. If the pump hums but nothing comes out, it's dry or blocked: call the crew.
- No: Wait for it to heat up. If it never gets ready, check its power and fuse.
