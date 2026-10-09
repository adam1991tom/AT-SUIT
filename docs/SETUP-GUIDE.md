<img src="../branding/logo/atsuit-wordmark.png" alt="AT-SUIT" width="200">

# Setup guide

From a bare server to a venue ready for its first show, in about an hour.
Every step after the install happens in the browser. [ADMIN.md](ADMIN.md)
has the detail behind each one; [INSTALL.md](INSTALL.md) covers other ways
to install.

## What you need

- **One server** on the venue network: any 64-bit Linux machine (Ubuntu
  22.04 or newer is easiest) with 4 CPU cores, 8 GB of memory and 20 GB of
  disk. Live captions use about one core per room; add
  [helper servers](INSTALL.md#helper-servers) for more rooms.
- **A fixed address** for it (a DHCP reservation is fine), for example
  `10.0.0.10`.
- **Tech laptops** with Windows 10 or 11 for the AT-SUIT app. Any other
  computer, tablet or phone can use the workspace in a browser.
- **Screens** (optional): Linux laptops or all-in-ones for stage timers and
  signage, or any browser pointed at a timer page.

## 1. Install (one command)

On the server:

```sh
curl -fsSL https://raw.githubusercontent.com/adam1991tom/at-suit/main/get.sh | sudo bash
```

It installs Docker if it's missing, puts AT-SUIT in `/opt/at-suit` and starts
it on port 8080. Options go after `-s --`, for example
`... | sudo bash -s -- --port 8180 --tls` (`--tls` adds https, which browsers
need before they'll share a microphone; the Windows app doesn't).

## 2. First visit

Open `http://SERVER:8080/` in a browser. The setup wizard asks for the
organisation, the venue, the rooms (one per line) and the first admin
account. Paste the licence key if you have one; without it AT-SUIT runs in
evaluation mode and you can add the key later in **Licence**.

## 3. Rooms and people

In the console:

1. **Rooms**: check the room names and order.
2. **People**: add an account for each manager and anyone who uses the
   console. Techs on an enrolled laptop don't need an account; they type
   their name each morning.
3. **Settings → General**: your product name, colour and which modules are
   on. **Settings → Look** picks the theme for the whole site.

## 4. Tech laptops

1. **Laptops & screens → Add laptops → Windows tech app**: upload the
   installer from the release (the `.exe`, `.blockmap` and `latest.yml`) once.
   After that every laptop updates itself from your server, never from the
   internet and never mid-show.
2. On each laptop, download and run the installer from the same page, then
   enter the server address, the enrolment code shown at the top of the page
   and a name for the laptop. IT can skip the typing with `node.json` (shown
   on the same page) and a silent install.
3. Print the **tech quick-start card** (linked on that page, or
   `http://SERVER:8080/guide/tech`) and leave one by each laptop.

## 5. Screens, overlays and Companion (optional)

- **Linux screens**: Laptops & screens → Add laptops → Linux screens has a
  one-line install command. Screens then show whatever the room's tech picks.
- **Overlays**: Laptops & screens → Overlay laptops, one entry per laptop
  running AT LiveOverlay.
- **Companion**: Settings → API keys, then follow
  `http://SERVER:8080/guide/companion`.

## 6. Before the first show

- Sign in on every tech laptop as a tech, pick the room and Main or Backup,
  and run a cue on the timer. Check the stage screen follows it.
- Send a help request from one room and answer it from another.
- Press **Send my mic** in a captioned room and check the words appear.
- **Backups**: download one and keep it off the server.

## Keeping it running

- **Updates**: `sudo /opt/at-suit/update.sh`. It saves a backup first, starts
  the new version and checks it is healthy; if it isn't, it puts the old
  version and the data back by itself. Laptops update when their app next
  starts or closes, or when the tech presses **Update now** in This laptop.
- **Backups**: `sudo /opt/at-suit/backup.sh` (add it to cron for a nightly
  copy) or the **Backups** page. `restore.sh` puts one back.
- **Support**: **Settings → About → Download diagnostics** gives support
  everything about the install without passwords or keys.
