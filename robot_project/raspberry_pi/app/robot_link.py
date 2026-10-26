"""
robot_link.py — starts/stops the articubot_one launch and reports robot status.

Differences from the old version
  * start/stop/enable-motors NEVER block the API: the work runs in a thread and
    the HTTP call returns immediately (the app polls /robot/status).
  * stop sends SIGINT to the whole process group (ros2 launch + every node it
    started), then SIGTERM, then SIGKILL — no orphaned rplidar/nav2 nodes.
  * NO ``ros2`` CLI calls.  Node health, /cmd_vel subscribers and Nav2 state come
    from the hub's cached graph, so /robot/status answers instantly.
  * "running" means the stack is really up (expected nodes visible), not merely
    that a log line contained the word "nav2".
  * A stack started outside the app (another terminal) is detected and reused,
    which prevents accidental double launches.
  * Log severity is parsed from the ROS log prefix instead of always "INFO".
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from . import ros_hub
from .settings import env_float, env_str

# ── Config ────────────────────────────────────────────────────────────────────

_REAL1_WS = env_str("REAL1_WS")
_EXTRA_SETUP = env_str("REAL1_EXTRA_SETUP")
_ROS_SETUP = env_str("REAL1_ROS_SETUP", "/opt/ros/humble/setup.bash")
_START_CMD = env_str("REAL1_START_CMD", "ros2 launch articubot_one master.launch.py")
_ENABLE_MODE = env_str("REAL1_ENABLE_MOTORS_MODE", "none")
_ENABLE_CMD = env_str("REAL1_ENABLE_MOTORS_CMD")
_LAUNCH_GRACE_S = env_float("ROBOT_LAUNCH_GRACE_S", 45.0)

# ── State ─────────────────────────────────────────────────────────────────────

_lock = threading.RLock()
_robot: dict[str, Any] = {
    "proc": None,
    "pid": None,
    "status": "idle",         # idle | launching | running | stopping | error
    "message": "Robot not started",
    "launch_command": _START_CMD,
    "ready": False,
    "starting": False,
    "started_at": None,
    "sim_running": False,
}
_logs: deque[dict[str, Any]] = deque(maxlen=500)

_SEV_RE = re.compile(r"\[(FATAL|ERROR|WARN|WARNING|INFO|DEBUG)\]", re.IGNORECASE)


def _append_log(line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    m = _SEV_RE.search(line)
    sev = m.group(1).upper() if m else "INFO"
    if sev == "WARNING":
        sev = "WARN"
    if "traceback" in line.lower() and not m:
        sev = "ERROR"
    with _lock:
        _logs.append({"severity": sev, "message": line, "ts": time.time()})


# ── Launch environment ────────────────────────────────────────────────────────

def _build_launch_env() -> dict[str, str]:
    """Environment with ROS + the robot workspace sourced (parsed from `env -0`)."""
    steps = []
    if _ROS_SETUP and Path(_ROS_SETUP).exists():
        steps.append(f"source {shlex.quote(_ROS_SETUP)}")
    if _EXTRA_SETUP and Path(_EXTRA_SETUP).exists():
        steps.append(f"source {shlex.quote(_EXTRA_SETUP)}")
    if _REAL1_WS and Path(f"{_REAL1_WS}/install/setup.bash").exists():
        steps.append(f"source {shlex.quote(_REAL1_WS + '/install/setup.bash')}")
    if not steps:
        return dict(os.environ)
    res = subprocess.run(
        ["bash", "-c", " && ".join(steps) + " && env -0"],
        capture_output=True, timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError("Sourcing the ROS workspace failed: "
                           + res.stderr.decode(errors="replace")[-300:])
    env: dict[str, str] = {}
    for item in res.stdout.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep and k:
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


# ── Process control ───────────────────────────────────────────────────────────

def _proc_alive() -> bool:
    with _lock:
        if ros_hub.hub.sim:
            return bool(_robot["sim_running"])
        proc = _robot["proc"]
    return proc is not None and proc.poll() is None


def _stack_visible() -> bool:
    """True when every expected node is in the ROS graph (stack is up)."""
    g = ros_hub.hub.graph()
    if g.get("updated", 0) == 0:
        return False
    nodes = set(g["nodes"])
    return all(n in nodes for n in ros_hub.EXPECTED_NODES) if ros_hub.EXPECTED_NODES else False


def start_robot() -> dict[str, Any]:
    hub = ros_hub.hub
    with _lock:
        if _robot["starting"] or (_proc_alive() and _robot["status"] in ("launching", "running")):
            return {"ok": True, "status": _robot["status"], "message": "Already starting/running"}
        if not _proc_alive() and _stack_visible():
            _robot.update(status="running", ready=False,
                          message="Robot stack already running (started outside the app)")
            return {"ok": True, "status": "running", "message": _robot["message"]}
        _robot.update(status="launching", ready=False, starting=True,
                      message="Launching robot stack…", started_at=time.time())
    if hub.sim:
        with _lock:
            _robot.update(sim_running=True, starting=False, status="running",
                          message="Simulation: robot stack running")
        hub.set_sim_stack(True)
        return {"ok": True, "status": "running"}
    threading.Thread(target=_launch_worker, name="robot-launch", daemon=True).start()
    return {"ok": True, "status": "launching"}


def _launch_worker() -> None:
    try:
        env = _build_launch_env()
        proc = subprocess.Popen(
            shlex.split(_START_CMD),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
            start_new_session=True,          # own process group → clean shutdown
        )
    except Exception as exc:
        with _lock:
            _robot.update(status="error", starting=False, message=f"Launch failed: {exc}")
        _append_log(f"[ERROR] launch failed: {exc}")
        return
    with _lock:
        _robot.update(proc=proc, pid=proc.pid, starting=False,
                      message="Launching robot stack…")
    _append_log(f"[INFO] started: {_START_CMD} (pid {proc.pid})")
    threading.Thread(target=_read_output, args=(proc,), daemon=True).start()


def _read_output(proc: "subprocess.Popen[str]") -> None:
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _append_log(line)
    finally:
        rc = proc.wait()
        with _lock:
            if _robot["proc"] is proc:
                stopping = _robot["status"] == "stopping"
                _robot.update(proc=None, pid=None, ready=False)
                if stopping or rc in (0, -signal.SIGINT, -signal.SIGTERM):
                    _robot.update(status="idle", message="Robot not started")
                else:
                    _robot.update(status="error",
                                  message=f"Launch exited with code {rc} — see /logs")
        _append_log(f"[INFO] launch process exited (code {rc})")


def stop_robot() -> dict[str, Any]:
    ros_hub.hub.teleop_arm(False)
    ros_hub.hub.cancel_nav_goal()
    with _lock:
        proc = _robot["proc"]
        if ros_hub.hub.sim:
            _robot.update(sim_running=False, status="idle", ready=False,
                          message="Robot not started")
            ros_hub.hub.set_sim_stack(False)
            return {"ok": True, "status": "idle"}
        if proc is None or proc.poll() is not None:
            if _stack_visible():
                return {"ok": False, "status": "running",
                        "message": "The robot stack was started outside the app — "
                                   "stop it in the terminal where it was launched."}
            _robot.update(status="idle", ready=False, message="Robot not started")
            return {"ok": True, "status": "idle"}
        _robot.update(status="stopping", ready=False, message="Stopping robot stack…")
    threading.Thread(target=_stop_worker, args=(proc,), name="robot-stop", daemon=True).start()
    return {"ok": True, "status": "stopping"}


def _stop_worker(proc: "subprocess.Popen[str]") -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig, wait_s in ((signal.SIGINT, 15), (signal.SIGTERM, 8), (signal.SIGKILL, 3)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        try:
            proc.wait(timeout=wait_s)
            break
        except subprocess.TimeoutExpired:
            _append_log(f"[WARN] stack still alive after {sig.name}, escalating")
    with _lock:
        if _robot["proc"] is proc or _robot["proc"] is None:
            _robot.update(proc=None, pid=None, status="idle", ready=False,
                          message="Robot not started")


def shutdown() -> None:
    """Called when the API exits: don't leave a ROS stack running unsupervised."""
    if os.getenv("ROBOT_STOP_STACK_ON_EXIT", "1") == "0":
        return
    with _lock:
        proc = _robot["proc"]
    if proc is not None and proc.poll() is None:
        _stop_worker(proc)


def set_ready() -> dict[str, Any]:
    with _lock:
        if _proc_alive() or _stack_visible():
            _robot.update(ready=True, message="Ready for work")
        else:
            _robot["message"] = "Start the robot first"
    return get_status()


def enable_motors() -> dict[str, Any]:
    """
    REAL1_ENABLE_MOTORS_MODE
      none            (default) ros2_control starts with the launch; nothing to do
      restart_launch  stop + start the whole stack in the background (≈1 min)
                      (alias: restart_bridge — the old behaviour)
      script          run REAL1_ENABLE_MOTORS_CMD and report its exit code
    """
    mode = _ENABLE_MODE
    if mode == "none":
        return {"ok": True, "mode": "none",
                "hint": "No separate motor-enable step is configured for this robot."}
    if mode in ("restart_launch", "restart_bridge"):
        def worker() -> None:
            stop_robot()
            for _ in range(60):
                with _lock:
                    if _robot["status"] != "stopping" and _robot["proc"] is None:
                        break
                time.sleep(0.5)
            start_robot()
        threading.Thread(target=worker, name="robot-restart", daemon=True).start()
        return {"ok": True, "mode": mode, "pending": True,
                "hint": "Robot stack restarting — wait until the status is running again "
                        "(about a minute) before driving or navigating."}
    if mode == "script" and _ENABLE_CMD:
        try:
            res = subprocess.run(shlex.split(_ENABLE_CMD), capture_output=True,
                                 text=True, timeout=30, env=_build_launch_env())
        except Exception as exc:
            return {"ok": False, "mode": mode, "hint": f"Enable script failed: {exc}"}
        return {"ok": res.returncode == 0, "mode": mode,
                "hint": (res.stdout or res.stderr).strip()[-300:] or "done"}
    return {"ok": False, "hint": f"Unknown REAL1_ENABLE_MOTORS_MODE '{mode}'"}


# ── Status (cache reads only — no subprocesses, no blocking) ─────────────────

def get_bridge_status() -> dict[str, Any]:
    g = ros_hub.hub.graph()
    alive = _proc_alive()
    nodes = set(g["nodes"])
    missing = [n for n in ros_hub.EXPECTED_NODES if n not in nodes]
    stack_up = bool(ros_hub.EXPECTED_NODES) and not missing and g.get("updated", 0) > 0
    has_sub = g["cmd_vel_subs"] > 0
    active = alive or stack_up
    ok = active and (has_sub or stack_up)
    if ok:
        hint = ""
    elif not active:
        hint = "Robot not started — press START"
    elif g.get("updated", 0) == 0:
        hint = "Waiting for the ROS graph…"
    else:
        hint = "Stack is starting; missing nodes: " + ", ".join(missing or ["(none)"])
    with _lock:
        pid = _robot["pid"]
    return {
        "ok": ok,
        "process_alive": alive,
        "ros_node_visible": stack_up,
        "cmd_vel_subscriber": has_sub,
        "bridge_subscriptions": g["cmd_vel_sub_nodes"],
        "missing_nodes": missing,
        "graph_age_s": g.get("age"),
        "cmd_vel_topic": ros_hub.CMD_VEL_TOPIC,
        "pid": pid,
        "launch_command": _START_CMD,
        "hint": hint,
        "warming_up": active and not ok,
    }


def _refresh_launch_status() -> None:
    """launching → running once the stack is really up (or after a grace period)."""
    with _lock:
        if _robot["status"] != "launching" or _robot["starting"]:
            return
        started = _robot["started_at"] or time.time()
    if _stack_visible() or ros_hub.hub.graph()["cmd_vel_subs"] > 0 \
            or time.time() - started > _LAUNCH_GRACE_S:
        with _lock:
            if _robot["status"] == "launching":
                _robot.update(status="running", message="Robot stack running")


def _node_health() -> dict[str, str]:
    nodes = set(ros_hub.hub.graph()["nodes"])
    return {n: ("running" if n in nodes else "stopped") for n in ros_hub.EXPECTED_NODES}


def get_status() -> dict[str, Any]:
    _refresh_launch_status()
    hub = ros_hub.hub
    alive = _proc_alive()
    external = (not alive) and _stack_visible()
    with _lock:
        s = {k: _robot[k] for k in ("status", "message", "ready")}
    nav = hub.nav.snapshot()
    navigating = nav["state"] in ("sending", "running", "canceling")
    loc = hub.localization()
    batt = hub.battery()
    running = alive or external

    if navigating:
        status = "navigating"
        dist = nav.get("distance_remaining")
        message = f"Navigating to {nav['destination']}" + (
            f" — {dist:.1f} m left" if isinstance(dist, (int, float)) else "")
    elif running and s["ready"]:
        status, message = "ready", "Ready for work"
    elif running and s["status"] in ("launching", "stopping", "error"):
        status, message = s["status"], s["message"]
    elif running:
        status = "running"
        message = ("Robot stack detected (started outside the app)" if external
                   else s["message"])
    elif s["status"] in ("launching", "stopping", "error"):
        status, message = s["status"], s["message"]
    else:
        status, message = "idle", "Robot not started"

    try:
        from . import missions
        mission_id = missions.current_mission_id()
    except Exception:
        mission_id = None

    return {
        "connection": "connected",
        "status": status,
        "ros_running": running,
        "message": message,
        "pose": hub.get_pose(),
        "destination": hub.get_nav_target(),
        "is_navigating": navigating,
        "current_action": "teleop" if hub.teleop_armed() else ("navigating" if navigating else None),
        "node_health": _node_health(),
        "bridge_status": get_bridge_status(),
        # new, honest fields
        "nav": nav,
        "nav_ready": hub.nav_ready(),
        "localized": loc["localized"],
        "sim": hub.sim,
        # battery: only real data; the app shows "N/A" when unavailable
        "battery": batt["percent"],
        "battery_voltage": batt["voltage"],
        "battery_alert": batt["alert"] if batt["available"] else "normal",
        "battery_available": batt["available"],
        "current_mission": mission_id,
    }


def get_link_check() -> dict[str, Any]:
    ws_ok = bool(_REAL1_WS) and Path(f"{_REAL1_WS}/install/setup.bash").exists()
    hub = ros_hub.hub
    ros_ok = ros_hub.ROS_AVAILABLE
    hint = []
    if not ros_ok and not hub.sim:
        hint.append(f"API process cannot import ROS ({ros_hub.IMPORT_ERROR}) — start via run_server_ros.sh")
    if not ws_ok:
        hint.append("REAL1_WS is not set or the workspace is not built (install/setup.bash missing)")
    return {
        "ok": ws_ok and (ros_ok or hub.sim),
        "REAL1_WS": _REAL1_WS or "(not set)",
        "ros_available": ros_ok,
        "sim": hub.sim,
        "import_error": ros_hub.IMPORT_ERROR or None,
        "hint": "; ".join(hint) or None,
    }


def get_logs(severity: Optional[str] = None, limit: int = 100) -> list[dict]:
    with _lock:
        logs = list(_logs)
    if severity:
        want = severity.upper()
        want = "WARN" if want == "WARNING" else want
        logs = [l for l in logs if l["severity"] == want]
    return logs[-max(1, min(limit, 500)):]
