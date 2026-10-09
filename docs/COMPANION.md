<img src="../branding/logo/atsuit-wordmark.png" alt="AT-SUIT" width="200">

# Companion and the AT-SUIT API

Bitfocus Companion (and anything else that can send a web request) drives
AT-SUIT with an API key. A key can do what a tech can: run any room's timer,
show stage messages, work the overlay laptops, test captions, route screens.
It can't change settings, accounts or licences.

> **Easier:** the `companion-module/` folder in the repo is a ready-made Companion module with presets for everything below. See its README. The plain HTTP buttons below keep working.

## 1. Make an API key

1. Sign in as an admin and go to **Settings → API keys**.
2. Give the key a name (e.g. `Companion FOH`) and press **Create**.
3. Copy the key (it starts `ats_`). It is only shown once. If you lose it,
   revoke it and make a new one.

Make one key per Companion machine, so you can revoke one without the other.

## 2. Set up Companion

Add a **Generic HTTP** connection (Connections → Add → "Generic: HTTP Requests").

| Setting | Value |
|---|---|
| Base URL | the AT-SUIT address, e.g. `http://10.100.70.100:8180` |

Every button uses a **POST** action with:

| Field | Value |
|---|---|
| URL | the path from the lists below, e.g. `/api/timers/3/preset/5` |
| Body | the JSON body, or leave it empty |
| Header input | `{"X-API-Key": "ats_your_key_here"}` |
| Content type | `application/json` |

Tip: put the key in a Companion custom variable (e.g. `atsuit_key`) and use
`{"X-API-Key": "$(custom:atsuit_key)"}` in every button, so changing the key
is one edit.

**Room numbers.** Each room has a number. You can see it in the address of the
room's stage screen (`/timer/3` is room 3) or in Timers in the console. The
lists below use room `3`; change it to your room.

To check the key works, open a terminal and run
`curl -X POST -H "X-API-Key: ats_..." http://10.100.70.100:8180/api/timers/3/blink/toggle`.
A wrong key gives `401`, a wrong room `404`.

## 3. Ready-made buttons

### Timer presets

One press loads the timer and starts it, on every stage screen in the room.
No body needed. Warning is at 5 minutes and danger at 1 minute, like the
venue's Ontime presets.

| Button | POST |
|---|---|
| 3 min | `/api/timers/3/preset/3` |
| 5 min | `/api/timers/3/preset/5` |
| 10 min | `/api/timers/3/preset/10` |
| 15 min | `/api/timers/3/preset/15` |
| 20 min | `/api/timers/3/preset/20` |
| 25 min | `/api/timers/3/preset/25` |
| 30 min | `/api/timers/3/preset/30` |
| 35 min | `/api/timers/3/preset/35` |
| 40 min | `/api/timers/3/preset/40` |
| 45 min | `/api/timers/3/preset/45` |
| 50 min | `/api/timers/3/preset/50` |
| 55 min | `/api/timers/3/preset/55` |
| 60 min | `/api/timers/3/preset/60` |

To load a preset without starting it, add `?start=false`
(`/api/timers/3/preset/10?start=false`), then start it with Start/pause.
For any other length, or a title on the stage screen, use
`/api/timers/3/preset` with the body `{"minutes": 7.5, "title": "Q&A"}`.

### Running the timer

| Button | POST | Body |
|---|---|---|
| Start / pause | `/api/timers/3/toggle` | |
| Start | `/api/timers/3/start` | |
| Pause | `/api/timers/3/pause` | |
| Reset (back to the full time) | `/api/timers/3/reset` | |
| Stop (clear the timer) | `/api/timers/3/stop` | |
| +1 min | `/api/timers/3/add` | `{"delta_ms": 60000}` |
| −1 min | `/api/timers/3/add` | `{"delta_ms": -60000}` |
| GO (start the next cue) | `/api/timers/3/go` | |
| Next cue (load, don't start) | `/api/timers/3/next` | |
| Previous cue | `/api/timers/3/previous` | |

### Blink, clock and blackout

Each has `/on`, `/off` and `/toggle`. No body needed.

| Button | POST | What the stage screens do |
|---|---|---|
| Blink | `/api/timers/3/blink/toggle` | the whole timer (and the message) flashes |
| Blink off | `/api/timers/3/blink/off` | |
| Clock | `/api/timers/3/clock/toggle` | the time of day instead of the timer; the timer keeps running underneath |
| Clock off | `/api/timers/3/clock/off` | back to the timer |
| Blackout | `/api/timers/3/blackout/toggle` | blank screen |

Loading or starting a timer (a preset, GO, Next, Start) turns the clock off by
itself. If the room is set to flash at danger, Blink comes on by itself at the
danger time and goes off when the next timer starts.

### Stage messages

The message covers the timer on the stage screens until it is hidden.

| Button | POST | Body |
|---|---|---|
| Wrap up | `/api/timers/3/message/show` | `{"text": "Please wrap up"}` |
| 5 minutes | `/api/timers/3/message/show` | `{"text": "5 minutes"}` |
| Wrap up, flashing | `/api/timers/3/message/show` | `{"text": "Please wrap up", "blink": true}` |
| Show the last message again | `/api/timers/3/message/show` | |
| Hide message | `/api/timers/3/message/hide` | |

The tech workspace has the same as one-tap **quick messages** (Please wrap up,
5/2/1 minutes, Time is up, Please come off stage, Stand behind the mic, Please
speak into the mic, There is an issue, please wait, Please slow down, Questions
from the room next, Please turn your phone off ...). Admins change the list
there (Edit) or with `PUT /api/timers-quick-messages`; `GET` reads it. The
Companion module has each one as a preset that hides the message on a second
press.

### Second line under the timer

A smaller second line under the main timer on the stage views (Standard,
stage, minimal, overlay): either a second countdown or a short text. Imported
Ontime views such as BDNG show it too (as Ontime's secondary message or aux
timer 1). Hidden, it changes nothing on screen.

| Button | POST | Body |
|---|---|---|
| Second countdown, 5 min | `/api/timers/3/secondary/timer/5` | |
| Second countdown, loaded not started | `/api/timers/3/secondary/timer/5?start=false` | |
| Second countdown, any length | `/api/timers/3/secondary/timer` | `{"duration_ms": 90000, "start": true}` |
| Text | `/api/timers/3/secondary/text` | `{"text": "Q&A next"}` |
| Start / pause the countdown | `/api/timers/3/secondary/toggle` | |
| Start / Pause | `/api/timers/3/secondary/start`, `/secondary/pause` | |
| Reset the countdown | `/api/timers/3/secondary/reset` | |
| +1 min on the countdown | `/api/timers/3/secondary/add` | `{"delta_ms": 60000}` |
| Hide the line | `/api/timers/3/secondary/hide` | |
| Show it again | `/api/timers/3/secondary/show` | |

### AT Overlay on a tech laptop

The AT-SUIT Node app can float the room timer over a tech laptop's screen
(clicks go through it). The node number is in Laptops & screens on the console.

| Button | PUT | Body |
|---|---|---|
| Overlay on, laptop 7 | `/api/fleet/nodes/7/overlay` | `{"on": true}` |
| Overlay off, laptop 7 | `/api/fleet/nodes/7/overlay` | `{"on": false}` |
| Overlay as a bottom bar | `/api/fleet/nodes/7/overlay` | `{"on": true, "position": "bottom-bar", "size": "medium"}` |
| Overlay showing a web page | `/api/fleet/nodes/7/overlay` | `{"on": true, "url": "https://..."}` |

Positions: `top-center` (the default), `bottom-right`, `bottom-left`, `top-right`, `top-left`,
`bottom-bar`, `top-bar`. Sizes: `small`, `medium`, `large`. An empty `url`
means the room timer.

### Overlay laptops (AT LiveOverlay)

Overlay laptops are numbered in Laptops & screens → Overlay laptops (`targets/1` is the first).

| Button | POST | Body |
|---|---|---|
| Overlay 1 show | `/api/overlays/targets/1/action` | `{"overlay": "1", "action": "show"}` |
| Overlay 1 hide | `/api/overlays/targets/1/action` | `{"overlay": "1", "action": "hide"}` |
| All overlays hide | `/api/overlays/targets/1/action` | `{"overlay": "all", "action": "hide"}` |
| Overlay 1 to a web page | `/api/overlays/targets/1/action` | `{"overlay": "1", "action": "seturl", "url": "https://..."}` |
| Load a scene | `/api/overlays/targets/1/scenes/load` | `{"name": "Keynote"}` |

### Captions

| Button | POST | Body |
|---|---|---|
| Caption test | `/api/captions/3/test` | `{"text": "Caption check"}` |
| Clear captions | `/api/captions/3/clear` | |
| Start saving a transcript | `/api/captions/3/transcript/start` | |
| Stop saving | `/api/captions/3/transcript/stop` | |

### Screens

| Button | Method and path | Body |
|---|---|---|
| Screen 12 to the Standard view of room 3 | `PUT /api/fleet/nodes/12/screen` | `{"room_id": 3, "view": "hcc"}` |
| Screen 12 to the imported BDNG view | `PUT /api/fleet/nodes/12/screen` | `{"room_id": 3, "view": "view:bdng"}` |
| Identify screen 12 | `POST /api/fleet/nodes/12/command` | `{"kind": "identify"}` |

Views: `stage`, `minimal`, `clock`, `backstage`, `hcc` (shown as "Standard"), `overlay`, `view:<slug>` (imported, e.g. `view:bdng`),
`captions` (audience screen), `captions:overlay`, `captions:bar` (subtitle bar), `screentest:<pattern>` for a display test pattern (`colorbars`, `grayramp`,
`rgbramp`, `checker`, `crosshatch`, `sharpness`, `motion`, `overscan`, `ledmap`, `black`,
`white`, `red`, `green`, `blue`, `gray`), `built:<name>` for views built in Timers → Views, `view:<name>`
for uploaded ones (the BDNG view is `view:bdng` once imported in Timers;
the old `bdng` still works and shows it, or the Standard view if it isn't
imported), or `url:https://...` for any web page.

### Reading values back into Companion

`GET /api/timers/3` (no key needed) returns the room's timer as JSON:
`remaining_ms`, `running`, `playback`, `title`, `message`, `message_visible`,
`message_blink`, `show_clock`, `blackout`, the cue loaded and the next one,
and `secondary` (`mode` timer or text, `visible`, `text`, `remaining_ms`, `running`).
Companion's Generic HTTP module can poll it into a variable.

## 4. Timer actions

`POST /api/timers/{room}/{action}`, body as JSON (every field optional):

| Action | Body | What it does |
|---|---|---|
| `start` | | Start (with nothing loaded, the first cue) |
| `pause` | | Pause |
| `toggle` | | Start or pause |
| `reset` | | Back to the full time, not running |
| `stop` | | Clear the timer |
| `set` | `duration_ms`, `title` | Load a plain countdown without starting it |
| `add` | `delta_ms` | Add (or with a minus number, take off) time |
| `load` | `cue_id` | Load a cue without starting it |
| `go` | | Load the next cue and start it |
| `next` | | Load the next cue |
| `previous` | | Load the previous cue |
| `message` | `message`, `message_visible`, `message_blink`, `blackout` | Set the stage message and its switches in one go |
| `blink` | `on` | Blink on (`true`), off (`false`) or toggle (left out) |
| `clock` | `on` | Time of day on the stage screens: on, off or toggle |
| `blackout` | `on` | Blackout: on, off or toggle |
| `thresholds` | `warn_ms`, `danger_ms`, `flash_danger` | When the timer turns amber and red, and whether it flashes at danger |

Times are in milliseconds: 60000 is a minute.

## 5. Every endpoint an API key can call

Send the key in the `X-API-Key` header. Bodies are JSON with the fields listed
(the full schema of each is at `/api/docs` on the server). Rows marked
"no key needed" also work without one. An API key is never enough for the
admin pages: settings, accounts, sites, licence, backups and keys need an
admin signed in.

### Timers

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/timers/{room_id}` |  | The room's timer: what's loaded, time left, message, blink, clock (no key needed) |
| GET | `/api/timers/{room_id}/cues` |  | The room's cue list (no key needed) |
| POST | `/api/timers/{room_id}/preset` | `minutes`, `start`, `title`, `warn_ms`, `danger_ms` | Load a timer of so many minutes and start it (start=false to load it ready): Companion's 3, 5, 10 ... 60 minute buttons |
| POST | `/api/timers/{room_id}/preset/{minutes}` | `?start=` | The same with the minutes in the address, so a button needs no body |
| POST | `/api/timers/{room_id}/message/show` | `text`, `blink` | Show the stage message (with new text if given) |
| POST | `/api/timers/{room_id}/message/hide` |  | Hide the stage message |
| POST | `/api/timers/{room_id}/secondary/{action}` | `text`, `minutes`, `duration_ms`, `delta_ms`, `start` | The second line under the timer. Actions: text, timer, start, pause, toggle, reset, add, show, hide |
| POST | `/api/timers/{room_id}/secondary/timer/{minutes}` | `?start=` | A second countdown of so many minutes, with no body (for a button) |
| GET | `/api/timers-quick-messages` |  | The ready-made stage messages in the tech workspace |
| POST | `/api/timers/{room_id}/cues` | `cue`, `title`, `note`, `duration_ms`, `time_start`, `timer_type`, `end_action`, `skip`, `colour`, `warn_ms`, `danger_ms`, `custom`, `after_id` | Add a cue |
| PUT | `/api/timers/{room_id}/cues/{cue_id}` | `cue`, `title`, `note`, `duration_ms`, `time_start`, `timer_type`, `end_action`, `skip`, `colour`, `warn_ms`, `danger_ms`, `custom`, `after_id` | Change a cue |
| DELETE | `/api/timers/{room_id}/cues/{cue_id}` |  | Delete a cue |
| POST | `/api/timers/{room_id}/cues/reorder` | `ids` | Put the cues in a new order (send every cue id once) |
| POST | `/api/timers/{room_id}/cues/import` | file upload; `?replace=` | Bring in a room's running order from an Ontime project file (db.json) |
| GET | `/api/timers-views` |  | Every timer view: built in, built in the console and uploaded (no key needed) |
| GET | `/api/timers-views/look/{view}` |  | What a view needs: logos, text, colours and whether the status bar shows (no key needed) |
| GET | `/api/timers-designs` |  | Views built in the console (Timers → Views) (no key needed) |
| POST | `/api/timers/{room_id}/{what}/{state}` |  | Blink, clock or blackout: /on, /off or /toggle, with no body |

### Captions

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/captions/status` |  | Is the caption engine running, and in which rooms |
| GET | `/api/captions/{room_id}/recent` |  | The room's last few captions (no key needed) |
| POST | `/api/captions/{room_id}/test` | `text`, `final` | Push text to the room's caption screens, to check them before the show |
| POST | `/api/captions/{room_id}/clear` |  | Clear the room's caption screens |
| GET | `/api/captions/{room_id}/appearance` |  | How the room's caption screens look: `audience`, `overlay`, `bar` (no key needed) |
| GET | `/api/captions/{room_id}/settings` |  | The room's caption settings: on/off, vocabulary, boost strength, gain, EQ, [MUSIC] and acronym switches, appearance, recording |
| PUT | `/api/captions/{room_id}/settings` | any of `enabled`, `record`, `vocabulary`, `hotwords_score` (0.5-6, whole server), `gain_db` (±20), `eq_band_gains_db` (8 values, ±12), `eq_bands` (`[{"index", "gain_db"}]`), `music_label`, `join_acronyms`, `appearance` (`{"bar": {"font_size": 64, ...}}`), `reset_appearance` (`audience`, `overlay`, `bar` or `all`) | Change them; caption screens update at once. Any tech |
| PUT | `/api/captions/rooms/{room_id}` | `enabled`, `vocabulary`, `record` | Older form of the above (now any tech) |
| GET | `/api/captions/{room_id}/history` |  | The last 200 captions with each word's confidence, newest first |
| POST | `/api/captions/{room_id}/history/clear` |  | Empty that list |
| GET | `/api/captions/{room_id}/corrections` |  | Corrections made in this room, newest first |
| POST | `/api/captions/{room_id}/corrections` | `original`, `corrected` | Log a misheard word and add the right one to the room's vocabulary |
| POST | `/api/captions/{room_id}/transcript/start` |  | Start saving captions to a new transcript file, now |
| POST | `/api/captions/{room_id}/transcript/stop` |  | Stop saving |
| GET | `/api/captions/transcripts` | `room_id` (optional) | Saved transcripts |
| GET | `/api/captions/transcripts/{tid}` |  | Download a transcript |
| GET | `/api/captions/transcripts/{tid}/export.srt` |  | The transcript as SRT subtitles (`export.vtt` for WebVTT) |
| DELETE | `/api/captions/transcripts/{tid}` |  | Delete a transcript that has finished |

### Overlay laptops

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/overlays/targets` |  | Overlay laptops (ATY overlay) |
| GET | `/api/overlays/targets/{target_id}/status` |  | What an overlay laptop is showing |
| POST | `/api/overlays/targets/{target_id}/action` | `overlay`, `action`, `url`, `value`, `seconds`, `degrees` | Show, hide, reload, lock, unlock, live, edit, close, seturl, opacity, refresh or rotate an overlay |
| GET | `/api/overlays/targets/{target_id}/scenes` |  | The overlay laptop's saved scenes |
| POST | `/api/overlays/targets/{target_id}/scenes/load` | `name` | Load a saved scene |

### Nodes and screens

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/fleet/screen-agent` |  | The current Linux screen agent version |
| POST | `/api/screens/pair` | `code`, `room_id`, `view`, `name` (optional) | Add the screen showing that pairing code to a room, showing `view` (techs only; not a screen's own key) |
| GET | `/api/screens/layouts` |  | What a screen can show, grouped: Timer, Captions, Test patterns |
| POST | `/api/screens/pair/request` | `name`, `info` | (Screens) ask for a pairing code; no key needed |
| GET | `/api/screens/pair/status` | `secret` | (Screens) waiting, or paired with the screen's key |
| PUT | `/api/fleet/nodes/{node_id}/screen` | `room_id`, `view` | Route a screen from the dashboard: which room and which view it shows |
| GET | `/api/fleet/nodes` |  | Tech laptops and screens, online or not |
| POST | `/api/fleet/nodes/{node_id}/command` | `kind`, `payload` | Send a node a command: set_url, reload, message, identify, restart_browser (reboot, shutdown and update are for admins) |
| GET | `/api/rooms/{room_id}/overlays` |  | The tech laptops in this room today, and each one's overlay |
| GET | `/api/fleet/nodes/{node_id}/overlay` |  | One tech laptop's AT Overlay: what was asked for and what it shows now |
| PUT | `/api/fleet/nodes/{node_id}/overlay` | `on`, `url`, `position`, `size`, `display`, `opacity` | Turn a tech laptop's overlay on or off, or move it. The laptop's app picks up a node command of kind "overlay" with the full settings |

### Chat and help requests

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/comms/channels` |  | Chat channels you can see, with unread counts |
| GET | `/api/comms/people` |  | People you can message |
| POST | `/api/comms/dm` | `account_id` | Open a direct message (people only, not API keys) |
| GET | `/api/comms/channels/{channel_id}/messages` | `?before=` `?limit=` | Messages in a channel |
| POST | `/api/comms/channels/{channel_id}/messages` | `body`, `priority` | Post to a channel (priority: normal or urgent) |
| DELETE | `/api/comms/messages/{message_id}` |  | Delete a message (admins, or the sender) |
| POST | `/api/comms/channels/{channel_id}/read` | `up_to` | Mark a channel read (no effect for API keys) |
| POST | `/api/comms/messages/{message_id}/attachments` | file upload | Attach a file to a message |
| GET | `/api/comms/attachments/{attachment_id}` |  | Download an attachment |
| POST | `/api/comms/help` | `room_id`, `category`, `description`, `priority` | Raise a help request for a room |
| GET | `/api/comms/help` | `?status=` | Help requests (filter with ?status=open) |
| PUT | `/api/comms/help/{help_id}` | `status` | Set a help request to acknowledged or resolved |

### Dashboard links

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/dashboard/links` | `?room_id=` | Dashboard links (filter with ?room_id=) |
| GET | `/api/dashboard/ping` |  | Is each link answering? Checked from the server, cached for 30 s |

### Presenters

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/presenter/events` | `?archived=` | Events (add ?archived=true for old ones) |
| GET | `/api/presenter/events/{event_id}` |  | The event's running order with every session's readiness |
| GET | `/api/presenter/events/{event_id}/schedule.csv` |  | The event's running order as CSV |
| POST | `/api/presenter/sessions` | `event_id`, `room_id`, `title`, `starts_at`, `ends_at`, `notes` | Add a session |
| PUT | `/api/presenter/sessions/{session_id}` | `event_id`, `room_id`, `title`, `starts_at`, `ends_at`, `notes` | Change a session |
| DELETE | `/api/presenter/sessions/{session_id}` |  | Removes the session and its show files. Its presenters stay, unassigned |
| POST | `/api/presenter/presenters` | `event_id`, `session_id`, `full_name`, `email`, `phone` | Add a presenter |
| PUT | `/api/presenter/presenters/{presenter_id}` | `event_id`, `session_id`, `full_name`, `email`, `phone` | Change a presenter |
| POST | `/api/presenter/presenters/{presenter_id}/new-link` |  | Replace a presenter's portal link (the old one stops working) |
| POST | `/api/presenter/presenters/{presenter_id}/checkin` | `?undo=` | Check a presenter in (?undo=true to undo) |
| DELETE | `/api/presenter/presenters/{presenter_id}` |  | Delete a presenter |
| POST | `/api/presenter/presenters/{presenter_id}/files` | file upload | The AV desk uploads a file for a presenter (from a USB stick, an email) |
| GET | `/api/presenter/files/{file_id}` |  | Download a presenter's file |
| PUT | `/api/presenter/files/{file_id}/review` | `status`, `note` | Approve or reject a file (status, note) |
| GET | `/api/presenter/events/{event_id}/review` |  | Every upload across the event, newest first, with its version number |
| POST | `/api/presenter/sessions/{session_id}/show-files` | file upload; `?kind=` `?label=` | The show's own files for a session (walk-in video, stings, the final deck), in running order. Separate from what the presenter uploaded |
| PUT | `/api/presenter/show-files/{show_id}` | `label`, `kind` | Rename or re-label a show file |
| POST | `/api/presenter/sessions/{session_id}/show-files/order` | `ids` | Put a session's show files in order |
| GET | `/api/presenter/show-files/{show_id}` |  | Download a show file |
| DELETE | `/api/presenter/show-files/{show_id}` |  | Delete a show file |
| GET | `/api/presenter/rooms/{room_id}/schedule` |  | One room's sessions across the current events, for the tech workspace |
| POST | `/api/presenter/rooms/{room_id}/to-timer` | `day`, `replace` | Turn the room's sessions (for one day) into its timer cue list: one cue per session, timed from its start and end, the presenter in the note |
| POST | `/api/presenter/events/{event_id}/import` | file upload | Read a running order. Nothing is saved until the rows are committed |
| POST | `/api/presenter/imports/{import_id}/commit` | `rows` | Save the rows of an imported running order |
| POST | `/api/presenter/imports/{import_id}/discard` |  | Throw an import away |

### General

| Method | Path | Body or query | What it does |
|---|---|---|---|
| GET | `/api/bootstrap` |  | Who you are, the sites, rooms and which modules are on |

The stage pages and feeds (`/timer/<room>`, `/captions/<room>`,
`/room/<room>/external/<view>/`, `/ontime/<room>/ws`) need no key at all.
