<img src="../branding/logo/atsuit-wordmark.png" alt="AT-SUIT" width="200">

# Installing AT-SUIT

## Requirements

- A Linux server with Docker Engine and Docker Compose v2.
- 2 GB RAM plus about 300 MB per room being captioned at the same time, and
  roughly one CPU core per captioned room.
- 2 GB disk for the image and speech model, plus whatever chat uploads need.
- The first start downloads the speech model (about 500 MB, once). For a site
  without internet, copy a `models` folder from another install into the
  `atsuit-data` volume (see "Offline sites").

## Install

```bash
git clone https://github.com/adam1991tom/AT-SUIT.git /opt/atsuit
cd /opt/atsuit
sudo ./install.sh --port 8180 --tls
```

`install.sh` writes `.env` from `.env.example`, refuses a port that is already
taken, builds the image, starts it, waits for it to be healthy, and prints the
address. Open it in a browser and complete the setup wizard.

Options: `--port N`, `--tls` (adds https), `--tls-host ADDRESS` (the address
laptops use, defaults to the server's first IP), `--no-asr` (no captions,
smaller image).

## https and microphones

Browsers only let a web page use the microphone over https (or on the same
machine). A tech laptop sending captions from `/node` therefore needs the https
address. `--tls` starts Caddy with its own certificate authority on port 8443.
Each laptop must trust that authority once:

```bash
docker compose cp tls:/data/caddy/pki/authorities/local/root.crt ./atsuit-root.crt
```

Install `atsuit-root.crt` on the laptops as a trusted root certificate (on
Windows: double-click, Install Certificate, Local Machine, Trusted Root
Certification Authorities; Action1 or Group Policy can push it to every
laptop). If you'd rather not, run the node agent for captions instead; it
doesn't need https.

## Running next to the old apps (trial)

AT-SUIT uses one port and one Docker volume (`atsuit_atsuit-data`) and
touches nothing else, so it can run beside RoomComms, Device Suite, Ontime
and Companion. On ATSERVER1, ports 4001-4020, 5050, 5070, 8000, 16622 and
45876 are taken; 8180 and 8443 are free.

```bash
sudo ./install.sh --port 8180 --tls --tls-host 10.100.70.101
```

## Bringing data across

Everything is in Admin → Import. Imports only add; they never change the old
apps.

| Old app | What to upload | How to get it |
|---|---|---|
| RoomComms | a zip of its data folder | `docker cp at-roomcomms:/data ./roomcomms-data && zip -r roomcomms-data.zip roomcomms-data` |
| Device Suite | `rooms.txt` | `/opt/device-suite/rooms.txt` |
| Device Suite | `state.json` | `/opt/device-suite/state.json` |
| Homarr | link export TSV | the `homarr-links.tsv` from the server snapshot |

RoomComms accounts keep their passwords. Messages are re-encrypted with
AT-SUIT's own key. Direct messages and operator logins (name only, no
password) aren't brought across; the import report says how many.

## Pointing kiosks at AT-SUIT

Kiosks running the Device Suite agent can report to AT-SUIT without being
reinstalled: change the server address in their agent config to
`http://SERVER:8180`. They appear in Nodes as "old agent". To reboot or
update them from AT-SUIT, upload the fleet SSH key in Admin → Node setup.

## Helper servers

One server is the **main** server: it holds the rooms, people, chat and every
screen. Any other machine can run the same install as a **helper** and take
heavy work off it. Today that is live caption speech recognition; each helper
adds the rooms it can caption to the total.

1. On the main server, go to **Admin → Servers → Make a join code**. A code
   works once, for 15 minutes.
2. On the helper, install AT-SUIT as above with `ATSUIT_ROLE=helper` in `.env`,
   then open `http://<helper>:8080` and type the main server's address and the
   code. Or set `ATSUIT_MAIN_URL` and `ATSUIT_JOIN_CODE` in `.env` before the
   first start. Use the main server's plain `http://` address on the venue
   network.
3. The helper connects out to the main server, so it needs no open ports of
   its own, and reconnects by itself after a restart.

On **Admin → Servers** choose how rooms are spread:

- **Share**: each room goes to whichever server is least busy, the main one included.
- **Offload**: helpers take rooms first, and the main server only steps in when they are full or offline.

If a helper goes offline mid-show, its rooms move to another helper or the
main server within a second, and the laptops keep sending audio as before.
**Remove** on that page disconnects a helper; it needs a new code to join again.

## Backups and updates

```bash
./backup.sh                           # saves backups/atsuit-DATE.tgz
./restore.sh backups/atsuit-DATE.tgz  # puts one back (asks first)
./update.sh                           # backs up, pulls, rebuilds, restarts
```

Admins can also download a backup zip from Admin → Audit & backup. A backup
contains the encryption key, so store it somewhere safe.

## Offline sites

On a machine with internet, run AT-SUIT once so it downloads the model, then
copy it:

```bash
docker run --rm -v atsuit_atsuit-data:/data -v $PWD:/out alpine tar czf /out/models.tgz -C /data models
# on the offline server, after ./install.sh:
docker run --rm -v atsuit_atsuit-data:/data -v $PWD:/in alpine sh -c "tar xzf /in/models.tgz -C /data && chown -R 999:999 /data/models"
docker compose restart atsuit
```

## Uninstall

```bash
docker compose --profile tls down       # stops it, keeps data
docker compose --profile tls down -v    # also deletes all data
```
