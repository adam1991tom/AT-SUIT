# AT-SUIT cloud

The kit for hosting AT-SUIT for many venues on one cloud server. The guide is
[docs/HOSTING.md](../docs/HOSTING.md).

```
atcloud            the tool: new venue, list, suspend, update, backup, restore, remove (Python 3 + Docker)
Caddyfile          the front door: https://<venue>.<domain> → that venue's container, Cloudflare only
bootstrap.sh       turns a fresh Hetzner Ubuntu 24.04 server into an AT-SUIT cloud server
systemd/           the nightly backup timer
test/e2e.sh        the whole kit end to end on one machine's Docker (CI runs it)
```
