#!/usr/bin/env bash
# Turn a fresh Hetzner Cloud server (Ubuntu 24.04) into an AT-SUITE cloud server.
#
#   git clone https://github.com/adam1991tom/AT-SUITE.git
#   sudo AT-SUITE/cloud/bootstrap.sh --domain atsuit.app
#
# It installs Docker, security updates, a firewall (SSH in; the venues' https is guarded by
# the front door, which only lets Cloudflare in), a swap file, the atcloud tool and the
# nightly backup timer, then runs `atcloud init`. Safe to run again.
set -euo pipefail

DOMAIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    *) echo "Unknown option $1"; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }
[ -n "$DOMAIN" ] || { echo "Usage: bootstrap.sh --domain yourdomain"; exit 2; }
KIT="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

echo "== Packages and security updates"
apt-get update -q
apt-get upgrade -yq
apt-get install -yq curl ca-certificates openssl python3 ufw unattended-upgrades fail2ban
# Security updates install by themselves; the server never reboots by itself (that could be mid-show).
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "== Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
# Room for a private network per venue (1,024 of them), and logs that can't fill the disk.
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path("/etc/docker/daemon.json")
d = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
d.setdefault("default-address-pools", [{"base": "10.200.0.0/14", "size": 24}])
d.setdefault("log-driver", "json-file")
d.setdefault("log-opts", {"max-size": "10m", "max-file": "3"})
d["live-restore"] = True  # venues keep running while Docker itself updates
p.write_text(json.dumps(d, indent=2) + "\n")
EOF
systemctl restart docker

echo "== Firewall and SSH"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
if [ -s /root/.ssh/authorized_keys ]; then
  # Keys only, once there is a key to sign in with.
  printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/10-atcloud.conf
  systemctl reload ssh || systemctl reload sshd || true
else
  echo "No SSH key for root yet, so password sign-in is left on. Add your key in the Hetzner console."
fi
systemctl enable --now fail2ban >/dev/null 2>&1 || true

echo "== Swap"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== atcloud"
mkdir -p /opt/atcloud
cp -r "$KIT"/. /opt/atcloud/
chmod +x /opt/atcloud/atcloud
ln -sf /opt/atcloud/atcloud /usr/local/bin/atcloud
cp /opt/atcloud/systemd/atcloud-backup.service /opt/atcloud/systemd/atcloud-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now atcloud-backup.timer >/dev/null

atcloud init --domain "$DOMAIN" --cloudflare

cat <<EOF

Done. Next (docs/HOSTING.md has each step):
  1. Cloudflare: DNS record  *.$DOMAIN  → this server's IP, proxied (orange cloud);
     SSL/TLS mode Full (strict); make an origin certificate for *.$DOMAIN and $DOMAIN
     and put it in /srv/atcloud/certs/origin.pem and origin.key, then run: atcloud edge
  2. Backups:  atcloud storagebox u123456   (your Storage Box user)
  3. Speech model for laptop captions, once:  atcloud models
  4. If the AT-SUITE image is private:  docker login ghcr.io
  5. First venue:  atcloud new grandhall --name "Grand Hall" --licence key.txt
EOF
