"""Executed by test_ros_wiring.py with PYTHONPATH=tests/fake_ros (no ROS needed)."""
import math, os, sys, threading, time
from array import array
from types import SimpleNamespace as NS

os.environ.update(ROBOT_REQUIRE_LOCALIZED="1", REAL1_CMD_VEL_LINEAR_SCALE="2.0")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app.ros_hub as rh

assert rh.ROS_AVAILABLE, rh.IMPORT_ERROR
hub = rh.RosHub(); hub.start()
node = hub._node
assert hub.ready and not hub.sim
assert node.pubs["/cmd_vel"] is not None and "/amcl_pose" in node.subs and "/map" in node.subs

stop = threading.Event()
def pump():                       # plays the role of the executor's 20 Hz timer
    while not stop.is_set():
        node._fast_tick(); time.sleep(0.02)
threading.Thread(target=pump, daemon=True).start()

client = node.nav_client
cmd_pub = node.pubs["/cmd_vel"]

def amcl(x, y, z, w, cov):
    node._on_amcl(NS(pose=NS(pose=NS(position=NS(x=x, y=y, z=0.0),
        orientation=NS(x=0.0, y=0.0, z=z, w=w)), covariance=[cov]+[0]*6+[cov]+[0]*28)))

# 1. unavailable: Nav2 not up → clear failure, nothing sent
r = hub.send_nav_goal("Table", 1, 1, 0)
assert not r["ok"] and r["state"] == "unavailable" and "not available" in r["error"], r
assert client.goals == []

# 2. Nav2 up but robot not localized → refused with an actionable message
client.ready = True; node.clients["/bt_navigator/get_state"].ready = True
node._graph_tick(); node._graph_tick()
g = hub.graph(); assert g["action_ready"] and g["nav_active"] is True, g
r = hub.send_nav_goal("Table", 1, 1, 0)
assert r["state"] == "unavailable" and "localized" in r["error"], r

# 3. localized (pose from AMCL, 90° yaw) → goal accepted → running
amcl(1.0, 2.0, math.sin(math.pi/4), math.cos(math.pi/4), 0.1)
loc = hub.localization(); assert loc["localized"]
assert abs(hub.get_pose()["theta"] - 90) < 1e-6
r = hub.send_nav_goal("Table", 3.0, 4.0, 180.0)
assert r["ok"] and r["state"] == "running", r
goal = client.goals[-1]
assert goal.pose.header.frame_id == "map" and goal.pose.pose.position.x == 3.0
assert abs(goal.pose.pose.orientation.z - 1.0) < 1e-9 and abs(goal.pose.pose.orientation.w) < 1e-9  # 180°

# 4. feedback → distance; result SUCCEEDED → succeeded
client.fb_cbs[-1](NS(feedback=NS(distance_remaining=2.5, number_of_recoveries=1)))
s = hub.nav.snapshot(); assert s["distance_remaining"] == 2.5 and s["recoveries"] == 1
client.handles[-1].result_future.set_result(NS(status=4))
assert hub.nav.state() == "succeeded"

# 5. Nav2 rejects → "rejected" with hint
client.accept = False
r = hub.send_nav_goal("Table", 3, 4, 0)
assert not r["ok"] and r["state"] == "rejected" and "rejected" in r["error"], r
client.accept = True

# 6. cancel: handle.cancel_goal_async is called, CANCELED result → canceled
r = hub.send_nav_goal("Door", -1, 0, 0); assert r["state"] == "running"
h = client.handles[-1]
assert hub.cancel_nav_goal() is True and hub.nav.state() == "canceling"
time.sleep(0.2); assert h.cancelled
h.result_future.set_result(NS(status=5))
assert hub.nav.state() == "canceled"

# 7. aborted → failed with explanation
r = hub.send_nav_goal("Door", -1, 0, 0)
client.handles[-1].result_future.set_result(NS(status=6))
s = hub.nav.snapshot(); assert s["state"] == "failed" and "aborted" in s["error"]

# 8. preempting a goal: late result of the OLD goal must not clobber the new one
hub.send_nav_goal("A", 1, 1, 0); old = client.handles[-1]
hub.send_nav_goal("B", 2, 2, 0)
old.result_future.set_result(NS(status=5))
assert hub.nav.state() == "running" and hub.nav.snapshot()["destination"] == "B"
hub.cancel_nav_goal(); time.sleep(0.2); client.handles[-1].result_future.set_result(NS(status=5))

# 9. teleop: silent while disarmed; 20 Hz + scale while armed; zero on stop/disarm
time.sleep(0.2); n0 = len(cmd_pub.published); time.sleep(0.3)
assert len(cmd_pub.published) == n0, "hub must not publish /cmd_vel while disarmed"
hub.teleop_arm(True)
hub.teleop_cmd(0.25, 0.5, seq=1); time.sleep(0.3)
last = cmd_pub.published[-1]
assert abs(last.linear.x - 0.5) < 1e-9 and abs(last.angular.z - 0.5) < 1e-9, (last.linear.x, last.angular.z)  # scale 2.0 on linear
assert len(cmd_pub.published) - n0 >= 6, "expected ~20 Hz publishing"
time.sleep(rh.TELEOP_STALE_S + 0.2)
assert cmd_pub.published[-1].linear.x == 0.0, "stale command must fall back to zero"
hub.teleop_cmd(0.4, 0.0, seq=2); time.sleep(0.1); hub.teleop_arm(False); time.sleep(0.2)
assert cmd_pub.published[-1].linear.x == 0.0
n1 = len(cmd_pub.published); time.sleep(0.3); assert len(cmd_pub.published) == n1

# 10. initial pose → 3 messages on /initialpose, map frame, correct yaw
ip = node.pubs["/initialpose"]
res = hub.set_initial_pose(1.0, 2.0, 90.0); assert res["ok"]
time.sleep(0.6)
assert len(ip.published) == 3, len(ip.published)
m = ip.published[0]
assert m.header.frame_id == "map" and m.pose.pose.position.x == 1.0
assert abs(m.pose.pose.orientation.z - math.sin(math.pi/4)) < 1e-9 and m.pose.covariance[0] == 0.25

# 11. live map: /map data → meta + PNG
w, h_ = 6, 4
data = array("b", [0]*(w*h_)); data[0] = 100; data[1] = -1
node._on_map(NS(data=data, header=NS(frame_id="map"),
    info=NS(resolution=0.05, width=w, height=h_,
            origin=NS(position=NS(x=-1.0, y=-2.0, z=0.0), orientation=NS(x=0, y=0, z=0.0, w=1.0)))))
meta = hub.live_map_meta(); assert (meta["width"], meta["height"]) == (6, 4) and meta["origin"]["x"] == -1.0
png = hub.live_map_png(); assert png[:4] == b"\x89PNG"
from PIL import Image; import io
img = Image.open(io.BytesIO(png)); assert img.getpixel((0, 3)) == 0 and img.getpixel((1, 3)) == 205

# 12. graph sampling: expected nodes + cmd_vel subscribers come from the graph, no CLI
node.node_names = ["robot_api_hub", "amcl", "bt_navigator", "controller_manager", "map_server"]
node.sub_infos["/cmd_vel"] = ["twist_mux"]
node._graph_tick(); g = hub.graph()
assert g["cmd_vel_subs"] == 1 and g["cmd_vel_sub_nodes"] == ["twist_mux"] and "robot_api_hub" not in g["nodes"]

stop.set(); hub.stop()
print("WIRING-OK")
