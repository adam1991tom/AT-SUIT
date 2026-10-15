#!/bin/sh
# Nightly copy of the shop's database (customers, keys, orders). Run from cron on the shop server:
#   15 3 * * *  /opt/atsuite-shop/deploy/backup.sh /var/backups/atsuite-shop
# SQLite's own backup command makes a safe copy while the shop is running. Copy the folder off the
# server too (or let your provider's snapshots cover it). The signing key is backed up separately, by hand.
set -eu
dest="${1:-/var/backups/atsuite-shop}"
mkdir -p "$dest"
docker compose -f "$(dirname "$0")/../docker-compose.yml" exec -T shop \
  python -c "import sqlite3,sys; s=sqlite3.connect('/data/shop.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
docker compose -f "$(dirname "$0")/../docker-compose.yml" cp shop:/data/backup.db "$dest/shop-$(date +%F).db"
find "$dest" -name 'shop-*.db' -mtime +30 -delete
