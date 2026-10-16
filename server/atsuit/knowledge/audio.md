# Audio

## Feedback: howling, squealing or ringing from the PA
Keywords: howl, squeal, ringing, whistle, feedback, screech
1. Pull down the fader of the mic that's open (or the master) a few dB straight away. Don't mute everything unless it keeps going.
2. Ask the presenter not to walk in front of the speakers, and to hold the mic closer instead of you adding gain.
3. Mute every mic nobody is using: each open mic adds to the problem.
4. If one tone keeps ringing, cut that frequency on the mic's EQ or the graphic (ring out the room in the next break, not mid-show).
5. Lapel mics feed back first: move the mic up the chest, about a hand's width below the chin, away from the speaker's mouth if they turn their head.

### Ask which: Do you know which mic is feeding back?
- Yes: Pull that channel down a few dB now, mute it if it keeps ringing, and ask the presenter to hold the mic closer and stay behind the speakers. Cut the ringing frequency on its EQ in the next break.
- No: Pull the master down a few dB now. Then mute the open mics one by one: the howl stops when you mute the one causing it. Leave unused mics muted.

## No sound from a microphone
Keywords: mic dead, mic not working, microphone not working, no audio, silent, nothing coming through, lectern mic
1. Check the mic is on (switch on the handheld or bodypack, lectern gooseneck button) and the channel is unmuted with its fader up.
2. Look at the channel's meter on the desk while someone speaks: no signal means the problem is before the desk, signal but no sound means it's after (routing, group, matrix, amp).
3. Condenser mics (lectern goosenecks, choir, lapel into a wired input) need 48V phantom power on that channel.
4. Swap the cable or try another input to find the faulty part. Label anything faulty so it doesn't come back next show.
5. For a radio mic, see the radio mic entries: check the receiver shows RF and audio.

### Ask meter: When someone talks into the mic, does that channel's meter on the desk move?
- Yes: The sound reaches the desk, so the fault is after it. Check the channel isn't muted, its fader is up, it's routed to the main mix (or the group you use) and the master fader is up. → mainmeter
- No: Nothing reaches the desk, so the fault is the mic, its cable or its input. → wireless

### Ask mainmeter: Does the main (master) meter move too?
- Yes: The desk is sending sound out, so the fault is after the desk: the amps or powered speakers are on, the right desk outputs are patched, and nothing in between (a processor or matrix) is muted. If only one speaker is silent, see "One speaker not working".
- No: Check the channel is assigned to the main mix, any group or VCA it goes through is up and unmuted, and no mute group or scene recall has muted it.

### Ask wireless: Is it a radio (wireless) mic?
- Yes: Look at the mic's receiver. → rf
- No: A wired mic needs the right input, a good cable, and 48V phantom power if it's a condenser. → phantom

### Ask rf: Does the receiver show RF (signal bars) when the mic is switched on?
- Yes: The receiver hears the mic but no sound arrives: check the pack or handheld isn't muted, the pack's input gain, the receiver's audio output, and its cable to the desk input.
- No: The mic isn't reaching the receiver: fresh batteries, the mic switched on (not only unmuted), and the mic and receiver on the same frequency (sync them again). Check the receiver's antennas are fitted.

### Ask phantom: Is it a condenser mic (a lectern gooseneck, a choir mic, or a lapel on a wired input)?
- Yes: Turn on 48V phantom power on that channel or the stage box (fader down first, to avoid a pop). If it's on already, swap the cable.
- No: Swap the cable, then try another input, then swap the mic, to find the faulty part. Label anything faulty.

## Radio mic dropping out or crackling
Keywords: radio mic, wireless, rf, dropout, cutting out, sound cutting out, crackle, crackling sound, receiver, bodypack, handheld, lapel, interference
1. Change the batteries first, even if the meter says half. Use fresh batteries for every session; rechargeables at full charge.
2. Check the receiver's RF meter: if it dips as the presenter moves, the antennas can't see the pack. Keep antennas in line of sight, off the floor, away from metal and LED walls, and use the paddle antennas or an antenna distributor if you have one.
3. Make sure the pack's antenna isn't folded or held in the hand, and the pack isn't against sweaty skin.
4. Another system may be on the same frequency: scan and pick a clear one, and coordinate frequencies across every room so neighbours don't clash.
5. Keep a spare handheld on a different, clear frequency ready to hand over.

### Ask rfdips: Does the receiver's RF meter drop when it cuts out?
- Yes: It's range or interference. Get the antennas in line of sight (off the floor, away from metal and LED walls), check the pack's antenna isn't folded or held, and scan for a clearer frequency.
- No: The radio link is fine, so it's the audio: check the mic's cable into the pack (lapels break at the connector), the pack's gain, and the battery. Swap the lapel mic if it crackles when you move its cable.

## Hum or buzz in the sound
Keywords: hum, buzz, ground loop, earth loop, mains hum, 50hz, noise
1. Find which channel it's on by muting channels one by one.
2. A laptop on its charger is the usual cause: try it on battery. If the hum goes, use a DI box with a ground lift, or a ground loop isolator on the laptop's audio.
3. Keep audio cables away from power cables and dimmers, crossing at right angles if they must cross.
4. Never remove the earth from a mains plug to fix hum. Use the ground lift on the DI or isolator instead.
5. A buzz that changes with the lights usually comes from dimmers or LED drivers: re-route the cable or use balanced connections.

### Ask laptop: Is the hum on a laptop or playback channel?
- Yes: Unplug the laptop's charger for a moment: if the hum stops, put a DI box with ground lift (or a ground loop isolator) on the laptop's audio. Never take the earth off a mains plug.
- No: Mute channels one by one to find it, then swap that cable and move it away from power and dimmer cables. A buzz that changes with the lights comes from dimmers or LED drivers.

## Distorted or crackly sound
Keywords: distortion, clipping, crunchy, overloaded, peaking, red light, crackling
1. Check the channel's input meter: if it hits red, turn the gain (trim) down and bring the fader up instead.
2. Shouting presenters or loud playback overload the input: lower gain for that source.
3. Crackle that changes when you move a cable is a faulty cable or connector. Swap it.
4. Laptop playback distorting: set the laptop's volume to about 75 to 80 percent and the app's volume to 100, then set the level on the desk.
5. Distortion only in the speakers, with the desk meters fine, is the amp or speakers being driven too hard: bring the master down.

## Laptop sound not coming through the PA
Keywords: laptop audio, video sound, playback, hdmi audio, no sound from laptop, youtube, embedded video
1. Check where the laptop is sending its sound: on Windows, the sound icon in the taskbar; on a Mac, System Settings → Sound → Output. Pick the output that goes to the desk (headphone jack, USB interface, or the HDMI if audio is taken from the video).
2. Check the laptop isn't muted and the app's own volume is up.
3. If sound is taken from HDMI, the HDMI must go through a device that extracts it (a de-embedder or the switcher's audio out): check it is selected.
4. Video in PowerPoint: make sure the video isn't muted in Playback, and that it's embedded, not linked to a file that's missing.
5. Always test playback with sound at the rehearsal, at show volume.

## Echo or delay in the room
Keywords: echo, delay, reverb, slap back, sound arriving late, lip sync
1. A short echo from the back wall is the room: keep the level down and aim speakers at the audience, not walls.
2. If delay speakers are set, check their delay time matches their distance from the stage (about 3 ms per metre).
3. Sound out of sync with video: set a delay on the audio (or the video processor) to line them up.
4. In hybrid events, echo heard by remote people means the room speakers feed back into the call: see the hybrid event audio entry.

## Presenter too quiet
Keywords: can't hear, quiet, low volume, soft speaker, not loud enough
1. Get the mic closer to the mouth before adding gain: a fist's width for a handheld, lapel high on the chest, gooseneck pointing at the chin.
2. Bring the channel up slowly and watch for feedback.
3. A light EQ lift around 2 to 4 kHz helps speech cut through without adding level.
4. Ask the stage manager to remind them to speak into the mic, or send a quick message to the speaker's timer screen.

## Phantom power (48V)
Keywords: phantom, 48v, condenser, lapel, gooseneck, p48
1. Condenser mics need 48V phantom power from the desk or the stage box; dynamic mics like a SM58 don't need it and aren't harmed by it.
2. Turn the channel's fader down before switching phantom on or off, or you'll get a loud pop.
3. Never use phantom power on a channel with a ribbon mic or an unbalanced device plugged in.

## DI boxes
Keywords: di box, direct injection, laptop audio, unbalanced, long cable, ground lift
1. Use a DI to take a laptop, keyboard or playback device into a mic input: it balances the signal for long cable runs and stops hum.
2. Passive DIs need no power; active DIs need phantom or batteries.
3. The ground lift switch on the DI fixes most hum from laptops on chargers.
4. Use the pad switch if the source is loud and distorts the input.

## Wireless frequency planning
Keywords: frequencies, intermod, rf plan, licence, channel 38, shared spectrum
1. Plan every radio mic, in-ear and comms frequency in the venue together, room by room, so nothing clashes.
2. Use the manufacturer's frequency groups, which avoid intermodulation between systems.
3. In the UK, channel 38 needs a licence (shared, from Ofcom); in other countries check the local rules before the event.
4. Scan on show day with all LED walls and lighting on: they add interference.

## Network audio (Dante) dropouts
Keywords: dante, aes67, network audio, clicks, dropouts, latency, clock, avb
1. Check every Dante device shows the same clock leader (Dante Controller → Clock Status) and nothing is flapping.
2. Use gigabit switches with EEE (green Ethernet) turned off, and QoS set as Audinate recommends.
3. Raise the device latency (1 ms to 2 ms or 5 ms) if there are clicks on a busy or long network.
4. Keep Dante on its own network or VLAN, away from guest Wi-Fi and video.

## Mixing desk lost its settings
Keywords: scene, show file, desk reset, recall, console, snapshot
1. Recall the last saved scene or show file; save the show to a USB stick at the end of every rehearsal and before doors.
2. If a scene recall changed something you didn't expect, check the recall safes and scopes.
3. Keep a printed input list and a photo of the desk's channel strip so you can rebuild quickly.

## Music playback skipping or stopping
Keywords: walk-in music, playback, spotify, skipping, gap, playlist, stops
1. Play from local files, not streaming, so a network drop doesn't stop the music.
2. Turn off crossfade surprises, notifications and sleep on the playback laptop.
3. Have a second source ready (phone or a second laptop) on another desk channel.
4. Check you have the rights to play the music at a public event.

## In-ear monitors
Keywords: iem, in ears, monitor mix, foldback, wedge, stage monitor
1. Start every in-ear mix low and bring it up: a sudden loud mix can damage hearing.
2. Check the pack's battery, the RF meter and that the earpieces are seated.
3. Wedge feedback: turn the wedge down, cut the ringing frequency on the monitor EQ, and point the mic away from the wedge.

## Hearing loop
Keywords: hearing loop, induction loop, t-coil, hearing aid, accessibility, assisted listening
1. Feed the loop from a clean mix of the speech mics (not music at high level).
2. Test it with a loop listener or the T setting on a hearing aid before doors.
3. Put up the loop sign so people know to switch to T.

## No sound at all from the PA
Keywords: no sound, no audio, pa dead, speakers silent, nothing from the speakers, whole system, total silence, no output, sound gone, everything silent
1. Look at the desk's main output meters while something plays: they tell you if the fault is before or after the desk.
2. Check the master fader and its mute, and that nothing recalled a scene or mute group that muted the outputs.
3. Check the amps or powered speakers have power (switched on last, after the desk).
4. Follow the signal from the desk outputs to the speakers: patch, processor (DSP), Dante routes, cables.
5. A tripped breaker or RCD that trips again is a stop: call the crew, don't keep resetting it.

### Ask desk: Do the desk's main output meters move when something plays?
- Yes: The desk is sending sound out. → amps
- No: The desk isn't sending anything. Check the master fader and mute, that sources are routed to the main mix, and that no scene recall or mute group has muted it. If no channel meter moves either, see "No sound from a microphone".

### Ask amps: Are the amps or powered speakers on, with their power lights lit?
- Yes: Power is fine. → signal
- No: Check the mains supply: the switch, the socket or distro, and any tripped breaker. If a breaker trips again, stop and call the crew.

### Ask signal: Do the amps or speakers show a signal light when something plays?
- Yes: Sound arrives at the amps, so check the speaker cables from the amp, and its protect or limit lights. An amp in protect may have overheated or a shorted cable: call the crew.
- No: Sound is lost between the desk and the amps: check the desk's output patch, the multicore or stage box, any processor (DSP) or Dante route, and the cables. Patch to a spare output to test.

## One speaker not working
Keywords: one speaker, single speaker, left side, right side, one side, side fill, delay speaker, sub, subwoofer, speaker dead, one side quiet
1. Check the speaker's power light (powered speakers), or its amp channel's signal and protect lights.
2. Swap its cable with the working side's at the speaker end: if the fault moves, it's the cable or what feeds it.
3. Check the desk isn't panned hard to the other side, and the matrix or processor output for that speaker isn't muted.
4. A silent sub is often fed from its own aux or matrix: check that send.

### Ask lights: What does the silent speaker (or its amp channel) show?
- Power, no signal: Sound isn't reaching it: check the cable from the desk or processor, then that speaker's output on the desk or DSP.
- Signal, no sound: It gets sound but doesn't play it: check the speaker cable from the amp, or swap the speaker. An amp channel in protect needs the crew.
- No power: Check its mains lead and the socket or distro it's on. If a breaker tripped, call the crew before resetting it.

## Gain structure: setting levels
Keywords: gain structure, gain, trim, levels, headroom, setting levels, line check, unity, too quiet, noisy, hiss, preamp
1. Set each input's gain (trim) with its fader at 0 (unity) while the person speaks or the source plays at show level. Aim for peaks about -18 to -12 dBFS: yellow, never red.
2. Then mix with the faders. Don't fix a quiet channel by pushing its fader to the top; add gain instead, if there's no feedback.
3. Keep the master near 0 and set the overall loudness on the amps or processor, so the desk works in its sweet spot.
4. Too much gain distorts; too little gives hiss when you push the faders. Mute channels nobody is using.
5. Laptops and playback: set the device to about 75 to 80 percent volume and do the rest on the desk.

## Mixing desk frozen or not passing sound
Keywords: desk crashed, desk frozen, console frozen, desk not responding, screen frozen, mixer crash, digital desk, faders not moving, stage box offline, mixer fault
1. Don't turn it off straight away: check whether sound is still passing. Most digital desks keep passing audio while the screen is frozen.
2. Check the link to the stage box (the network or digital snake cable and its lights): a lost link silences every input on it.
3. If you must restart, warn the stage and bring the amps or the master down first, then restart and recall the last saved show or scene.
4. Keep the show file on a USB stick so you can load it on this desk or a spare.

### Ask passing: Is sound still coming out of the speakers?
- Yes: Leave it running until the next break if you can, and restart then. Ride the levels with whatever still works (the faders, or the tablet app).
- No: Check the stage box link next. → stagebox

### Ask stagebox: Does the desk show the stage box (I/O) as connected?
- Yes: Recall the last saved scene or show file, and check the inputs are patched to the channels. If the desk still won't respond, bring the amps down, restart it and load the show file.
- No: Reseat the cable between desk and stage box (or the network switch between them), and check the stage box has power. Use a spare cable if you have one.

## In-ear monitor dropping out
Keywords: iem dropout, in ear dropout, in ears cutting out, iem crackle, iem hiss, lost monitor, iem rf, beltpack, earpiece
1. Fresh batteries in the beltpack first.
2. Check the transmitter's antenna (or combiner and paddle) is fitted and can see the stage.
3. Check the pack and transmitter are on the same frequency; scan for a clear one if there's interference.
4. Turn the pack's volume down while you fix it: dropouts can come back as loud bursts.

### Ask kind: Is it hiss and crackle, or does the sound cut out completely?
- Hiss: The radio signal is weak. Move the transmitter antenna so it sees the stage, keep packs off sweaty skin and away from LED walls, and check the transmitter's power setting.
- Cuts out: Look at the pack's RF meter: if it drops, it's range or interference (scan and move frequency); if RF is fine, check the earpieces and their cable.
