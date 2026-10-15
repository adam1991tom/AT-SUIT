"""Laptop health (the console's task manager) and screen snapshots."""
import pytest

from atsuit.modules import fleet

SYS = {"cpu": 37, "mem_total": 16_000_000_000, "mem_free": 4_000_000_000, "app_mem": 300_000_000,
       "disk": {"drive": "C:\\", "total": 500_000_000_000, "free": 120_000_000_000},
       "net": [{"name": "Ethernet", "ip": "10.0.0.21"}], "online": True, "displays": [{"w": 1920, "h": 1080, "primary": True}]}
SHOT = {"name": "Screen", "image": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="}


@pytest.fixture(autouse=True)
def _clean():
    for d in (fleet._history, fleet._snaps, fleet._watched):
        d.clear()
    yield


def _laptop(admin, name="hd-main"):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": name, "kind": "tech"}).json()
    return n["node_id"], {"Authorization": f"Node {n['token']}"}


def _beat(admin, hdr, **sysinfo):
    r = admin.post("/api/nodes/heartbeat", headers=hdr, json={"version": "1.8.0", "info": {"system": {**SYS, **sysinfo}}})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_is_reported_and_kept_for_graphs(admin):
    nid, hdr = _laptop(admin)
    _beat(admin, hdr, cpu=20)
    _beat(admin, hdr, cpu=60)
    h = admin.get(f"/api/fleet/nodes/{nid}/health").json()
    assert h["system"]["cpu"] == 60 and h["system"]["disk"]["free"] == 120_000_000_000
    assert [x["cpu"] for x in h["history"]] == [20, 60] and h["history"][0]["mem"] == 75
    assert h["snapshots"] is False and h["snapshot"] is None
    # The list shows the latest numbers too.
    row = next(n for n in admin.get("/api/fleet/nodes").json() if n["id"] == nid)
    assert row["info"]["system"]["cpu"] == 60

    # Only the last 15 minutes.
    for i in range(fleet.HISTORY + 5):
        _beat(admin, hdr, cpu=i % 100)
    assert len(admin.get(f"/api/fleet/nodes/{nid}/health").json()["history"]) == fleet.HISTORY

    # Laptops can't read it, and removing a laptop forgets it.
    assert admin.get(f"/api/fleet/nodes/{nid}/health", headers=hdr).status_code in (401, 403)
    assert admin.get("/api/fleet/nodes/99999/health").status_code == 404
    admin.delete(f"/api/fleet/nodes/{nid}")
    assert nid not in fleet._history


def test_snapshots_off_by_default(admin):
    nid, hdr = _laptop(admin)
    assert admin.get("/api/admin/settings").json()["laptop_snapshots"] is False
    admin.get(f"/api/fleet/nodes/{nid}/health")  # a manager looking doesn't turn them on
    assert _beat(admin, hdr)["snapshot"] is False
    r = admin.post("/api/nodes/me/snapshot", headers=hdr, json={"shots": [SHOT]})
    assert r.status_code == 403
    assert nid not in fleet._snaps


def test_snapshots_only_while_a_manager_looks(admin, monkeypatch):
    nid, hdr = _laptop(admin)
    assert admin.put("/api/admin/settings", json={"laptop_snapshots": True}).status_code == 200

    # Nobody looking: the laptop isn't asked, and anything it sends is dropped.
    assert _beat(admin, hdr)["snapshot"] is False
    assert admin.post("/api/nodes/me/snapshot", headers=hdr, json={"shots": [SHOT]}).json()["snapshot"] is False
    assert nid not in fleet._snaps

    # A manager opens the laptop: the next heartbeat asks for snapshots.
    h = admin.get(f"/api/fleet/nodes/{nid}/health").json()
    assert h["snapshots"] is True and h["snapshot"] is None
    assert _beat(admin, hdr)["snapshot"] is True
    assert admin.post("/api/nodes/me/snapshot", headers=hdr, json={"shots": [SHOT, {**SHOT, "name": "Screen 2"}]}).json()["snapshot"] is True
    h = admin.get(f"/api/fleet/nodes/{nid}/health").json()
    assert [s["name"] for s in h["snapshot"]["shots"]] == ["Screen", "Screen 2"]

    # They stop once the manager closes it (no more asks for WATCH_SECONDS).
    later = fleet.time.time() + fleet.WATCH_SECONDS + 1
    monkeypatch.setattr(fleet.time, "time", lambda: later)
    assert _beat(admin, hdr)["snapshot"] is False

    # Turning snapshots off drops what's held.
    monkeypatch.undo()
    admin.put("/api/admin/settings", json={"laptop_snapshots": False})
    assert admin.get(f"/api/fleet/nodes/{nid}/health").json()["snapshot"] is None
    assert nid not in fleet._snaps


def test_snapshot_input_is_checked(admin):
    nid, hdr = _laptop(admin)
    admin.put("/api/admin/settings", json={"laptop_snapshots": True})
    admin.get(f"/api/fleet/nodes/{nid}/health")
    post = lambda shots: admin.post("/api/nodes/me/snapshot", headers=hdr, json={"shots": shots}).status_code
    assert post([{**SHOT, "image": "https://example.com/x.jpg"}]) == 422
    assert post([{**SHOT, "image": "data:image/jpeg;base64," + "A" * fleet.SNAP_BYTES}]) == 422
    assert post([SHOT] * (fleet.SNAP_MAX + 1)) == 422
    assert admin.post("/api/nodes/me/snapshot", json={"shots": [SHOT]}).status_code in (401, 403)  # people can't send them
