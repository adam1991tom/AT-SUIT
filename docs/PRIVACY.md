# Privacy and data protection

This page is for venues and their clients: what AT-SUITE keeps, where it goes, and
how long it stays. It describes the software as shipped. Each venue that runs it
is the data controller for what its staff put into it.

## Where everything runs

AT-SUITE runs on the venue's own server, on the venue's network. The tech laptops,
stage screens and caption sources talk to that server and to nothing else. There
is no AT-SUITE cloud service: nothing about a show, a room or a person is sent to
the AT-SUITE team.

The server only connects out to the internet for these things, and you can block
all of them without breaking a show:

| What | Where to | What is sent | When |
|---|---|---|---|
| Updates | github.com (AT-SUITE releases) | Nothing but the request for the latest version | Every 30 minutes, or never with updates turned off (Settings → Updates) |
| Speech model | github.com (k2-fsa) | Nothing but the download request | Once, when captions are first set up |
| Venue weather | open-meteo.com | The latitude and longitude of the town an admin picked | Every 15 minutes, only once a town is set |
| Schedule AI (optional) | The Ollama server an admin sets up | The schedule file being imported | Only when someone imports a schedule that needs it |
| AI helper model (optional) | The Ollama server an admin sets up, on your network | The question, the room and the matching venue notes and past fixes | When a tech asks the AI helper |
| AI helper web search (off by default) | api.duckduckgo.com | The question only | When a tech asks something the venue notes don't cover |
| Report a bug | Your own email app | What you type, plus the version and server name | Only when you press Send in your email app |

## Captions

- Speech is turned into text by an offline speech model (sherpa-onnx), **on the
  tech's laptop** when it runs the AT-SUITE app, so only the text goes to the
  venue's server and the audio never leaves the laptop. The app gets the model
  from the venue's own server, not the internet. Browsers, older apps, or a venue
  that turns this off (Settings → General) send the audio to the venue's server
  instead, which does the same work there.
- Audio never leaves the venue's network, and neither does the text. If the venue
  adds helper servers to share the work, audio goes to those, on the same network.
- Audio is never stored. Only the finished caption lines are kept, and only while
  a transcript is being recorded.
- Transcripts are **stored encrypted** with the server's own key, so a copied file
  or a backup can't be read without it.
- Transcripts are **deleted automatically 12 hours after they end** (an admin can
  set 1 to 168 hours in Settings → General). Export them for the client as text,
  SRT or VTT before then. A transcript still recording is never cut short.

## Laptop screen snapshots

Off unless an admin turns them on. A laptop only sends small pictures of its
screens while a manager has that laptop open on the console, the tech sees a
note on their screen the whole time, and the pictures are only ever held in the
server's memory.

## People and passwords

- Staff accounts hold a username, a display name, a role and a password hash
  (never the password itself).
- New accounts and resets use the default password ChangeMe, which has to be
  changed at the first sign-in.
- Forgot password tells the venue's admins and managers. It gives the same answer
  for any username, so it can't be used to find out who works there.
- Every sign-in, failed sign-in, reset and settings change is in the audit log.

## Other things the server keeps

| What | Kept for |
|---|---|
| Crew chat and help requests | Until deleted, or the number of days set in Settings → General |
| Handover notes (encrypted) | Until ticked off, then 30 days |
| Show reports | As long as the venue keeps them |
| Laptop health (CPU, memory, disk, network) | In the server's memory for 15 minutes; never saved |
| Laptop screen snapshots (off by default) | In the server's memory while a manager is looking; never saved |
| Presenter details (name, email, phone) | Until the session is deleted |
| Audit log | On the server, for the venue's own records |

To remove a person's data, delete their account under People and their presenter
entries under Presenters.
