"""Backend tests — run with:  cd raspberry_pi && python -m pytest -q   (no ROS needed)."""
import io
import time

import pytest

from app import database, missions, ros_hub
from app.ros_hub import NavTracker


def wait_for(pred, timeout=5.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


# ── NavTracker (the state machine that replaces "fire and forget") ────────────

def test_tracker_ignores_stale_callbacks():
    t = NavTracker()
    g1 = t.begin("A", 0, 0, 0)
    g2 = t.begin("B", 1, 1, 0)
    assert t.set_state(g1, "succeeded") is False          # old goal must not touch new one
    assert t.state() == "sending"
    assert t.set_state(g2, "running") and t.state() == "running"


def test_tracker_cancel_not_overridden_by_running():
    t = NavTracker()
    g = t.begin("A", 0, 0, 0)
    assert t.request_cancel() == g
    t.set_state(g, "running")                             # late "accepted" callback
    assert t.state() == "canceling"
    t.set_state(g, "canceled")
    assert t.state() == "canceled" and not t.is_active()


def test_tracker_finished_goal_is_not_resurrected():
    t = NavTracker()
    g = t.begin("A", 0, 0, 0)
    t.set_state(g, "failed", "boom")
    assert t.set_state(g, "running") is False
    assert t.snapshot()["error"] == "boom"


# ── auth / health ─────────────────────────────────────────────────────────────

def test_login_works_with_bcrypt5(client):
    r = client.post("/auth/login", json={"username": "user", "password": "userpw"})
    assert r.status_code == 200 and r.json()["role"] == "user"
    assert client.post("/auth/login", json={"username": "user", "password": "x"}).status_code == 401


def test_health_matches_app_discovery_contract(client):
    j = client.get("/health").json()
    assert j["ok"] is True and j["service"] == "robot-control"


def test_requires_auth(client):
    r = client.get("/robot/status", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# ── robot lifecycle / status ──────────────────────────────────────────────────

def test_status_shape_and_honest_battery(client):
    s = client.get("/robot/status").json()
    for k in ("connection", "status", "ros_running", "pose", "is_navigating", "node_health",
              "bridge_status", "battery", "battery_available", "nav_ready", "localized", "nav"):
        assert k in s, k
    assert s["status"] == "idle" and s["battery_available"] is False and s["battery"] is None


def test_start_stop_cycle(client):
    assert client.post("/robot/start").json()["ok"]
    s = client.get("/robot/status").json()
    assert s["ros_running"] and s["status"] in ("running", "launching") and s["nav_ready"]
    assert s["bridge_status"]["ok"] and s["bridge_status"]["cmd_vel_subscriber"]
    assert client.post("/robot/ready").json()["status"] == "ready"
    assert client.post("/robot/stop").json()["status"] == "idle"
    assert client.get("/robot/status").json()["status"] == "idle"
    client.post("/robot/start")


def test_enable_motors_default_does_not_restart(client):
    r = client.post("/robot/enable-motors").json()
    assert r["ok"] and r["mode"] == "none"
    assert client.get("/robot/status").json()["ros_running"]


def test_status_never_spawns_ros2_cli(client, tmp_path, monkeypatch):
    """Regression: old /robot/status ran 3 `ros2` CLI calls and froze the server."""
    fake = tmp_path / "ros2"
    fake.write_text("#!/bin/bash\nsleep 3\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    t0 = time.time()
    for path in ("/robot/status", "/robot/bridge-status", "/teleop/debug", "/teleop/connection"):
        assert client.get(path).status_code == 200
    assert time.time() - t0 < 1.0


# ── destinations ──────────────────────────────────────────────────────────────

def test_destination_crud_and_validation(client):
    assert client.post("/destinations", json={"name": "Table 3", "x": 2.0, "y": 1.5, "theta": 90}).json()["ok"]
    assert client.post("/destinations", json={"name": "Door", "x": -1.0, "y": 0.5, "theta": 0}).status_code == 200
    names = [d["name"] for d in client.get("/destinations").json()["destinations"]]
    assert names == ["Table 3", "Door"]
    assert client.post("/destinations", json={"name": "  ", "x": 0, "y": 0, "theta": 0}).status_code == 422
    assert client.post("/destinations", json={"name": "x" * 80, "x": 0, "y": 0, "theta": 0}).status_code == 422
    assert client.delete("/destinations/nope").status_code == 404


# ── navigation ────────────────────────────────────────────────────────────────

def test_goal_unknown_destination_404(client):
    assert client.post("/navigation/goal", json={"destination": "Nowhere"}).status_code == 404


def test_goal_runs_to_success_and_robot_moves(client):
    r = client.post("/navigation/goal", json={"destination": "Table 3"})
    assert r.status_code == 200 and r.json()["state"] in ("running", "succeeded")
    assert wait_for(lambda: client.get("/navigation/status").json()["state"] == "succeeded")
    pose = client.get("/robot/status").json()["pose"]
    assert (pose["x"], pose["y"], pose["theta"]) == (2.0, 1.5, 90.0)


def test_goal_is_cancellable(client, monkeypatch):
    monkeypatch.setattr(ros_hub, "SIM_SPEED", 0.5)         # slow: ~6 s trip
    assert client.post("/navigation/goal", json={"destination": "Door"}).status_code == 200
    s = client.get("/robot/status").json()
    assert s["is_navigating"] and s["status"] == "navigating" and s["destination"] == "Door"
    assert client.post("/navigation/cancel").json()["cancelled"] is True
    assert wait_for(lambda: client.get("/navigation/status").json()["state"] == "canceled")
    assert client.get("/robot/status").json()["is_navigating"] is False


def test_goal_failure_is_reported_not_swallowed(client, monkeypatch):
    """Regression: the old API answered ok:true even when Nav2 was unavailable."""
    monkeypatch.setattr(ros_hub, "REQUIRE_LOCALIZED", True)
    monkeypatch.setattr(ros_hub.hub, "localization",
                        lambda: {"have_pose": False, "cov_xy": None, "localized": False})
    r = client.post("/navigation/goal", json={"destination": "Door"})
    assert r.status_code == 503 and "localized" in r.json()["detail"]
    st = client.get("/navigation/status").json()
    assert st["state"] == "unavailable" and "localized" in st["error"]


def test_initial_pose_sets_pose_and_validates(client):
    r = client.post("/navigation/initial-pose", json={"x": 0.5, "y": -0.5, "theta": 45})
    assert r.status_code == 200
    assert client.get("/robot/status").json()["pose"] == {"x": 0.5, "y": -0.5, "theta": 45.0}
    assert client.post("/navigation/initial-pose", json={"x": "nan", "y": 0}).status_code in (200, 422)


# ── missions ──────────────────────────────────────────────────────────────────

def mission_done(client, mid, timeout=15):
    return wait_for(lambda: client.get(f"/missions/{mid}").json()["status"]
                    in ("completed", "failed", "cancelled"), timeout)


def test_mission_success_has_steps_and_progress(client):
    m = client.post("/missions", json={"destinations": ["Table 3", "Door"]}).json()
    assert m["status"] in ("pending", "in_progress")
    assert [s["destination"] for s in m["steps"]] == ["Table 3", "Door"]      # app reads this
    assert mission_done(client, m["id"])
    m = client.get(f"/missions/{m['id']}").json()
    assert m["status"] == "completed" and m["progress"] == "2/2"
    assert all(s["status"] == "completed" for s in m["steps"])
    assert client.get("/robot/status").json()["current_mission"] is None


def test_mission_failure_is_not_reported_as_completed(client, monkeypatch):
    """Regression: old runner marked missions 'completed' when nothing moved."""
    monkeypatch.setattr(ros_hub.hub, "send_nav_goal",
                        lambda *a, **k: {"ok": False, "state": "unavailable", "error": "Nav2 down"})
    m = client.post("/missions", json={"destinations": ["Table 3", "Door"]}).json()
    assert mission_done(client, m["id"])
    m = client.get(f"/missions/{m['id']}").json()
    assert m["status"] == "failed" and "Nav2 down" in m["error"]
    assert [s["status"] for s in m["steps"]] == ["failed", "cancelled"]


def test_mission_cancel(client, monkeypatch):
    monkeypatch.setattr(ros_hub, "SIM_SPEED", 0.3)
    m = client.post("/missions", json={"destinations": ["Door", "Table 3"]}).json()
    assert wait_for(lambda: client.get("/robot/status").json()["current_mission"] == m["id"])
    assert client.post("/navigation/goal", json={"destination": "Door"}).status_code == 409
    assert client.post(f"/missions/{m['id']}/cancel").json()["ok"]
    assert mission_done(client, m["id"])
    assert client.get(f"/missions/{m['id']}").json()["status"] == "cancelled"
    assert client.post("/navigation/cancel").status_code == 200


def test_mission_validation(client):
    assert client.post("/missions", json={"destinations": []}).status_code == 422
    assert client.post("/missions", json={"destinations": ["Nope"]}).status_code == 404
    assert client.get("/missions/9999").status_code == 404


# ── teleop ────────────────────────────────────────────────────────────────────

def test_teleop_requires_arming_and_reports_it(client):
    client.post("/teleop/enable", json={"enabled": False})
    r = client.post("/teleop/cmd_vel", json={"linear": 0.3, "angular": 0}).json()
    assert r["armed"] is False and r["applied"] is False      # app can now tell the user


def test_teleop_arm_clamp_stale_seq_and_watchdog(client):
    e = client.post("/teleop/enable", json={"enabled": True}).json()
    assert e["teleop_armed"] and e["relay_alive"] and e["cmd_vel_subscriber"]
    r = client.post("/teleop/cmd_vel", json={"linear": 9.0, "angular": 9.0, "seq": 1}).json()
    assert r["applied"] and r["linear"] == ros_hub.MAX_LINEAR and r["angular"] == ros_hub.MAX_ANGULAR
    client.post("/teleop/cmd_vel", json={"linear": 0.0, "angular": 0.0, "seq": 3})
    late = client.post("/teleop/cmd_vel", json={"linear": 0.5, "angular": 0, "seq": 2}).json()
    assert late["applied"] is False and late["reason"] == "stale"     # late "go" can't beat "stop"
    assert ros_hub.hub._teleop_output() == (0.0, 0.0)
    client.post("/teleop/cmd_vel", json={"linear": 0.4, "angular": 0, "seq": 4})
    assert ros_hub.hub._teleop_output() == (0.4, 0.0)
    time.sleep(ros_hub.TELEOP_STALE_S + 0.1)
    assert ros_hub.hub._teleop_output() == (0.0, 0.0)                 # watchdog: silence => stop
    assert client.post("/teleop/test-drive").json()["ok"]
    client.post("/teleop/enable", json={"enabled": False})
    assert ros_hub.hub._teleop_output() is None                       # disarmed => hub is silent


def test_test_drive_needs_arming(client):
    client.post("/teleop/enable", json={"enabled": False})
    r = client.post("/teleop/test-drive").json()
    assert r["ok"] is False and "Arm" in r["hint"]


def test_arming_joystick_cancels_navigation(client, monkeypatch):
    monkeypatch.setattr(ros_hub, "SIM_SPEED", 0.5)
    client.post("/navigation/goal", json={"destination": "Table 3"})
    client.post("/teleop/enable", json={"enabled": True})
    assert wait_for(lambda: not client.get("/robot/status").json()["is_navigating"])
    client.post("/teleop/enable", json={"enabled": False})


def test_goal_disarms_joystick(client):
    client.post("/teleop/enable", json={"enabled": True})
    client.post("/navigation/goal", json={"destination": "Door"})
    assert ros_hub.hub.teleop_armed() is False
    client.post("/navigation/cancel")


# ── map ───────────────────────────────────────────────────────────────────────

def _write_map(tmp_path, w=40, h=30, name="m"):
    from PIL import Image
    img = Image.new("L", (w, h), 254)
    img.putpixel((0, 0), 0)                          # top-left occupied
    img.save(tmp_path / f"{name}.pgm")
    (tmp_path / f"{name}.yaml").write_text(
        f"image: {name}.pgm\nresolution: 0.05\norigin: [-1.0, -2.0, 0.0]\nnegate: 0\n")
    return tmp_path / f"{name}.yaml"


def test_map_from_file_is_served_as_png(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOT_MAP_YAML", str(_write_map(tmp_path)))
    meta = client.get("/navigation/map").json()
    assert (meta["width"], meta["height"], meta["source"]) == (40, 30, "file")
    assert meta["origin"]["x"] == -1.0 and meta["resolution"] == 0.05
    r = client.get("/navigation/map/image")
    assert r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"


def test_live_map_wins_and_mismatch_is_flagged(client, tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setenv("ROBOT_MAP_YAML", str(_write_map(tmp_path, 40, 30, "file")))
    w, h = 20, 10
    data = bytearray([0] * (w * h))
    data[0] = 100                      # occupied at ROS bottom-left
    data[1] = 255                      # unknown (-1 as unsigned)
    meta = {"image_url": "/navigation/map/image", "resolution": 0.05,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "width": w, "height": h,
            "frame_id": "map", "negate": 0}
    ros_hub.hub._on_map(meta, bytes(data))
    try:
        m = client.get("/navigation/map").json()
        assert m["source"] == "live" and (m["width"], m["height"]) == (20, 10)
        assert "differs" in m["warning"]                       # my_map vs my_map1 problem
        img = Image.open(io.BytesIO(client.get("/navigation/map/image").content))
        assert img.size == (20, 10)
        assert img.getpixel((0, h - 1)) == 0                  # row 0 of ROS data = image bottom
        assert img.getpixel((1, h - 1)) == 205                # unknown
        assert img.getpixel((5, 0)) == 254                    # free
    finally:
        ros_hub.hub._map = None


def test_no_map_gives_clear_404(client, monkeypatch):
    monkeypatch.delenv("ROBOT_MAP_YAML", raising=False)
    monkeypatch.delenv("REAL1_WS", raising=False)
    r = client.get("/navigation/map")
    assert r.status_code == 404 and "No map available" in r.json()["detail"]


# ── logs ──────────────────────────────────────────────────────────────────────

def test_log_severity_is_parsed(client):
    from app import robot_link
    robot_link._append_log("[controller_server-5] [ERROR] [1.0]: Failed to make progress")
    robot_link._append_log("[amcl-3] [WARN] [1.0]: no scan")
    robot_link._append_log("[amcl-3] [INFO] [1.0]: ok")
    errs = client.get("/logs", params={"severity": "error"}).json()["logs"]
    assert errs and all(l["severity"] == "ERROR" for l in errs)
    assert client.get("/logs", params={"severity": "warning"}).json()["count"] >= 1


# ── persistence ───────────────────────────────────────────────────────────────

def test_destinations_file_is_valid_json_after_writes(client):
    import json
    for i in range(20):
        database.upsert_destination(f"P{i}", i, i, 0)
    assert len(json.loads(database.DESTINATIONS_PATH.read_text())) >= 20
    for i in range(20):
        database.delete_destination(f"P{i}")


# ── the original lag bug: polling + joystick together ────────────────────────

def test_polling_and_joystick_do_not_queue_up(client):
    import threading
    client.post("/teleop/enable", json={"enabled": True})
    lat, stop = [], threading.Event()

    def poll():
        while not stop.is_set():
            client.get("/robot/status"); client.get("/robot/bridge-status")
            time.sleep(0.2)

    th = threading.Thread(target=poll); th.start()
    try:
        for i in range(40):
            t = time.time()
            client.post("/teleop/cmd_vel", json={"linear": 0.2, "angular": 0, "seq": 100 + i})
            lat.append(time.time() - t)
            time.sleep(0.06)
    finally:
        stop.set(); th.join()
        client.post("/teleop/enable", json={"enabled": False})
    assert max(lat) < 0.5, f"joystick latency spiked: {max(lat):.2f}s"
