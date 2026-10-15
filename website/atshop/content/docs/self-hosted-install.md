# Install on your own server

Run AT SUITE on a server at your venue. It works with no internet at all, licence included.

## What you need

- A 64-bit Linux machine (Ubuntu 22.04 or newer is easiest) with 4 CPU cores, 8 GB of memory and 20 GB of disk.
- A fixed address on the venue network, for example `10.0.0.10` (a DHCP reservation is fine).
- Windows 10 or 11 laptops for the techs.
- Your licence key, from [your account](/account).

## Install

On the server, as root:

```sh
curl -fsSL https://github.com/adam1991tom/AT-SUIT-releases/releases/latest/download/get.sh | sudo bash
```

It installs Docker if it's missing, puts AT SUITE in `/opt/at-suit` and starts it on port 8080. To pick another port or add https (browsers need https before they'll share a microphone; the Windows app doesn't):

```sh
curl -fsSL https://github.com/adam1991tom/AT-SUIT-releases/releases/latest/download/get.sh | sudo bash -s -- --port 8180 --tls
```

## First visit

Open `http://SERVER:8080/` in a browser and follow the setup wizard. Paste your licence key at the end. You can finish setup without one and paste it later in **Licence**, but until you do, only the Licence page works.

## Updates and backups

Your server checks for new releases and installs them outside show times, or when you press **Install now** in **Settings → Updates**. Download a backup any time from **Backups**.

Next: [install the laptop app](/docs/laptop-app).
