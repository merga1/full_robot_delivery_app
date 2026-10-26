"""
ros_hub.py — the ONLY module that talks to ROS 2 from inside the API process.

Design rules (these fix the lag / "it doesn't move" problems of the old hub):

1. ONE rclpy node + executor runs in a background thread.  HTTP handlers never
   call rclpy directly; they enqueue commands that the node thread executes
   (rclpy is not fully thread-safe).
2. NO ``ros2`` CLI subprocesses.  Graph information (nodes, /cmd_vel
   subscribers, Nav2 action server, bt_navigator lifecycle state) is sampled
   once per second inside the node and cached.  Status endpoints only read
   the cache, so they answer in microseconds.
3. Navigation uses an in-process ``NavigateToPose`` action client.  The API
   knows whether Nav2 accepted, rejected, aborted or finished the goal, and
   reports it to the app (state machine: NavTracker).
4. The hub publishes /cmd_vel ONLY while the joystick is armed (20 Hz, with a
   0.5 s staleness cut-off).  When disarmed it is silent, so it never fights
   Nav2 on the same topic.
5. If rclpy is missing the server REFUSES to start (unless ROBOT_ALLOW_SIM=1),
   instead of silently pretending everything works.
"""

from __future__ import annotations

import io
import logging
import math
import queue
import threading
import time
from typing import Any, Optional

from .settings import EXPECTED_NODES_DEFAULT, env_bool, env_float, env_list, env_str

log = logging.getLogger("robot.hub")

# ── Optional ROS imports ──────────────────────────────────────────────────────

IMPORT_ERROR = ""
try:
    import rclpy
    from action_msgs.msg import GoalStatus
    from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
    from lifecycle_msgs.srv import GetState
    from nav2_msgs.action import NavigateToPose
    from nav_msgs.msg import OccupancyGrid
    from rclpy.action import ActionClient
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

    ROS_AVAILABLE = True
except Exception as exc:  # ImportError, or a broken ROS install
    ROS_AVAILABLE = False
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

# ── Configuration ─────────────────────────────────────────────────────────────

CMD_VEL_TOPIC = env_str("REAL1_CMD_VEL_TOPIC", "/cmd_vel")
LINEAR_SCALE = env_float("REAL1_CMD_VEL_LINEAR_SCALE", 1.0)
ANGULAR_SCALE = env_float("REAL1_CMD_VEL_ANGULAR_SCALE", 1.0)
MAX_LINEAR = env_float("ROBOT_MAX_LINEAR", 0.6)      # m/s safety clamp (before scale)
MAX_ANGULAR = env_float("ROBOT_MAX_ANGULAR", 1.5)    # rad/s safety clamp
TELEOP_STALE_S = env_float("ROBOT_TELEOP_STALE_S", 0.5)

AMCL_TOPIC = env_str("ROBOT_AMCL_TOPIC", "/amcl_pose")
MAP_TOPIC = env_str("ROBOT_MAP_TOPIC", "/map")
INITIALPOSE_TOPIC = env_str("ROBOT_INITIALPOSE_TOPIC", "/initialpose")
NAV_ACTION = env_str("ROBOT_NAV_ACTION", "navigate_to_pose")
NAV_LIFECYCLE_NODE = env_str("ROBOT_NAV_LIFECYCLE_NODE", "/bt_navigator")
BATTERY_TOPIC = env_str("ROBOT_BATTERY_TOPIC", "")   # optional sensor_msgs/BatteryState

REQUIRE_LOCALIZED = env_bool("ROBOT_REQUIRE_LOCALIZED", True)
LOCALIZED_MAX_COV = env_float("ROBOT_LOCALIZED_MAX_COV", 0.5)  # m^2 (x/y variance)
EXPECTED_NODES = env_list("ROBOT_EXPECTED_NODES", EXPECTED_NODES_DEFAULT)
SIM_SPEED = env_float("ROBOT_SIM_SPEED", 0.5)        # m/s, simulation mode only


def _yaw_to_quat(yaw: float) -> tuple[float, float]:
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)  # (z, w)


def _quat_to_yaw(z: float, w: float, x: float = 0.0, y: float = 0.0) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


# ── Navigation state machine (pure Python — unit-testable without ROS) ────────

class NavTracker:
    """
    Tracks the lifecycle of the current navigation goal.

    state: idle | sending | running | canceling      (ACTIVE)
           succeeded | failed | rejected | canceled | unavailable   (FINISHED)
    Callbacks from an older goal are ignored via goal_id.
    """

    ACTIVE = ("sending", "running", "canceling")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._goal_id = 0
        self._cancel_req = False
        self._d = self._blank(0)

    @staticmethod
    def _blank(goal_id: int) -> dict[str, Any]:
        return {
            "state": "idle",
            "goal_id": goal_id,
            "destination": None,
            "target": None,
            "distance_remaining": None,
            "recoveries": 0,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }

    def begin(self, name: str, x: float, y: float, theta: float) -> int:
        with self._lock:
            self._goal_id += 1
            self._cancel_req = False
            d = self._blank(self._goal_id)
            d.update(
                state="sending",
                destination=name,
                target={"x": x, "y": y, "theta": theta},
                started_at=time.time(),
            )
            self._d = d
            return self._goal_id

    def fail_now(self, name: str, x: float, y: float, theta: float,
                 state: str, error: str) -> int:
        gid = self.begin(name, x, y, theta)
        self.set_state(gid, state, error)
        return gid

    def set_state(self, goal_id: int, state: str, error: Optional[str] = None) -> bool:
        with self._lock:
            if goal_id != self._goal_id:
                return False  # stale callback from a previous goal
            cur = self._d["state"]
            if state == "running" and cur == "canceling":
                return True   # keep "canceling"
            if cur not in self.ACTIVE and cur != "idle" and state in self.ACTIVE:
                return False  # never resurrect a finished goal
            self._d["state"] = state
            if error is not None:
                self._d["error"] = error
            if state not in self.ACTIVE:
                self._d["finished_at"] = time.time()
            return True

    def feedback(self, goal_id: int, distance: Optional[float], recoveries: int) -> None:
        with self._lock:
            if goal_id == self._goal_id and self._d["state"] in self.ACTIVE:
                self._d["distance_remaining"] = distance
                self._d["recoveries"] = recoveries

    def request_cancel(self) -> Optional[int]:
        """Mark the active goal as canceling. Returns its id (or None if idle)."""
        with self._lock:
            if self._d["state"] in self.ACTIVE:
                self._cancel_req = True
                self._d["state"] = "canceling"
                return self._goal_id
            return None

    def cancel_requested(self, goal_id: int) -> bool:
        with self._lock:
            return goal_id == self._goal_id and self._cancel_req

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            d = dict(self._d)
            if d["target"] is not None:
                d["target"] = dict(d["target"])
            return d

    def state(self) -> str:
        with self._lock:
            return self._d["state"]

    def is_active(self) -> bool:
        return self.state() in self.ACTIVE


# ── The hub ───────────────────────────────────────────────────────────────────

class RosHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.ready = False
        self.sim = False
        self.start_error = ""
        self.nav = NavTracker()

        self._cmds: "queue.Queue[tuple]" = queue.Queue()
        self._node: Any = None
        self._executor: Any = None
        self._thread: Optional[threading.Thread] = None

        self._pose = {"x": 0.0, "y": 0.0, "theta": 0.0}   # theta in degrees
        self._have_pose = False
        self._cov_xy: Optional[float] = None

        self._tp: dict[str, Any] = {
            "armed": False, "lin": 0.0, "ang": 0.0,
            "last_cmd_at": 0.0, "seq": -1, "publish_count": 0,
        }
        self._graph: dict[str, Any] = {
            "nodes": [], "cmd_vel_subs": 0, "cmd_vel_sub_nodes": [],
            "action_ready": False, "nav_active": None, "updated": 0.0,
        }
        self._sim_stack = False
        self._map: Optional[dict[str, Any]] = None
        self._map_version = 0
        self._battery: Optional[dict[str, Any]] = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self.ready:
                return
            if ROS_AVAILABLE:
                self._start_ros()
            elif env_bool("ROBOT_ALLOW_SIM", False):
                self.sim = True
                self.ready = True
                log.warning("ROS not available (%s) — SIMULATION MODE (ROBOT_ALLOW_SIM=1)",
                            IMPORT_ERROR)
            else:
                self.start_error = (
                    f"ROS 2 python packages are not importable ({IMPORT_ERROR}). "
                    "Start the API through run_server_ros.sh (it sources ROS + your "
                    "workspace), or set ROBOT_ALLOW_SIM=1 for development without a robot."
                )
                raise RuntimeError(self.start_error)

    def _start_ros(self) -> None:
        try:
            rclpy.init(args=None)
        except RuntimeError:
            pass  # already initialised in this process
        self._node = _make_node(self)
        self._executor = MultiThreadedExecutor(num_threads=2)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(
            target=self._executor.spin, name="ros-hub", daemon=True)
        self._thread.start()
        self.ready = True
        log.info("ROS hub started (cmd_vel=%s, nav action=%s)", CMD_VEL_TOPIC, NAV_ACTION)

    def stop(self) -> None:
        with self._lock:
            if not self.ready:
                return
            self.teleop_arm(False)
            time.sleep(0.15)  # let the node thread publish the final zero
            self.ready = False
            if self._executor is not None:
                try:
                    self._executor.shutdown()
                    self._node.destroy_node()
                    rclpy.shutdown()
                except Exception:
                    pass

    # ── simple state exposed to other modules ────────────────────────────────

    def ros_available(self) -> bool:
        return ROS_AVAILABLE

    def set_sim_stack(self, up: bool) -> None:
        self._sim_stack = up

    def get_pose(self) -> dict[str, float]:
        with self._lock:
            return dict(self._pose)

    def localization(self) -> dict[str, Any]:
        if self.sim:
            return {"have_pose": True, "cov_xy": 0.0, "localized": True}
        with self._lock:
            have, cov = self._have_pose, self._cov_xy
        ok = have and (cov is None or cov <= LOCALIZED_MAX_COV)
        return {"have_pose": have, "cov_xy": cov, "localized": bool(ok)}

    def graph(self) -> dict[str, Any]:
        if self.sim:
            nodes = list(EXPECTED_NODES) if self._sim_stack else []
            return {"nodes": nodes, "cmd_vel_subs": 1 if self._sim_stack else 0,
                    "cmd_vel_sub_nodes": ["twist_mux"] if self._sim_stack else [],
                    "action_ready": self._sim_stack, "nav_active": True if self._sim_stack else None,
                    "updated": time.time(), "age": 0.0}
        with self._lock:
            g = dict(self._graph)
        g["age"] = (time.time() - g["updated"]) if g["updated"] else None
        return g

    def nav_ready(self) -> bool:
        g = self.graph()
        return bool(g["action_ready"]) and g["nav_active"] is not False

    def battery(self) -> dict[str, Any]:
        with self._lock:
            b = dict(self._battery) if self._battery else None
        if not b:
            return {"available": False, "percent": None, "voltage": None, "alert": "unknown"}
        pct = b["percent"]
        alert = "critical" if pct is not None and pct < 10 else (
            "low" if pct is not None and pct < 20 else "normal")
        return {"available": True, "percent": pct, "voltage": b["voltage"], "alert": alert}

    # ── teleop ───────────────────────────────────────────────────────────────

    def teleop_arm(self, armed: bool) -> None:
        with self._lock:
            was = self._tp["armed"]
            self._tp.update(armed=armed, lin=0.0, ang=0.0, seq=-1, last_cmd_at=0.0)
        if was or not armed:
            self._cmds.put(("zero",))

    def teleop_armed(self) -> bool:
        with self._lock:
            return bool(self._tp["armed"])

    def teleop_cmd(self, linear: float, angular: float, seq: Optional[int] = None) -> dict[str, Any]:
        lin = max(-MAX_LINEAR, min(MAX_LINEAR, float(linear)))
        ang = max(-MAX_ANGULAR, min(MAX_ANGULAR, float(angular)))
        with self._lock:
            if not self._tp["armed"]:
                return {"armed": False, "applied": False, "reason": "not_armed"}
            if seq is not None:
                if seq <= self._tp["seq"]:
                    return {"armed": True, "applied": False, "reason": "stale"}
                self._tp["seq"] = seq
            self._tp.update(lin=lin, ang=ang, last_cmd_at=time.monotonic())
        if lin == 0.0 and ang == 0.0:
            self._cmds.put(("zero",))   # stop without waiting for the next 50 ms tick
        return {"armed": True, "applied": True, "linear": lin, "angular": ang}

    def teleop_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._tp)

    def _teleop_output(self) -> Optional[tuple[float, float]]:
        """Called from the node thread every 50 ms. None => stay silent."""
        with self._lock:
            if not self._tp["armed"]:
                return None
            fresh = (time.monotonic() - self._tp["last_cmd_at"]) <= TELEOP_STALE_S
            return (self._tp["lin"], self._tp["ang"]) if fresh else (0.0, 0.0)

    def _count_publish(self) -> None:
        with self._lock:
            self._tp["publish_count"] += 1

    # ── navigation ───────────────────────────────────────────────────────────

    def _nav_reply(self) -> dict[str, Any]:
        s = self.nav.snapshot()
        s["ok"] = s["state"] in ("sending", "running", "succeeded")
        return s

    def send_nav_goal(self, name: str, x: float, y: float, theta_deg: float,
                      wait: float = 3.0) -> dict[str, Any]:
        """
        Send a NavigateToPose goal and wait up to ``wait`` seconds for Nav2 to
        accept or reject it.  Never raises; inspect reply["ok"] / ["error"].
        """
        theta = float(theta_deg)

        def fail(error: str, state: str = "unavailable") -> dict[str, Any]:
            self.nav.fail_now(name, x, y, theta, state, error)
            return self._nav_reply()

        if not self.ready:
            return fail("ROS hub is not running")
        if not self.sim:
            g = self.graph()
            if not g["action_ready"]:
                return fail(f"Nav2 action server '{NAV_ACTION}' is not available — "
                            "is the navigation stack running?")
            if g["nav_active"] is False:
                return fail("Nav2 (bt_navigator) is not active yet — wait for the "
                            "robot stack to finish starting")
        if REQUIRE_LOCALIZED and not self.localization()["localized"]:
            return fail("Robot is not localized — set its position on the map first")

        if self.teleop_armed():
            self.teleop_arm(False)       # navigation takes over /cmd_vel

        gid = self.nav.begin(name, x, y, theta)
        if self.sim:
            self._sim_start(gid, x, y, theta)
        else:
            self._cmds.put(("goal", gid, x, y, theta))

        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and self.nav.state() == "sending":
            time.sleep(0.05)
        return self._nav_reply()

    def cancel_nav_goal(self) -> bool:
        gid = self.nav.request_cancel()
        if gid is None:
            return False
        if self.sim:
            self.nav.set_state(gid, "canceled")
        else:
            self._cmds.put(("cancel", gid))
        return True

    def is_navigating(self) -> bool:
        return self.nav.is_active()

    def get_nav_target(self) -> Optional[str]:
        s = self.nav.snapshot()
        return s["destination"] if s["state"] in NavTracker.ACTIVE else None

    def set_initial_pose(self, x: float, y: float, theta_deg: float) -> dict[str, Any]:
        for v in (x, y, theta_deg):
            if not math.isfinite(float(v)):
                return {"ok": False, "error": "x, y and theta must be finite numbers"}
        if not self.ready:
            return {"ok": False, "error": "ROS hub is not running"}
        with self._lock:
            self._pose = {"x": float(x), "y": float(y), "theta": float(theta_deg)}
            self._have_pose = True
            self._cov_xy = 0.25   # matches the covariance published with the pose
        if not self.sim:
            self._cmds.put(("initialpose", float(x), float(y), math.radians(float(theta_deg))))
        return {"ok": True, "pose": self.get_pose()}

    # ── live map (from Nav2's /map topic) ────────────────────────────────────

    def live_map_meta(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._map["meta"]) if self._map else None

    def live_map_png(self) -> Optional[bytes]:
        with self._lock:
            m = self._map
            if m is None:
                return None
            if m.get("png") is not None:
                return m["png"]
            raw, w, h = m["raw"], m["meta"]["width"], m["meta"]["height"]
            version = m["version"]
        from PIL import Image  # local import: only needed when serving the map

        table = bytearray([205] * 256)            # unknown (-1 → 255) and others
        for v in range(101):
            table[v] = 254 - int(round(v * 2.54))  # free=254 … occupied=0
        img = Image.frombytes("L", (w, h), raw.translate(bytes(table)))
        img = img.transpose(Image.FLIP_TOP_BOTTOM)   # ROS row 0 = bottom
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        png = buf.getvalue()
        with self._lock:
            if self._map is not None and self._map["version"] == version:
                self._map["png"] = png
        return png

    # ── callbacks fed by the node thread ─────────────────────────────────────

    def _on_pose(self, x: float, y: float, yaw_rad: float, cov_xy: float) -> None:
        with self._lock:
            self._pose = {"x": x, "y": y, "theta": math.degrees(yaw_rad)}
            self._have_pose = True
            self._cov_xy = cov_xy

    def _on_map(self, meta: dict[str, Any], raw: bytes) -> None:
        with self._lock:
            self._map_version += 1
            self._map = {"meta": meta, "raw": raw, "png": None, "version": self._map_version}

    def _on_battery(self, percent: Optional[float], voltage: Optional[float]) -> None:
        with self._lock:
            self._battery = {"percent": percent, "voltage": voltage}

    def _set_graph(self, **kw: Any) -> None:
        with self._lock:
            self._graph.update(kw, updated=time.time())

    # ── simulation (ROBOT_ALLOW_SIM=1) ───────────────────────────────────────

    def _sim_start(self, gid: int, x: float, y: float, theta: float) -> None:
        def run() -> None:
            with self._lock:
                sx, sy = self._pose["x"], self._pose["y"]
            dist = math.hypot(x - sx, y - sy)
            self.nav.set_state(gid, "running")
            steps = max(1, int(dist / max(SIM_SPEED, 1e-3) / 0.1))
            for i in range(1, steps + 1):
                time.sleep(0.1)
                snap = self.nav.snapshot()
                if snap["goal_id"] != gid:
                    return
                if snap["state"] == "canceling":
                    self.nav.set_state(gid, "canceled")
                    return
                if snap["state"] != "running":
                    return
                f = i / steps
                with self._lock:
                    self._pose = {"x": sx + (x - sx) * f, "y": sy + (y - sy) * f,
                                  "theta": theta}
                self.nav.feedback(gid, dist * (1 - f), 0)
            with self._lock:
                self._pose = {"x": x, "y": y, "theta": theta}
            self.nav.set_state(gid, "succeeded")

        threading.Thread(target=run, name="sim-nav", daemon=True).start()


hub = RosHub()


# ── The rclpy node (only defined when ROS is importable) ──────────────────────

def _make_node(h: RosHub):
    return _RobotNode(h)


if ROS_AVAILABLE:

    class _RobotNode(Node):  # type: ignore[misc]
        def __init__(self, h: RosHub) -> None:
            super().__init__("robot_api_hub")
            self.h = h
            self._tick = 0
            self._goal_handle: Optional[tuple[int, Any]] = None
            self._ls_future: Any = None
            self._init_msg: Any = None
            self._init_left = 0
            self._cancel_since: Optional[float] = None

            self.cmd_pub = self.create_publisher(Twist, CMD_VEL_TOPIC, 10)
            self.init_pub = self.create_publisher(
                PoseWithCovarianceStamped, INITIALPOSE_TOPIC, 10)

            latched = QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
            )
            self.create_subscription(PoseWithCovarianceStamped, AMCL_TOPIC, self._on_amcl, latched)
            self.create_subscription(OccupancyGrid, MAP_TOPIC, self._on_map, latched)
            if BATTERY_TOPIC:
                from sensor_msgs.msg import BatteryState
                self.create_subscription(BatteryState, BATTERY_TOPIC, self._on_batt, 10)

            self.nav_client = ActionClient(self, NavigateToPose, NAV_ACTION)
            self.ls_client = self.create_client(GetState, f"{NAV_LIFECYCLE_NODE}/get_state")

            self.create_timer(0.05, self._fast_tick)
            self.create_timer(1.0, self._graph_tick)

        # subscriptions ------------------------------------------------------
        def _on_amcl(self, msg: Any) -> None:
            p = msg.pose.pose
            cov = msg.pose.covariance
            self.h._on_pose(p.position.x, p.position.y,
                            _quat_to_yaw(p.orientation.z, p.orientation.w,
                                         p.orientation.x, p.orientation.y),
                            max(cov[0], cov[7]))

        def _on_map(self, msg: Any) -> None:
            try:
                raw = msg.data.tobytes()
            except AttributeError:
                raw = bytes((v & 0xFF) for v in msg.data)
            o = msg.info.origin
            meta = {
                "image_url": "/navigation/map/image",
                "resolution": float(msg.info.resolution),
                "origin": {"x": float(o.position.x), "y": float(o.position.y),
                           "yaw": _quat_to_yaw(o.orientation.z, o.orientation.w)},
                "width": int(msg.info.width),
                "height": int(msg.info.height),
                "frame_id": msg.header.frame_id or "map",
                "negate": 0,
            }
            self.h._on_map(meta, raw)

        def _on_batt(self, msg: Any) -> None:
            pct = float(msg.percentage)
            if pct != pct:        # NaN
                pct = None        # type: ignore[assignment]
            elif pct <= 1.5:
                pct *= 100.0      # REP-109 uses 0..1
            v = float(msg.voltage)
            self.h._on_battery(None if pct is None else round(pct, 1),
                               None if v != v else round(v, 2))

        # timers -------------------------------------------------------------
        def _fast_tick(self) -> None:
            self._tick += 1
            while True:
                try:
                    cmd = self.h._cmds.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._run_cmd(cmd)
                except Exception:
                    log.exception("hub command failed: %s", cmd[0])
            if self._init_left and self._tick % 6 == 0:
                self._init_msg.header.stamp = self.get_clock().now().to_msg()
                self.init_pub.publish(self._init_msg)
                self._init_left -= 1
            out = self.h._teleop_output()
            if out is not None:
                self._publish(*out)

        def _graph_tick(self) -> None:
            try:
                names = sorted({n for n in self.get_node_names() if n != "robot_api_hub"})
                try:
                    infos = self.get_subscriptions_info_by_topic(CMD_VEL_TOPIC)
                    sub_nodes = sorted({i.node_name for i in infos})
                except Exception:
                    sub_nodes = []
                nav_active = self.h._graph["nav_active"]
                if self._ls_future is not None and self._ls_future.done():
                    try:
                        nav_active = (self._ls_future.result().current_state.id == 3)
                    except Exception:
                        nav_active = None
                    self._ls_future = None
                if self._ls_future is None and self.ls_client.service_is_ready():
                    self._ls_future = self.ls_client.call_async(GetState.Request())
                self.h._set_graph(
                    nodes=names, cmd_vel_subs=len(sub_nodes), cmd_vel_sub_nodes=sub_nodes,
                    action_ready=bool(self.nav_client.server_is_ready()),
                    nav_active=nav_active,
                )
            except Exception:
                log.exception("graph sampling failed")
            # safety net: a cancel that never gets answered must not hang forever
            if self.h.nav.state() == "canceling":
                self._cancel_since = self._cancel_since or time.monotonic()
                if time.monotonic() - self._cancel_since > 8.0:
                    self.h.nav.set_state(self.h.nav.snapshot()["goal_id"], "canceled")
            else:
                self._cancel_since = None

        # commands from HTTP threads (executed on the node thread) -----------
        def _run_cmd(self, cmd: tuple) -> None:
            kind = cmd[0]
            if kind == "zero":
                self._publish(0.0, 0.0)
            elif kind == "goal":
                self._send_goal(*cmd[1:])
            elif kind == "cancel":
                gh = self._goal_handle
                if gh is not None and gh[0] == cmd[1]:
                    gh[1].cancel_goal_async()
                # if not accepted yet, _on_goal_response will cancel it
            elif kind == "initialpose":
                _, x, y, yaw = cmd
                m = PoseWithCovarianceStamped()
                m.header.frame_id = "map"
                m.pose.pose.position.x = x
                m.pose.pose.position.y = y
                m.pose.pose.orientation.z, m.pose.pose.orientation.w = _yaw_to_quat(yaw)
                m.pose.covariance[0] = 0.25
                m.pose.covariance[7] = 0.25
                m.pose.covariance[35] = 0.0685
                self._init_msg = m
                self._init_left = 3

        def _publish(self, lin: float, ang: float) -> None:
            t = Twist()
            t.linear.x = lin * LINEAR_SCALE
            t.angular.z = ang * ANGULAR_SCALE
            self.cmd_pub.publish(t)
            self.h._count_publish()

        def _send_goal(self, gid: int, x: float, y: float, theta_deg: float) -> None:
            goal = NavigateToPose.Goal()
            goal.pose = PoseStamped()
            goal.pose.header.frame_id = "map"
            goal.pose.header.stamp = self.get_clock().now().to_msg()
            goal.pose.pose.position.x = float(x)
            goal.pose.pose.position.y = float(y)
            goal.pose.pose.orientation.z, goal.pose.pose.orientation.w = _yaw_to_quat(
                math.radians(theta_deg))
            fut = self.nav_client.send_goal_async(
                goal, feedback_callback=lambda fb, g=gid: self._on_feedback(g, fb))
            fut.add_done_callback(lambda f, g=gid: self._on_goal_response(g, f))

        def _on_feedback(self, gid: int, fb: Any) -> None:
            f = fb.feedback
            self.h.nav.feedback(gid, float(f.distance_remaining),
                                int(getattr(f, "number_of_recoveries", 0)))

        def _on_goal_response(self, gid: int, fut: Any) -> None:
            try:
                handle = fut.result()
            except Exception as exc:
                self.h.nav.set_state(gid, "failed", f"Could not send goal: {exc}")
                return
            if not handle.accepted:
                self.h.nav.set_state(
                    gid, "rejected",
                    "Nav2 rejected the goal — is bt_navigator active and the robot "
                    "localized? Check the Nav2 log.")
                return
            self._goal_handle = (gid, handle)
            if self.h.nav.cancel_requested(gid):
                handle.cancel_goal_async()
            else:
                self.h.nav.set_state(gid, "running")
            handle.get_result_async().add_done_callback(
                lambda f, g=gid: self._on_result(g, f))

        def _on_result(self, gid: int, fut: Any) -> None:
            try:
                status = fut.result().status
            except Exception as exc:
                self.h.nav.set_state(gid, "failed", f"Lost contact with Nav2: {exc}")
                return
            if status == GoalStatus.STATUS_SUCCEEDED:
                self.h.nav.set_state(gid, "succeeded")
            elif status == GoalStatus.STATUS_CANCELED:
                self.h.nav.set_state(gid, "canceled")
            else:
                self.h.nav.set_state(
                    gid, "failed",
                    "Nav2 aborted the goal (no valid path, robot stuck, or timeout) — "
                    "check the Nav2 log and costmaps.")
