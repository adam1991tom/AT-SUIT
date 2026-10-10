# Changelog

Versions follow [semantic versioning](https://semver.org). To release, set
`VERSION`, add a section here, merge to `ATSUIT`, then push a tag `vX.Y.Z` (or
publish that release on GitHub); the release workflow publishes the image and
the GitHub release.

## 1.0.7

**Updates**
- Automatic updates work on a server installed from a git checkout. The update timer runs as root, and git refused to fetch the new release into a checkout owned by another user ("update.sh stopped at line 62"). It now tells git the checkout is safe.

## 1.0.6

**Workspace**
- Minimising a docked window, or one whose part isn't in use on this site, no longer leaves an empty box where it was. The windows beside it grow to fill the space.
- The docking guides that appear while dragging a window are drawn in their proper cross again. The top and middle guides had picked up the page's own styles, so they sprawled over the windows underneath.

## 1.0.5

**Captions**
- Vocabulary works. The speech engine had been throwing every vocabulary word away as it loaded, so names like "Harrogate" were never boosted. They now steer what it hears: tried with spoken names, "Ocado", "Priya" and "Wolseley" came out right once they were in the vocabulary. It helps most when the spelling is close to how the word sounds.
- Any vocabulary word the engine still can't use is listed under the vocabulary box, instead of being dropped without a word.
- New per-room switch: **Sentence case instead of capitals**. Captions read "Welcome to Harrogate" instead of "WELCOME TO HARROGATE"; vocabulary words keep their own spelling ("BBC", "iPhone") and joined acronyms stay in capitals.

**Companion module**
- Ships `yarn.lock` (Yarn 4), which Companion needs to install the module's dependencies itself.
- Works in Companion 5 (tested in 5.0.7). The README now names the variables correctly: they use the connection's label, `$(AT-SUIT:time_left)`, and the presets follow the label if it's renamed.

## 1.0.4

**Licence keys and subscriptions**
- AT-SUIT needs a licence key to work. Without a valid key only the Licence page works: the workspace, screens, timer and caption pages say "not licensed", the API answers 402 and live connections are refused. Pasting a key unlocks it at once, and screens come back by themselves. Evaluation mode is gone.
- Subscription keys carry their end date. Admins see a warning in the console and the workspace 30 days before the end; everyone sees it in the last 7 days.
- After the end there are 14 days of grace with everything working. Then AT-SUIT locks, but never during a show: the lock waits for a restart or 02:00 to 05:00 (site time) with no timer running or paused and no captions live. Nothing is deleted.
- Winding the server's clock back doesn't revive an expired key.
- Servers set up before 1.0.4 without a key get 14 days to add one; servers with a key carry on as before.
- Only the vendor key built into AT-SUIT is trusted (the `ATSUIT_VENDOR_PUBKEY` setting is gone).
- `tools/licence.py issue` takes a plan (`small`, `venue`, `large`, `enterprise`, `trial`, `event`, `owner`), a length in months or an end date, and adds 7 spare days to paid periods. Keys get a serial.
- The licence is now proprietary (see LICENSE). Earlier versions were MIT.

**Releases from a public releases-only repository**
- Servers and laptops look for updates in `adam1991tom/AT-SUIT-releases`, which holds only the releases (no source), so the source repository can be private and nobody needs a token. Until that repository has a release, they ask the source repository as before.
- Each release carries `at-suit-install.tar.gz` (install files only) and `get.sh`. The one-line install is now `curl -fsSL https://github.com/adam1991tom/AT-SUIT-releases/releases/latest/download/get.sh | sudo bash`, and installs the published image.

## 1.0.3

**Speaker preview**
- It's a command-centre monitor now: every room's help calls are treated the same (open ones first, newest first), each labelled with its room. The "This room" badge is gone.
- Notifications show important and urgent messages to all crew and to any room, not only the screen's own room.

**Chat**
- A message that arrives while a chat is opening no longer goes missing until the next reload.

**Releases and updates**
- The server checks GitHub every half hour (was every three hours), so a new release shows up soon after it's published.
- Settings → Updates says **Ahead of GitHub** when the server runs a newer version than the newest release, instead of "Up to date".
- The default branch is now `ATSUIT`. Checks, builds and the install script follow it; with no release yet, `get.sh` and `update.sh` take the default branch whatever its name.
- A release published on GitHub's Releases page builds and attaches the Windows app and release notes by itself.
- A server installed from a git checkout updates to a release even when that release isn't straight ahead of its copy (for example after a squash merge), as long as nobody has edited its files by hand.

## 1.0.2

**The Windows app shows its version and updates on start**
- The version is in the workspace top bar, in **This laptop** and in the tray menu.
- **This laptop** says whether the app is up to date and has **Check for updates**, then **Update now** once a new version has downloaded. The tray menu has the same.
- When the app starts and finds a newer version on the server, it installs it and restarts once, with a notice, before the day's work. A tech can turn this off in This laptop. A version found later still waits for Update now or for the app to close, never interrupting a show.
- CI's update test now checks the real thing: the app finds the new version as it starts, installs it and comes back by itself.

**The server updates itself from GitHub**
- The server checks the newest GitHub release every half hour. **Settings → Updates** chooses **Install by itself** (the default), **Tell me only** (with an **Install now** button) or **Off**, and shows the newest release, what's new and how the last update went.
- It never installs while a show is on, meaning a timer that is running or paused in any room.
- Installing is done on the host by `update.sh --auto`, run every 10 minutes by a systemd timer that `install.sh` sets up (`sudo ./install.sh --updater` for an existing install). It is the same safe update: backup, health check and roll back. A version that failed isn't retried by itself.
- After updating, the server fetches the same release's Windows app and publishes it, so laptops keep updating from the server without internet. A laptop only goes to GitHub itself when the server has no app.
- A private repository needs a read-only GitHub token in Settings → Updates. It stays on the server.

## 1.0.1

**From the first show-day test**
- The overlay timer fills its box. The time of day, the cue title and the second line sit small in the corners; across a bar, the title and second line are on the left and the clock on the right.
- A new screen view, **Speaker preview**: the studio clock on the left; every help call on the site (this room's first) and the crew's important and urgent messages on the right. No stage timer. Pick it like any view, or open `/timer/<room>?view=preview`. Backstage is unchanged.
- A new chat message stands out in the workspace: the Chat window glows, the message lights up for a few seconds, and while it's unread the Chat tab and taskbar button turn the accent colour and pulse.

**Fixes**
- The Help button sends one call per press. It waits for the server before it can be pressed again, a held Enter key doesn't repeat it, and the server treats the same call from the same person within 30 seconds as the one already sent.

## 1.0.0

**Ready to sell**
- One-step install on a fresh Linux server: `curl -fsSL …/get.sh | sudo bash` installs Docker if needed and AT-SUIT in `/opt/at-suit`.
- Safe updates: `update.sh` backs up, starts the new version and waits for it to be healthy; if it isn't, it restores the backup and the previous version by itself.
- CI installs the Windows app, publishes a newer build on a server and checks the app updates itself to it.
- A setup guide (docs/SETUP-GUIDE.md) and a printable tech quick-start card at `/guide/tech`, linked from Laptops & screens → Add laptops.
- A show-day test runs a whole venue day through the server: managers, laptops, screens, timers, chat, help, captions, overlays, backup, audit and sign-out.

**Fixes**
- Renaming a laptop or screen no longer takes it out of its room.
- Only tech laptops can be set to Main or Backup PC.

## 0.9.0

**The console is in clear sections**
- The side bar is grouped into **Live** (Dashboard, Chat, Help requests, Timers, Presenters, Captions), **Venue** (Rooms, Laptops & screens, People, Links) and **Site** (Settings, Licence, Servers, Backups, Audit log). The one big Admin page with thirteen tabs is gone.
- **Laptops & screens** brings the list of every laptop and screen together with adding laptops and the overlay laptops, under one heading.
- **Settings** holds General, Look (themes), Chat, API keys and About. The modules have plain names (Chat & help requests, Laptops & screens…).
- **Backups** and the **Audit log** are separate pages; managers can read the audit log.
- A manager only sees the pages they can use. A page that is off or not theirs opens the first one that is.
- Old links and bookmarks to `#/admin/...` and `#/fleet` open the matching new page. Every hint that said "Admin → …" now names the new page.
- Works on a phone: the side bar becomes a scrolling strip and each section's tabs scroll sideways.

## 0.8.0

**The workspace works like Windows**
- Every part of the tech workspace is now a window: the timer, quick timers, messages, second line, cue list, chat, captions, add a screen, sessions, screens, previews, overlays and links.
- Drag a window by its title onto the guides to dock it beside another, add it as a tab, or put it along an edge; let go anywhere else and it floats. Floating windows resize from any edge; docked ones share the screen with bars you can drag.
- Minimise, maximise (or double-click the title) and close on every window, and a taskbar along the bottom with a button for each.
- **Pop out** any window into its own window for a second monitor. It keeps running exactly as it was (chat stays live, the timer keeps ticking). In the AT-SUIT app it is a real app window that can stay on top; closing it puts it back.
- **Layouts**: save arrangements under a name and switch with one click; each laptop remembers its last one. An admin can make their layout the starting one for every laptop.
- On a phone or a narrow window everything stacks in one column.
- The chat box is a window like the rest; its taskbar button counts new messages while it is out of sight.

**Under the hood**
- The new screens are built with Svelte (`web/`), starting with the workspace's window manager. The built files are committed, so installing the server needs no build step; CI checks they match the source.
- The old tile board and floating-window code are gone.

## 0.7.2

**Helper servers**
- Add more machines to share the work. The main server stays the brain (rooms, people, screens); a helper runs the same install with `ATSUIT_ROLE=helper`, joins with a one-time code from Admin → Servers, and takes live caption speech recognition off the main server.
- Admin → Servers lists every server with its status, speech engine, rooms captioning and CPU, and chooses **Share** (least busy server wins) or **Offload** (helpers first). Admin only.
- If a helper goes offline mid-show, its rooms move to another helper or the main server within a second; the laptops carry on as before.
- Helpers connect out to the main server, so they need no open ports, and reconnect on their own after a restart. Removing one disconnects it and it needs a new code to come back.
- Vocabulary changes reach every helper.

## 0.7.1

**Appearance (site-wide, admin only)**
- New Admin → Appearance page sets one look for the whole site: Dark, Light, Modern or Futuristic, plus spacing (compact, normal, roomy), corners (square, rounded, round), font (system, brand, rounded, mono) and animation on or off.
- Changes preview live; "Save for everyone" applies them to the console, every tech workspace, setup, the guide, presenter and captions control. Techs and managers can't change it.
- Each screen remembers the last look so it paints in the right theme straight away.
- Changing the look is recorded in the audit log.

## 0.7.0

The bug-sweep release: every screen was gone through as admin, manager, tech and stage screen, and 58 of the 61 problems found are fixed. Still open: the top bar wraps on 1400px screens, the console Timers page is wide on a phone (both go with the rebuild), and the Companion module keeps port 8180 on purpose (the documented port next to the old apps).

**Stage screens**
- Long stage messages shrink to fit the screen instead of running off it; the second line keeps to two lines (one in the overlay); a long cue title no longer squeezes the status bar's times.
- Hiding a message also stops the blink; the Clock view never pulses.
- Backstage shows the venue's date (not the screen's own time zone), is easier to read in portrait, and keeps a long message to three lines.
- Every clock screen re-checks the venue time every 5 minutes, so it stays right across a clock change.
- Caption screens no longer replay old lines as new after a reload or reconnect.
- Screen test: bad values in the address can't hang the browser.

**Security**
- Cue notes stay with the crew: public screens, the Ontime feed and the live timer feed leave them out.
- The presenter upload link checks the link and the file size before reading the file.
- Sign-in slows down after 8 wrong passwords from one address (for 5 minutes), and every failed sign-in is in the audit log.
- A manager kept to one site only sees and changes that site's rooms, people, links and laptops.
- An empty "Install licence" no longer removes the licence; there is a separate *Remove licence* button that asks first.

**Console**
- Time zones are checked (and picked from a list) in setup and Sites; a wrong one used to make every clock show UTC.
- Chat unread counts are kept on the server: the Chat badge lights on every page and survives a reload, in the console and on tech laptops.
- A link for a room is saved to that room's site; managers can't make admin-board links.
- Pages for a module that is turned off open the next page instead of a blank one.
- Duplicate room names and usernames say so instead of "Internal Server Error"; a file that isn't a RoomComms database gets a clear message; an event can't end before it starts.
- Publishing the Windows app needs the .exe and latest.yml together (an .exe alone used to be deleted).
- Deleting a link, an overlay laptop or a chat message, and making a new presenter sync code, now ask first; link and overlay changes are audited.
- Error messages name the field in words ("Time start: use a time like 9:30").
- Buttons that need a file or a version say so instead of doing nothing; your own role and Active box can't be changed by mistake; managers don't see Update buttons they can't use.
- Wide tables scroll inside their panel on a phone.

**Tech workspace**
- Windows open below the top bar (it wraps on smaller screens), and unpinning one puts it back at its own size.
- The open chat box no longer covers the board; on a phone it starts folded and the top bar scrolls away.
- The Screens window refreshes by itself when another tech or the console changes a screen.
- "Send day to timer": a session without an end time runs until the next one starts (or counts up), instead of a 0:00 cue that skipped at once; a 0:00 cue never skips on by itself.
- GO on the last cue says "That's the last cue" and leaves the stage alone (Stop stops).
- A quick message with Blink unticked no longer turns the Blink switch off.
- Help: you aren't alerted about your own call or told you're on your own way, and the caller sees "Help call sent" rather than "on the way" before anyone answers.
- An urgent chat message shows one alert, not two; Overlays only says "sent" when that laptop can show it.
- The stage message sends on Enter and won't show an empty message; deleting a cue asks first; cue rows fit a phone.

**Windows app**
- A 502/503 page from a proxy (while the server restarts) is retried like an unreachable server.

**Companion**
- "Danger" feedback is only lit for a running or paused count-down under its danger time; a stopped timer shows `--:--`.

## 0.6.7

- **New Manager role**, between Tech and Admin. A manager can add, rename and remove rooms, add and edit tech and viewer accounts, move laptops and screens between rooms (Nodes), edit links and presenter events, edit the quick speaker messages, and read the audit log. A manager can't change site settings, the licence, branding, modules, timer views or designs, add or remove nodes, see enrolment codes, make API keys, take backups or import data, and can't create or change admins or other managers. In the console a manager sees Admin → Rooms, People, Links and Audit log.
- Renaming a room is now in the audit log.

## 0.6.6

- **A tech laptop stays in its room for the day.** Once a tech has picked the room, the start screen only offers that room and the workspace's room picker is locked, even after signing out and in again. The server refuses any other room from a tech laptop (403) until the next working day. Only an admin moves a laptop, from the console (Nodes).
- **The console is admin-only.** Tech accounts and tech laptops go to the workspace; a tech who signs in on the console is signed straight out. On a tech laptop the Console button is now *Admin*: an admin signs in there with a short session (30 minutes, ended when the browser closes), and the tech's day carries on underneath.
- **Timer views menu.** The *Standard ↗* and *Backstage ↗* links are gone; the cue list and the console's Timers page have one *Timer views ↗* menu with every view (Standard, Backstage, Clock, Minimal and any imported view).
- **The Help button flashes** while a help call is open, in the workspace and on the console's *Help requests* link, until someone presses *On my way*.

## 0.6.5

- **No separate Stage view: the stage screen is Standard.** `/timer/<room>` and the *Standard ↗* links open the Standard view; screens and links still set to `stage` show Standard too. Stage is gone from every view list.
- **The Clock view is just the clock.** The time of day with a small *Current time* above it, and the stage message when one is showing. No room name, no status bar, no second line, no progress bar.
- **Screens preview lists each output once.** The caption screens (audience screen, overlay, subtitle bar) are all under *Captions*, not repeated under *Timer views*; the subtitle bar and overlay previews open the right caption layout; earlier pins carry over.
- **The overlay stays where it was put.** The app puts the overlay window back in its place once it has shown and loaded (Windows can move a new transparent window), and tells the server where it really is. In the workspace's Overlays window, a laptop whose app is too old for top centre (before 0.6.0, which put it bottom right) says so, as does a laptop showing it somewhere other than asked.

## 0.6.4

- **Backstage is a studio clock.** `/timer/<room>?view=backstage` (and any screen set to Backstage) is now a radio-studio style screen: a big clock with sixty second dots filling round the face and the site's time in the middle, an outer ring showing how much of the stage timer is left (amber, then red), and an ON STAGE / PAUSED / OVERTIME lamp. Beside it: exactly what the stage screen is showing (the timer in the Standard style and colours, when it started and ends, and any message, second line, blink, clock or blackout), now and next with planned times, the running order, and the room's help calls. A new help call shows straight away in red with who asked and when, the screen's edge flashes for its first two minutes, and it turns amber once someone is on it; calls open in other rooms are counted underneath. No sign-in needed, like the other timer screens.

## 0.6.3

- **The second line turns off where you turned it on.** *Show text* is now a toggle: it reads *Hide text* while that text is on stage, and pressing it again (or with the box empty) takes the line off. The second line's header has a clear *Turn off* button (*Show again* brings it back), and the countdown controls only show when there is a countdown.
- **Tidier timer screens.** The status bar leaves out what has nothing to say yet (no cue, not started, nothing elapsed) instead of showing dashes, and keeps the end time on the right. In the workspace, the stage message box and its Show / Hide sit on one line.

## 0.6.2

- **Emoji in chat.** A 😊 button beside the message box opens a picker (faces, hands and show-day signs like 🎤 🔇 ⚠️ 🆘) and drops the emoji where the cursor is. Any message can get a reaction: ☺︎+ offers 👍 ✅ 👀 ❤️ 😂 🙏 or the full picker; tap a reaction again to take it away, hover it to see who.
- **Better attachments.** Attach several files at once, or paste or drop them onto the chat; they wait under the box (✕ removes one) and go with the next message, with or without text. Pictures show in the chat, other files show their size. Deleting a message deletes its files from the server too, and so does the automatic clear-out of old messages.
- **Admins can delete direct-message chats.** Admin → Chat lists every direct-message chat (who and how much, never what was said) and deletes one, or all, for both people, with its files and reactions. An admin in a direct-message chat also gets *Delete chat* at the top.

## 0.6.1

- **GO starts what is loaded.** Pressing a quick timer (with *Start on tap* off) and then GO now starts that timer; before, GO moved past it and every screen went to `--:--`. A cue loaded with *Next* also starts on GO, and GO after that moves on as before.
- **Screens and the workspace respond at once.** Every timer button updates the workspace from the server's reply instead of waiting for the live feed, older updates can no longer overwrite newer ones, and the Standard view shows a loaded time before GO instead of a blank.
- **The workspace layout is yours.** Everything on the workspace (room timer, quick timers, messages, second line, cue list, Add a screen, captions, sessions and any pinned window) is a tile. *Layout* turns on editing: drag ⠿ to move a tile, ◂ ▸ ▴ ▾ or the corner to resize it, ✕ to hide it, *Show a hidden part* to bring it back, or pick a ready-made layout (Standard, Compact timer, Big timer). Each laptop keeps its own. The timer tile is smaller by default and its clock scales to the tile.

## 0.6.0

- **Add a screen with a code.** A new Linux screen (or any browser on `/screen`) shows a big six-digit code. Any tech types it into *Add a screen* beside the timer in the workspace and picks what it shows: a timer view, a built or uploaded view, captions (audience screen, transparent overlay or the new subtitle bar), a test pattern or a web page. The screen joins the room and shows it straight away. The screen agent (0.3.0) installs without an enrolment code and goes back to showing a code if it is removed; `--code` still works.
- **Captions work like AT LiveCaption.** Per room, for any tech (Captions → Caption settings, or `/static/captions-control.html`): mic gain and graphic EQ with a live analyser, level meter after processing, custom vocabulary with a boost strength, music/filler noise shown as [MUSIC], spelled-out acronyms joined, a live preview that marks words the engine wasn't sure of, click-a-word corrections that add to the vocabulary, the last 200 captions, transcripts you start and stop at any time with SRT and WebVTT export, and each caption screen's look (font, size, weight, alignment, colours, opacity, lines, position, hold, fade, AI disclaimer), pushed live to the screens. Caption screens use LiveCaption's rolling lines: wrapped to the screen width, long sentences roll on without waiting for a pause, old lines hold then fade. New vocabulary reaches rooms already captioning at their next pause. See `captions-parity.md` in the v0.6 notes for the full comparison.
- **The HCC view is now called Standard** (its id stays `hcc`, so screens and links keep working). Same look, logo still uploadable.
- **BDNG is an imported view**: the client's Ontime view exactly as built (`docs/views/bdng.html`, the original HTML, CSS and JS in one file, no logos inside). Import it in Admin → Timers with one button (or upload the file like any view); it runs unchanged on the room's Ontime-compatible feed, and reads its two logos and bottom text ("BDNG Official Timekeeping Sponsor") from Admin → Timers. Screens still set to `bdng` show it, or the Standard view until it is imported.
- **Timer views: Ontime-style status bar** along the bottom of the stage, Standard, clock and backstage views (time now, running/paused/overtime, the cue, started, elapsed, expected end). On by default, switchable per view in Admin → Timers (and in the view builder), or `?status=0/1` on a screen's address. Standard looks exactly as before above it (checked pixel for pixel against the venue's Ontime view). Imported views can have it too, as a strip over their bottom edge (off unless ticked).
- **Second line under the timer**: a second countdown or a short text, set from the workspace, the API (`/api/timers/<room>/secondary/...`) or Companion. Standard, stage, minimal and overlay show it; so do imported Ontime views like BDNG (secondary message / aux timer 1).
- The **next cue is no longer shown** on the stage views (still in the workspace; a built view can turn it on). The **room name** sits small in the bottom-left corner, only just visible.
- **Workspace timer: quick buttons** for 3 to 60 minutes (start on tap, or load only) and a big **Clock** button; **quick messages** to the speaker (tap to show, tap again to hide, optional blink), editable by admins.
- Captions window: send a test line, clear the screens, links to the caption screen and overlay, engine state, latest transcript.
- Companion module (`companion-module/`): presets 3 to 60, +/-, control, blink, clock, blackout, messages, AT Overlay and caption tests, with live feedback and a time-left variable.
- Admins and techs land on the workspace; the console Dashboard shows live room timers; the Companion guide is served at `/guide/companion`.
- Main PC no longer shows urgent chat toasts.
- **AT Overlay** can sit top centre, now the default, and follows the room the tech signed in to.
- **Screens preview** window: pin live thumbnails of the room's timer views, caption screens and Linux screens.
- **Pin any window** to a dashboard grid under the timer, reorder it and make it wider.
- Workspace **Links** window has search and groups (this room, tools, everything else) for the imported Homarr board.

- **Screen test (from AT-ScreenTest).** `/screentest` is a browser page of test patterns for any display: solid colours with a slow pulse, colour bars, grey and RGB gradients, a pixel checkerboard that inverts, crosshatch and geometry, sharpness and text, a motion line, an overscan border with the 5% safe area, and the LED tile map (rows by columns, tile coordinates, a corner mark to spot a rotated panel, and a highlight that walks tile by tile). Move the mouse for the controls; arrow keys change pattern. A Linux screen or kiosk can be sent to any pattern from the workspace's Screens window, Timers → Screens, or Companion (`screentest:ledmap` and so on).

## 0.5.0

- **AT-SUIT logo and brand.** The app, the Windows app, its tray icon, the favicons and the sign-in pages use the new AT-SUIT logo, and the default accent is the brand orange. `branding/` has the logo files, app icons, social images, the brand guide (HTML and PDF) and `make_brand.py`, which rebuilds them all. Headings use the brand font (Saira), the console sidebar is navy with an orange marker on the current page, pages show the spinning gear while they start, and the Windows app's setup, pop-ups and a new "can't reach the server" page carry the logo.
- **Tech workspace redesign.** The room timer fills the screen. Captions, sessions, screens, overlays and links open as windows you can move, resize and close; the layout is remembered. Chat is a small dock in the corner, and the red **Help** button at the top drops down with the message and the room's open calls.
- **Notifications** for chat, help calls, the timer (warning, danger, overtime), new presenter files, captions stopping and the connection dropping: a bell list and a note in the window, and silent pop-ups on the backup PC. The main PC still shows nothing.
- **Captions** in their own window, with a live overview next to the timer. **Linux screens** in the room can be switched from the workspace to any view or any web page.
- **Timers.** Built-in **HCC** and **BDNG** views; every view follows HCC's warning, danger, blink, message and overtime behaviour. A **Clock** button shows the time of day on every screen. **Blink** and **Blackout** are buttons. A **view builder** in Timers → Views. An **overlay** view for the AT Overlay window.
- **AT Overlay built in.** AT-SUIT Node can float the room timer (or any web page) over the laptop's screen in a see-through, click-through window. Turn it on, move it or change it from any tech laptop in the room, e.g. from the backup PC onto the main PC.
- **Companion.** Presets for 3 to 60 minutes, blink, clock, blackout and stage message endpoints, and [docs/COMPANION.md](docs/COMPANION.md) with ready-made buttons and every endpoint an API key can call.
- **Admin.** Node setup is split by machine (Windows tech app, Linux screens, room sync, older agents) with download links. A new **Info** tab (version, build, nodes, server, storage) with diagnostics to download or copy. The **Licence** tab shows every detail and only admins see anything about the licence. Import, backup and audit are one tab. Logo URL and support contact are gone from General.

## 0.4.1

- **Tech laptops: name, room, main or backup.** No more username and password on AT-SUIT Node: the tech types their name, picks the room and says whether this is the **Main PC** (no pop-ups or notifications at all) or the **Backup PC** (silent pop-ups on top of everything). Chat and help calls carry their name. It resets each morning or on Sign out; Nodes in the console shows who is on each laptop and which it is.

## 0.4.0

- **The venue's Ontime views run as they are**: HCC and both BDNG views were tested against AT-SUIT. Views now live at `/room/<room>/external/<view>/`, so a view's own `?room=` setting (BDNG uses it for the room name) is left alone. Ontime's `/data/settings` is answered too.
- **Flash at danger**: the Ontime automation the venue used (blink the timer at danger, stop blinking when the next timer starts) is built in, per room, and switched on automatically when an Ontime project with that automation is imported. It always flashes its own room, so the SD1 automation that flashed CC can't happen.
- The stage timer blinks the clock too when Blink is on, like Ontime.
- **Presenters** (from AT-Presenter), a new page in the console: events and their running order by day, a link for each presenter (no account) to upload slides from their phone and check in, file review with versions and notes the presenter sees, show files per session in running order (walk-in video, stings, the final deck), schedule import from spreadsheets and CSV (PDF and Word through a local AI model) with a check-and-edit step before anything is saved, and a CSV of the whole schedule.
- **Sessions in this room** in the tech workspace: today's sessions, who's here, their files to approve or reject, the show files, and "Send this day to the timer" to make the room's cue list from the sessions.
- **Room sync** for presentation laptops: `room-sync/atsuit_room_sync.py` (Python only, nothing to install) keeps a folder per session with the approved presenter file and the show files, using the room's code from Presenters → Settings. It removes files that are withdrawn or replaced.
- Licences that list every module of the first release now cover modules added since; new licences can say `*`.

## 0.3.0

- **Cue lists, like Ontime**: each room's timer runs a running order. Add, edit, reorder and skip cues (title, duration, planned start, colour, note, count down / count up / clock); GO, next, previous, load, ±1 minute, stage messages with blink and blackout. At zero a cue can keep going, stop, load the next or play the next, handled by the server. Bring in an existing Ontime project file (v3 or v4).
- **Custom Ontime views**: upload a view's folder (zip with `index.html`) in Timers → Views. It is served at `/external/<view>/?room=<room>` and gets the same live data Ontime sends, so views made for Ontime work unchanged.
- **Remote screens**: Linux laptops and all-in-ones are display-only screens at `/screen`. Pick the room and view on the screen itself (tap the top-left corner 5 times) or route it from Timers → Screens: a timer view, captions, a custom view or any web page.
- **Linux screen agent** (`screen-agent/`): one-line install. HDMI rule: with a display plugged in the picture goes only to that display, otherwise to the built-in screen. Reports name, IP, displays and agent version; restart, update and (optionally) reboot from the dashboard; updates itself.
- Timers page in the console is now a preview of every room plus screen routing; techs run timers from their workspace.
- Overlay control moved to the tech workspace, for that room's LiveOverlay laptops. Admin → Overlay laptops is where they are added.
- Stage timers count down smoothly between updates.

## 0.2.0

- **AT-SUIT Node for Windows**: the tech workspace as an installed app, no browser. A laptop enrols once (or from a `C:\ProgramData\AT-SUIT\node.json` preset for silent roll-outs) and opens straight into the workspace. The captions mic works over plain http, so laptops need no certificate. Closing hides it to the tray; it updates itself from the AT-SUIT server when it next quits.
- **Room of the day**: a tech laptop isn't tied to a room. The tech picks the room after signing in, and the choice resets each morning at 05:00 site time. Kiosks keep the room an admin gives them.
- **Silent pop-ups, backup laptop only**: off by default and switched on per laptop. Pop-ups sit on top of everything, including full-screen slides, never take focus and never make a sound (all app audio is muted, no Windows toasts, no native dialogs). Tested on Windows in CI.
- Admin → Node setup: download link for the Windows app and an upload for new app releases.
- Chat's "Direct message" picker is now in the page instead of a browser prompt.

## 0.1.0

First release.

- One server app: crew chat with DMs, attachments and help requests; room timers with stage screens; server-side live captions with audience and overlay screens; node enrolment, heartbeats and commands; AT LiveOverlay control; link dashboard.
- Setup wizard and Admin pages for everything: sites, rooms, people, links, nodes, modules, branding, licence, API keys, imports, audit log, backups.
- Tech workspace at `/node` for every laptop, with the laptop's mic as the caption source.
- Works with the existing Device Suite kiosk agents (`/heartbeat`, `/agent/poll`, `/agent/ack`).
- Imports from AT-RoomComms (accounts keep their passwords), Device Suite `rooms.txt` and `state.json`, and Homarr links.
- Offline licences, evaluation mode, multi-site.
- Docker image, compose file with optional https, install/update/backup/restore scripts, node agent.
