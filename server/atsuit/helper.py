"""Helper mode (ATSUIT_ROLE=helper). This server has no rooms, accounts or
screens of its own: it joins a main AT-SUIT server with a one-time code and
runs whatever work the main server hands it (live caption recognition for
now). See modules/cluster.py for the main server's side and the wire format.

Joining: set ATSUIT_MAIN_URL and ATSUIT_JOIN_CODE, or open this server's page
in a browser and type them in. The secret it gets back is kept in the data
folder, so a restart reconnects on its own."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import struct
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import VERSION, asr, config

STATIC = Path(__file__).parent / "static"
STATS_EVERY = 5.0


def state_file() -> Path:
    return config.cfg.data / "helper.json"


def load_state() -> dict:
    try:
        return json.loads(state_file().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    config.cfg.data.mkdir(parents=True, exist_ok=True)
    f = state_file()
    f.write_text(json.dumps(st), "utf-8")
    with contextlib.suppress(OSError):
        f.chmod(0o600)


def ws_url(main_url: str) -> str:
    return ("wss://" + main_url[8:] if main_url.startswith("https://") else
            "ws://" + main_url.removeprefix("http://")) + "/ws/helper"


def load() -> float:
    try:
        return os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        return 0.0


class Status:
    def __init__(self) -> None:
        self.state = "waiting"  # waiting | connecting | connected | offline | removed
        self.detail = "Not joined to a main server yet"
        self.since = time.time()
        self.wake = asyncio.Event()

    def set(self, state: str, detail: str = "") -> None:
        if state != self.state:
            self.since = time.time()
        self.state, self.detail = state, detail


status: Status | None = None


class Worker:
    """Runs the jobs one main server sends down one connection. `send` takes
    a dict and delivers it to the main server."""

    def __init__(self, send) -> None:
        self.send = send
        self.jobs: dict[int, asyncio.Queue] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.loading: asyncio.Task | None = None

    def stats(self, kind: str = "stats") -> dict:
        ready = asr.engine.state == "ready"
        return {"t": kind, "name": load_state().get("name", socket.gethostname()), "version": VERSION,
                "capacity": config.cfg.asr_max_rooms if ready else 0, "load": round(load(), 3),
                "asr": asr.engine.state, "rooms": len(self.jobs)}

    async def on_text(self, m: dict) -> None:
        t = m.get("t")
        if t == "config":
            vocab = [str(w)[:100] for w in m.get("vocabulary", [])][:5000]
            score = m.get("hotwords_score")

            prev = self.loading

            async def reload():
                if prev:  # one load at a time, in order, so the newest vocabulary wins
                    with contextlib.suppress(Exception):
                        await prev
                await asyncio.to_thread(asr.engine.load, vocab, True, score)
                await self.send(self.stats())
            self.loading = asyncio.create_task(reload())
        elif t == "open" and isinstance(m.get("job"), int):
            job = m["job"]
            await self.close(job)
            q: asyncio.Queue = asyncio.Queue(maxsize=200)
            self.jobs[job] = q
            self.tasks[job] = asyncio.create_task(self._run(job, q, dict(m.get("options") or {})))
            await self.send(self.stats())
        elif t == "close":
            await self.close(m.get("job"))
            await self.send(self.stats())

    async def on_bytes(self, data: bytes) -> None:
        if len(data) < 4:
            return
        (job,) = struct.unpack(">I", data[:4])
        q = self.jobs.get(job)
        if q is None:
            return
        if q.full():  # falling behind: drop the oldest audio, not the newest
            q.get_nowait()
        q.put_nowait(data[4:])

    async def _run(self, job: int, q: asyncio.Queue, options: dict) -> None:
        try:
            session = asr.engine.session(options)
        except RuntimeError as exc:
            await self.send({"t": "error", "job": job, "message": str(exc)})
            self.jobs.pop(job, None)
            return
        while True:
            pcm = await q.get()
            if pcm is None:
                return
            events = await asyncio.to_thread(session.feed, pcm)
            if events:
                await self.send({"t": "events", "job": job, "events": events})

    async def close(self, job) -> None:
        q = self.jobs.pop(job, None)
        task = self.tasks.pop(job, None)
        if q is not None:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def close_all(self) -> None:
        for job in list(self.jobs):
            await self.close(job)


async def join(main_url: str, code: str, name: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(main_url + "/api/helpers/join", json={"code": code, "name": name})
    if r.status_code != 200:
        try:
            msg = r.json().get("detail", r.text)
        except ValueError:
            msg = r.text
        raise HTTPException(r.status_code if r.status_code < 500 else 502, str(msg)[:300])
    d = r.json()
    st = {"main_url": main_url, "id": d["id"], "name": d["name"], "secret": d["secret"]}
    save_state(st)
    return st


async def run() -> None:
    """Keep one connection open to the main server, for ever."""
    import websockets
    from websockets.exceptions import InvalidStatus

    assert status is not None
    backoff = 1.0
    while True:
        st = load_state()
        if not st.get("secret") and config.cfg.main_url and config.cfg.join_code:
            try:
                st = await join(config.cfg.main_url, config.cfg.join_code,
                                config.cfg.helper_name or socket.gethostname())
            except Exception as exc:
                status.set("waiting", f"Couldn't join {config.cfg.main_url}: {getattr(exc, 'detail', exc)}")
        if not st.get("secret"):
            status.wake.clear()
            await status.wake.wait()
            continue
        status.set("connecting", f"Connecting to {st['main_url']}")
        worker: Worker | None = None
        try:
            async with websockets.connect(ws_url(st["main_url"]), max_size=2 ** 22, ping_interval=20,
                                          additional_headers={"Authorization": f"Bearer {st['secret']}"}) as ws:
                lock = asyncio.Lock()

                async def send(msg: dict) -> None:
                    async with lock:
                        await ws.send(json.dumps(msg))

                worker = Worker(send)
                await send(worker.stats("hello"))
                status.set("connected", f"Connected to {st['main_url']}")
                backoff = 1.0

                async def ticker():
                    while True:
                        await asyncio.sleep(STATS_EVERY)
                        await send(worker.stats())

                tick = asyncio.create_task(ticker())
                try:
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            await worker.on_bytes(msg)
                        else:
                            await worker.on_text(json.loads(msg))
                finally:
                    tick.cancel()
        except InvalidStatus as exc:
            if exc.response.status_code == 403:
                state_file().unlink(missing_ok=True)
                status.set("removed", "The main server doesn't know this helper any more. Join it again with a new code.")
                continue
            status.set("offline", f"The main server answered {exc.response.status_code}; trying again")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network down, main restarting
            status.set("offline", f"Can't reach {st['main_url']} ({type(exc).__name__}); trying again")
        finally:
            if worker:
                await worker.close_all()
        if status.state == "connected":
            status.set("offline", f"Lost {st['main_url']}; trying again")
        await asyncio.sleep(backoff)
        backoff = min(30.0, backoff * 2)


class JoinIn(BaseModel):
    main_url: str = Field(min_length=8, max_length=300, pattern=r"^https?://[^\s/]+(:\d+)?/?$")
    code: str = Field(min_length=4, max_length=20)
    name: str = Field(min_length=1, max_length=60)


def create_helper_app() -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        global status
        config.cfg.ensure_dirs()
        asr.engine = asr.Engine()
        status = Status()
        task = asyncio.create_task(run())
        yield
        task.cancel()

    app = FastAPI(title="AT-SUIT helper", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "helper.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "brand" / "favicon.ico")

    @app.get("/api/helper/status")
    def helper_status():
        st = load_state()
        return {"role": "helper", "version": VERSION, "state": status.state, "detail": status.detail,
                "since": status.since, "main_url": st.get("main_url", ""), "name": st.get("name", ""),
                "joined": bool(st.get("secret")), "speech": asr.engine.status(), "load": round(load(), 2),
                "capacity": config.cfg.asr_max_rooms}

    @app.post("/api/helper/join")
    async def helper_join(body: JoinIn):
        # Only an unjoined helper takes a new main server: once joined, the
        # main server's admin removes it first (or the data folder is wiped).
        if load_state().get("secret"):
            raise HTTPException(409, "This helper has already joined a main server")
        st = await join(body.main_url.rstrip("/"), body.code, body.name.strip())
        status.wake.set()
        return {"ok": True, "name": st["name"]}

    @app.get("/api/health")
    def health():
        return {"ok": True, "role": "helper"}

    return app
