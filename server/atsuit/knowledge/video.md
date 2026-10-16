# Video and projection

## No signal on the projector or screen
Keywords: no signal, no picture, picture gone, no image, blank screen, projector, display, monitor, black screen, nothing on screen
1. Check the right input is selected on the projector or screen, and on any switcher in between.
2. Check the laptop is sending to it: Windows key + P (choose Duplicate or Extend), or on a Mac System Settings → Displays.
3. Reseat the HDMI at both ends; try another cable or adapter. USB-C adapters fail a lot: keep spares.
4. Bring the laptop's resolution down to 1920 × 1080 at 60 Hz, which everything accepts.
5. Unplug and replug the HDMI (or power-cycle the receiver) to force a fresh handshake.

### Ask input: Is the projector or screen set to the input the laptop is plugged into?
- Yes: → laptop
- No: Pick the right input on the projector's remote or the screen's menu, and on any switcher in between. → laptop

### Ask laptop: Does the laptop show the screen in its display settings (Windows key + P, or Displays on a Mac)?
- Yes: The laptop sees it. Choose Duplicate (or Extend), set 1920 × 1080 at 60 Hz, and unplug and replug the cable to force a fresh handshake. → between
- No: The laptop can't see the screen. Try another cable, port or adapter, and Detect in the display settings (see "Laptop doesn't detect the screen").

### Ask between: Is there a switcher, extender or splitter between the laptop and the screen?
- Yes: Plug the laptop straight into the screen to test. If that works, the fault is the box in between: check its input and output, power it off and on, and check the cable runs and extender lights.
- No: Swap the cable, then the adapter. If another laptop works on the same cable, the fault is the first laptop's port or display settings.

## HDMI keeps dropping or flickering
Keywords: flicker, flashing, dropping, sparkles, hdmi handshake, hdcp, intermittent, black for a second
1. Long HDMI runs (over about 10 to 15 m) need an active cable, HDBaseT or fibre extender: a passive cable will drop out.
2. Use the lowest resolution and frame rate that looks right (1080p60 is safest), and turn off HDR on the laptop.
3. HDCP (copy protection) can blank the picture when a laptop plays protected content (streaming services, some video players). Use a local file instead.
4. Loose or worn connectors: tape or lock the HDMI so it can't be pulled.

## Picture is the wrong size, stretched or cut off
Keywords: aspect ratio, stretched, cropped, overscan, 16:9, 4:3, cut off, resolution
1. Set the laptop's resolution to match the screen's native one (usually 1920 × 1080 for 16:9).
2. Turn off overscan (sometimes called "just scan" or "1:1 pixel" on the display).
3. 4:3 slides on a 16:9 screen will have black bars: that's correct, don't stretch them.
4. On the projector, check the aspect setting and the zoom and lens shift before using keystone.

## Colours look wrong or washed out
Keywords: colour, color, washed out, pink, green tint, magenta, purple, dark, contrast, rgb, ycbcr
1. A pink or green picture is usually a bad cable or adapter: swap it first.
2. Washed out or crushed blacks: the laptop and display disagree on RGB full or limited range. Set the graphics driver output to RGB Full, or set the display to match.
3. Check the projector's picture mode (presentation or standard) and that eco mode isn't dimming it.

## Laptop doesn't detect the screen
Keywords: second screen not detected, laptop won't show on projector, wont show on projector, laptop not showing, display not found, mac, windows, extend, duplicate, presenter view
1. Windows: Settings → System → Display → Detect. Mac: hold Option and click Detect Displays in Display settings.
2. Try another cable, port or adapter; restart the laptop with the cable already plugged in.
3. Docks and USB-C hubs sometimes need their own power to drive a display.

### Ask port: How is the laptop connected?
- HDMI: Use a short, known-good HDMI cable straight into the laptop, and restart the laptop with it plugged in.
- USB-C: Not every USB-C port carries video (look for the DisplayPort or Thunderbolt symbol). Try another port, and give the adapter or dock its own power.
- VGA: VGA has no sound and needs the right input picked on the projector. Check the screws are in and no pins are bent, then run the projector's auto-adjust.
- Mini DisplayPort: Push the adapter fully in, and use an active adapter to HDMI on older Macs.

## PowerPoint shows on the wrong screen
Keywords: presenter view, slides on laptop, wrong monitor, notes on projector, swap displays
1. In PowerPoint, Slide Show → Monitor: choose the projector, and tick Use Presenter View.
2. During the show, Display Settings at the top of Presenter View → Swap Presenter View and Slide Show.
3. Keynote: Play → Use Alternate Display (or Option + click Play).

## Video in a presentation stutters or won't play
Keywords: video stuttering, choppy, no video, codec, mp4, embedded video, playback
1. Use MP4 (H.264) for video in slides: it plays everywhere.
2. Embed the video in the file, or keep it in the same folder as the presentation if it's linked.
3. Play it through once before the session so it is cached, with the laptop on mains power and in high-performance mode.
4. If it still stutters, play it from a separate media player on its own output.

### Ask what: What happens when the video should play?
- Black box: The video is missing or linked to a file that isn't there: insert it again from a copy on the laptop's own drive. → codec
- Error: The laptop can't play that file. → codec
- Stutters: Close other apps, plug in the charger, set high-performance mode, and play it once before the session.
- No sound: Check the video's own volume (Playback in PowerPoint) and the laptop's sound output (see "Laptop sound not coming through the PA").

### Ask codec: Is the video an MP4 file (H.264)?
- Yes: Copy it to the laptop's own drive (not a USB stick or network drive), insert it again, and play it in a media player such as VLC to check the file isn't damaged.
- No: Convert it to MP4 (H.264), for example with HandBrake or PowerPoint's File → Info → Compress Media, or play it from VLC on its own output.

## Projector overheating or turning off
Keywords: projector off, overheat, lamp, fan, temperature warning, shut down
1. Check nothing blocks the air vents and the filter is clean.
2. Let it cool fully before turning it back on; don't unplug it while the fan is cooling.
3. Lamp projectors dim with age: note the lamp hours, and keep a spare lamp for long shows.

## LED wall problems
Keywords: led wall, panel, tile, processor, dead pixels, moire, black tile
1. A dark tile or a strip of tiles: check the data and power links to that tile, then swap the tile.
2. The LED processor must be set to the wall's map and the input resolution; check it hasn't lost its settings.
3. Lines or flicker on camera: match the camera shutter to the wall's refresh rate.
4. Keep radio mic antennas away from the wall: it radiates interference.

## Confidence monitor and speaker's screen
Keywords: foldback screen, confidence monitor, downstage monitor, speaker can't see slides, timer screen
1. Feed the confidence monitor with the presenter view or the next slide from the playback machine, not a camera.
2. Put the speaker's timer (an AT-SUITE timer view) on its own screen or as an overlay, where they can see it without turning away from the audience.
3. Test from the speaker's position, not from tech.

## Switching between sources
Keywords: switcher, scaler, matrix, vision mixer, presentation switcher, changeover
1. Fade or cut to a holding slide or the event logo before switching laptops, so the audience never sees a desktop.
2. Set every source to the same resolution so the switcher doesn't need to resync.
3. Have the next presenter's laptop up and tested on the preview output before they walk on.

## Camera and image magnification (IMAG)
Keywords: camera, imag, lens, focus, white balance, follow, ptz
1. White balance the cameras under the show lighting before doors.
2. Match the frame rate across cameras, switcher and screens (50 Hz in Europe, 60 Hz in North America) to avoid flicker.
3. PTZ cameras: save presets for the lectern, the panel and wide, and test recalling them.

## Video flicker on camera from lights or screens
Keywords: flicker on camera, banding, rolling bars, shutter, pwm, frame rate, 50hz, 60hz
1. Match the camera frame rate to the local mains frequency (25/50 fps on 50 Hz power, 30/60 on 60 Hz).
2. Set the shutter to 1/50 or 1/100 on 50 Hz power (1/60 or 1/120 on 60 Hz).
3. Cheap LED lights flicker on camera: raise their output or swap them for ones made for camera.

## Projector lamp or filter warning
Keywords: lamp warning, replace lamp, lamp hours, filter warning, clean filter, lamp light, warning light, flashing light, temperature light, projector warning
1. A lamp warning means the lamp is near the end of its life: it can usually finish the show but may dim or fail. Swap it in a long break, never mid-session.
2. A filter warning: clean or change the filter with the projector off and cool, then reset the filter timer in the menu.
3. A flashing temperature light: clear the vents and the space around it, and check the room isn't too hot. Let the fan finish before unplugging.
4. Laser projectors have no lamp: a warning there usually means temperature or a fault code. Note the code for the supplier.

### Ask which: Which warning is showing?
- Lamp: Keep going if the picture is fine. Note the lamp hours and have the spare lamp ready for the next break.
- Filter: Keep going; clean or change it after the show and reset the filter hours.
- Temperature: Clear the vents and turn eco mode on to cut the heat. If it shuts down, let it cool fully before turning it on again.

## Confidence monitor blank or showing the wrong thing
Keywords: confidence monitor blank, foldback screen blank, downstage monitor no picture, speaker monitor wrong, presenter can't see notes, dsm monitor, stage monitor screen
1. Check the monitor's power and input, and which output feeds it (presenter view, the next slide, or the timer).
2. Slides instead of presenter view means the laptop's displays are swapped: see "PowerPoint shows on the wrong screen".
3. Long runs to the stage need an extender (HDBaseT or fibre): check its lights at both ends.

### Ask blank: Is the monitor blank, or showing the wrong picture?
- Blank: Check power, then the input, then the extender lights at both ends. Plug a laptop straight in at the stage to test the screen itself.
- Wrong picture: Check which output feeds it (the switcher's aux, or the laptop's second output), and swap presenter view if it's showing the slides.
