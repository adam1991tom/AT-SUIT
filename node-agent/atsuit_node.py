#!/usr/bin/env python3
"""AT-SUIT node agent.

Runs on a tech laptop, kiosk or caption PC (Windows, Linux or macOS). It
enrols with the server, sends heartbeats, carries out commands (open a
screen, identify, reboot), and can stream a microphone to the server for
live captions. All speech recognition happens on the server.

    pip install websockets sounddevice numpy
    python atsuit_node.py --server http://10.100.70.101:8180 --code ABCD-1234-EF56 --name ATLAP3 --room CC --mic

The token from enrolment is saved next to this file (atsuit_node.json), so
later runs only need --server.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path

VERSION = "0.1.0"
STATE = Path(__file__).with_name("atsuit_node.json")
SAMPLE_RATE = 16000


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))
    try:
        os.chmod(STATE, 0o600)
    except OSError:
        pass


def http(server: str, method: str, path: str, body=None, token: str | None = None):
    req = urllib.request.Request(server.rstrip("/") + path, method=method)
    if token:
        req.add_header("Authorization", f"Node {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=10) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        raise RuntimeError(f"{e.code}: {detail}") from None


def local_ip(server: str) -> str:
    host = server.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            return s.getsockname()[0]
    except OSError:
        return ""


def mac() -> str:
    n = uuid.getnode()
    return ":".join(f"{(n >> s) & 0xFF:02x}" for s in range(40, -1, -8))


# ---------------------------------------------------------------- commands --
def power(action: str) -> None:
    win = platform.system() == "Windows"
    cmd = {
        "reboot": ["shutdown", "/r", "/t", "5"] if win else ["sudo", "-n", "reboot"],
        "shutdown": ["shutdown", "/s", "/t", "5"] if win else ["sudo", "-n", "shutdown", "now"],
    }[action]
    subprocess.Popen(cmd)


def run_command(cmd: dict, args) -> tuple[bool, str]:
    kind, payload = cmd["kind"], cmd.get("payload") or {}
    if kind == "set_url":
        url = payload.get("url", "")
        if args.kiosk_cmd:
            if not url.startswith(("http://", "https://")):
                return False, "Not a web address"
            subprocess.Popen([url if part == "{url}" else part for part in shlex.split(args.kiosk_cmd)])
        else:
            webbrowser.open(url)
        return True, ""
    if kind in ("message", "identify"):
        print(f"[{time.strftime('%H:%M:%S')}] {payload.get('text') or 'Identify: ' + args.name}")
        return True, ""
    if kind in ("reboot", "shutdown"):
        if not args.allow_power:
            return False, "Power commands are off on this node (start the agent with --allow-power)"
        power(kind)
        return True, ""
    return False, f"This agent can't do '{kind}'"


def newer(a: str, b: str) -> bool:
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in v.split(".")]
    return parts(a) > parts(b)


def self_update(server: str, token: str, release: dict) -> None:
    """Pull a newer agent from the server, check it, swap it in and restart.
    Nodes update themselves; the server never has to reach in over SSH."""
    import hashlib

    req = urllib.request.Request(server.rstrip("/") + "/api/nodes/agent/file")
    req.add_header("Authorization", f"Node {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != release.get("sha256"):
        raise RuntimeError("downloaded agent failed its checksum")
    me = Path(__file__).resolve()
    tmp = me.with_suffix(".new")
    tmp.write_bytes(data)
    compile(data, str(tmp), "exec")  # refuse to install something that doesn't even parse
    os.replace(tmp, me)
    print(f"Updated agent {VERSION} -> {release['version']}, restarting")
    os.execv(sys.executable, [sys.executable, str(me), *sys.argv[1:]])


async def control_loop(args, state) -> None:
    server, token = args.server, state["token"]
    last_url = ""
    while True:
        try:
            hb = http(server, "POST", "/api/nodes/heartbeat", {
                "ip": local_ip(server), "mac": mac(), "version": f"agent-{VERSION}", "current_url": last_url,
                "info": {"os": platform.platform(), "python": sys.version.split()[0], "mic": bool(args.mic)},
            }, token)
            release = (hb or {}).get("agent") or {}
            if args.self_update and release.get("version") and newer(release["version"], VERSION):
                self_update(server, token, release)
            for cmd in http(server, "GET", "/api/nodes/commands", token=token) or []:
                ok, detail = run_command(cmd, args)
                if ok and cmd["kind"] == "set_url":
                    last_url = cmd["payload"].get("url", "")
                http(server, "POST", f"/api/nodes/commands/{cmd['id']}/ack", {"ok": ok, "detail": detail}, token)
        except (OSError, RuntimeError) as exc:
            print("server:", exc)
        await asyncio.sleep(args.interval)


# --------------------------------------------------------------------- mic --
def find_room(server: str, token: str, room: str) -> int:
    me = http(server, "GET", "/api/nodes/me", token=token)
    if not room and me.get("room"):
        return me["room"]["id"]
    if room.isdigit():
        return int(room)
    rooms = http(server, "GET", "/api/bootstrap", token=token)["rooms"]
    match = [r for r in rooms if room.lower() in (r["name"].lower(), r["short_name"].lower())]
    if not match:
        raise SystemExit(f"No room called {room!r}. Rooms: {', '.join(r['name'] for r in rooms)}")
    return match[0]["id"]


async def mic_loop(args, state) -> None:
    import numpy as np
    import sounddevice as sd
    import websockets

    room_id = find_room(args.server, state["token"], args.room)
    url = args.server.replace("http", "ws", 1).rstrip("/") + f"/ws/audio/{room_id}?node_token={state['token']}"
    device = int(args.device) if args.device and args.device.isdigit() else (args.device or None)
    while True:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
        loop = asyncio.get_running_loop()

        def callback(indata, frames, t, status):
            pcm = (np.clip(indata[:, 0], -1, 1) * 32767).astype(np.int16).tobytes()
            loop.call_soon_threadsafe(lambda: queue.full() or queue.put_nowait(pcm))

        try:
            async with websockets.connect(url, max_size=None) as ws:
                hello = json.loads(await ws.recv())
                if hello.get("type") != "ready":
                    print("captions:", hello.get("message", hello))
                    await asyncio.sleep(10)
                    continue
                print(f"Streaming microphone to room {room_id}")
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=1600,
                                    device=device, callback=callback):
                    while True:
                        await ws.send(await queue.get())
        except Exception as exc:  # network drop, device unplugged
            print("mic:", exc.__class__.__name__, exc)
            await asyncio.sleep(3)


# --------------------------------------------------------------------- main --
def main() -> None:
    ap = argparse.ArgumentParser(description="AT-SUIT node agent")
    ap.add_argument("--server", help="e.g. http://10.100.70.101:8180")
    ap.add_argument("--code", help="enrolment code (first run only)")
    ap.add_argument("--name", default=socket.gethostname().split(".")[0])
    ap.add_argument("--kind", default="tech", choices=["tech", "kiosk", "caption"])
    ap.add_argument("--room", default="", help="room name or number to caption (defaults to the node's room)")
    ap.add_argument("--mic", action="store_true", help="stream the microphone for captions")
    ap.add_argument("--device", default="", help="input device number or name (see --list-devices)")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--kiosk-cmd", default="", help='command to open a screen, e.g. "chromium --kiosk {url}"')
    ap.add_argument("--allow-power", action="store_true", help="allow reboot and shutdown from the console")
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--re-enrol", action="store_true", help="enrol again even if this node has a token")
    ap.add_argument("--no-self-update", dest="self_update", action="store_false",
                    help="don't install newer agents the server offers")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return
    state = load_state()
    args.server = args.server or state.get("server")
    if not args.server:
        ap.error("--server is required the first time")
    if args.re_enrol or not state.get("token") or state.get("server") != args.server:
        if not args.code:
            ap.error("--code is required the first time")
        r = http(args.server, "POST", "/api/nodes/enrol", {"code": args.code, "name": args.name, "kind": args.kind})
        state = {"server": args.server, "token": r["token"], "name": r["name"]}
        save_state(state)
        print(f"Enrolled as {r['name']}")
    args.name = state.get("name", args.name)

    async def run():
        tasks = [control_loop(args, state)]
        if args.mic:
            tasks.append(mic_loop(args, state))
        await asyncio.gather(*tasks)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
