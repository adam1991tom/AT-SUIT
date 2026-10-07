#!/usr/bin/env python3
"""AT-SUIT room sync: keeps a presentation laptop's folder up to date.

Runs on a room's presentation laptop that isn't an AT-SUIT Node. Every few
seconds it asks the AT-SUIT server what this room needs (each session's
approved presenter file and its show files) using the room's sync code from
Presenters -> Settings, downloads anything new or changed, and removes what
is no longer wanted. Nothing to install: Python 3.8+ only.

    python atsuit_room_sync.py --server https://10.100.70.100:8443 --code ABCD-2345

The first run saves the settings to atsuit_room_sync.json next to this file,
so after that plain `python atsuit_room_sync.py` is enough. The same settings
can come from ATSUIT_SERVER, ATSUIT_ROOM_CODE, ATSUIT_SYNC_DIR and
ATSUIT_SYNC_SECONDS.

The folder looks like:
    files/
      2026-10-12 0930 Keynote/
        Keynote final.pptx            (the presenter's approved file)
        show/01 Walk-in.mp4           (show files, in running order)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "atsuit_room_sync.json"
STATE = ".atsuit-sync.json"


def settings(argv=None) -> dict:
    ap = argparse.ArgumentParser(description="Keep this laptop's presentation folder in step with AT-SUIT.")
    ap.add_argument("--server", help="AT-SUIT address, e.g. https://10.100.70.100:8443")
    ap.add_argument("--code", help="the room's sync code (Presenters -> Settings)")
    ap.add_argument("--dir", help="folder to keep the files in (default: files/ next to this script)")
    ap.add_argument("--every", type=int, help="seconds between checks (default 20)")
    ap.add_argument("--cafile", help="the server's certificate (.pem), when it uses its own self-signed one")
    ap.add_argument("--once", action="store_true", help="sync once and exit")
    a = ap.parse_args(argv)
    cfg = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            cfg = {}
    pick = lambda arg, env, key, default: arg if arg not in (None, False) else os.environ.get(env) or cfg.get(key, default)  # noqa: E731
    cfg = {"server": str(pick(a.server, "ATSUIT_SERVER", "server", "")).rstrip("/"),
           "code": str(pick(a.code, "ATSUIT_ROOM_CODE", "code", "")).strip().upper(),
           "dir": str(pick(a.dir, "ATSUIT_SYNC_DIR", "dir", str(HERE / "files"))),
           "every": max(5, int(pick(a.every, "ATSUIT_SYNC_SECONDS", "every", 20))),
           "cafile": str(pick(a.cafile, "ATSUIT_CAFILE", "cafile", ""))}
    if not cfg["server"] or not cfg["code"]:
        if not sys.stdin.isatty():
            sys.exit("Give --server and --code (or ATSUIT_SERVER and ATSUIT_ROOM_CODE).")
        print("First-time setup for this room's laptop.")
        cfg["server"] = cfg["server"] or input("AT-SUIT address (e.g. https://10.100.70.100:8443): ").strip().rstrip("/")
        cfg["code"] = cfg["code"] or input("This room's sync code (Presenters -> Settings): ").strip().upper()
    if a.server or a.code or not CONFIG.exists():
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    cfg["once"] = a.once
    return cfg


def safe(name: str) -> str:
    out = "".join(ch for ch in str(name) if ch.isalnum() or ch in "._-() &,'").strip(" .")
    return out[:80] or "file"


def folder(item: dict) -> str:
    when = (item.get("starts_at") or "").replace("T", " ").replace(":", "")
    return safe(f"{when} {item['session_title']}".strip())


class Sync:
    def __init__(self, cfg: dict):
        self.cfg, self.root = cfg, Path(cfg["dir"]).resolve()
        self.ctx = ssl.create_default_context(cafile=cfg["cafile"]) if cfg["cafile"] else None
        self.base = f"{cfg['server']}/api/presenter/sync/{urllib.parse.quote(cfg['code'])}"

    def get(self, url: str, timeout: int = 20):
        req = urllib.request.Request(url, headers={"User-Agent": "atsuit-room-sync"})
        return urllib.request.urlopen(req, timeout=timeout, context=self.ctx)

    def manifest(self) -> dict:
        with self.get(self.base) as r:
            return json.loads(r.read().decode("utf-8"))

    def download(self, url: str, dest: Path) -> str:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part, h = dest.with_name(dest.name + ".part"), hashlib.sha256()
        with self.get(url, timeout=300) as r, part.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
        part.replace(dest)
        return h.hexdigest()

    def inside(self, rel: str) -> Path | None:
        p = (self.root / rel).resolve()
        return p if p != self.root and self.root in p.parents else None

    def once(self) -> tuple[str, int]:
        m = self.manifest()
        self.root.mkdir(parents=True, exist_ok=True)
        state_file = self.root / STATE
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        want = {}
        for f in m["files"]:
            want[f"file:{f['session_id']}:{f['file_id']}"] = (f"{folder(f)}/{safe(f['original_name'])}", f["sha256"], f"{self.base}/files/{f['file_id']}")
        for f in m["show_files"]:
            name = f"{int(f['position']) + 1:02d} {safe(f['label'])}"
            ext = Path(f["original_name"]).suffix
            if ext and not name.lower().endswith(ext.lower()):
                name += safe(ext)
            want[f"show:{f['show_id']}"] = (f"{folder(f)}/show/{name}", f["sha256"], f"{self.base}/show/{f['show_id']}")
        changed = 0
        for key in list(state):  # gone, moved or replaced
            if key in want and want[key][0] == state[key]["path"] and want[key][1] == state[key]["sha256"]:
                continue
            old = self.inside(state[key]["path"])
            if old and old.exists():
                old.unlink()
                for parent in (old.parent, old.parent.parent):
                    if parent != self.root and self.root in parent.parents and parent.exists() and not any(parent.iterdir()):
                        parent.rmdir()
            del state[key]
            changed += 1
        for key, (rel, sha, url) in want.items():
            dest = self.inside(rel)
            if not dest or (key in state and dest.exists()):
                continue
            print(f"Downloading {rel}")
            got = self.download(url, dest)
            if got != sha:
                print(f"  warning: {rel} didn't match the server's checksum; it will be fetched again")
                continue
            state[key] = {"path": rel, "sha256": sha}
            changed += 1
            state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return m["room_name"], changed


def main(argv=None) -> None:
    cfg = settings(argv)
    s = Sync(cfg)
    print(f"AT-SUIT room sync: {cfg['server']}, code {cfg['code']}, folder {s.root}, every {cfg['every']} s")
    while True:
        try:
            room, changed = s.once()
            if changed:
                print(f"{room}: up to date ({changed} change{'s' if changed != 1 else ''})")
        except urllib.error.HTTPError as e:
            print("That room code isn't recognised. Check it in Presenters -> Settings." if e.code == 404 else f"Server error {e.code}; trying again.")
        except (urllib.error.URLError, OSError) as e:
            print(f"Couldn't reach {cfg['server']} ({getattr(e, 'reason', e)}); trying again.")
        if cfg["once"]:
            return
        time.sleep(cfg["every"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
