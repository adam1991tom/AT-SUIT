# Live captions

Captions are made on the tech's own laptop from their microphone, by an offline speech model. Audio never leaves the laptop; only the words go to the screens.

## Starting captions
Captions are turned on per room by an admin. In your workspace, press **Send my mic**. The first time, the app fetches the speech model (about 200 MB) in the background.

## Names and jargon are wrong
Add speaker names, brand names and jargon to the room's vocabulary. Changes reach the laptop straight away, even mid-show.

## Showing captions
Put `/captions/<room>` on an audience screen, or `/captions/<room>/overlay` in OBS or vMix as a browser source.

## If the network drops
The laptop keeps listening. What is said meanwhile is sent as soon as the connection is back, so the transcript has no gap.
