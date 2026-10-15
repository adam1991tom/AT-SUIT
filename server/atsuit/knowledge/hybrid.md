# Streaming and hybrid events

## Remote people hear an echo (hybrid events)
Keywords: echo, teams, zoom, webex, remote, hybrid, mix minus, feedback loop, online
1. The call must not hear itself: send the call a mix of the room's mics and playback only, without the call's own audio (a mix-minus).
2. Turn off the meeting laptop's own mic and speakers; take audio in and out through the desk only.
3. In Teams or Zoom, turn off noise suppression and auto gain for the desk input (Zoom: original sound for musicians), so speech isn't cut.

## Remote presenters can't be heard in the room
Keywords: remote speaker, online presenter, teams audio, zoom audio, can't hear remote
1. Route the call laptop's audio output into the desk (headphone out, USB interface or HDMI de-embed).
2. Check the call laptop's output device is the one going to the desk and its volume is up.
3. Ask the remote presenter to use a headset or a good USB mic; test with them at least 30 minutes before.

## Stream dropping frames or buffering
Keywords: stream, obs, vmix, dropped frames, buffering, upload, bitrate, youtube, rtmp
1. Use a wired connection, not Wi-Fi, and check the upload speed is at least twice the stream bitrate.
2. Lower the bitrate (for 1080p, 4 to 6 Mbps is usually enough) or the resolution.
3. Dropped frames in rendering (not network) mean the computer is overloaded: close other apps or lower the output.
4. Record locally at the same time, so there's a full copy whatever happens online.

## Stream has no sound or the wrong sound
Keywords: stream audio, no audio on stream, obs audio, vmix audio, capture card
1. Check the stream software's audio meters while someone speaks.
2. Feed the stream a separate mix from the desk (the room mix is often wrong for online viewers).
3. Make sure only one audio source is live in the stream software, or you'll hear doubled or echoing audio.

## Internet for the event
Keywords: internet, bandwidth, wi-fi, wifi, guest network, wired, speed test
1. Keep production on its own wired network, separate from guest Wi-Fi.
2. Run a speed test at the time of the event, with the room full if you can.
3. Have a backup connection (a 4G or 5G router) for the stream.
