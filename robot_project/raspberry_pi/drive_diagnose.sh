#!/usr/bin/env bash
# Run this ON THE PI when the robot does not drive or navigate.
# It sources ROS + the workspace exactly like the API does, then inspects the graph.
HERE="$(dirname "$(readlink -f "$0")")"
[ -f "$HERE/real1_link.env" ] && { set -a; . "$HERE/real1_link.env"; set +a; }
. "${REAL1_ROS_SETUP:-/opt/ros/humble/setup.bash}"
[ -n "${REAL1_EXTRA_SETUP:-}" ] && [ -f "$REAL1_EXTRA_SETUP" ] && . "$REAL1_EXTRA_SETUP"
[ -n "${REAL1_WS:-}" ] && [ -f "$REAL1_WS/install/setup.bash" ] && . "$REAL1_WS/install/setup.bash"
CMD_VEL="${REAL1_CMD_VEL_TOPIC:-/cmd_vel}"
sec() { echo; echo "=== $* ==="; }

sec "environment"; echo "ROS_DISTRO=$ROS_DISTRO ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} RMW=${RMW_IMPLEMENTATION:-default}"
sec "time ros2 node list  (should be < 3 s; the API no longer calls this)"; time ros2 node list
sec "$CMD_VEL publishers/subscribers"; ros2 topic info "$CMD_VEL" -v
sec "Nav2 action server"; ros2 action list | grep -E "navigate_to_pose" || echo "navigate_to_pose NOT FOUND — Nav2 not running"
sec "bt_navigator lifecycle (want: active [3])"; ros2 lifecycle get /bt_navigator 2>&1 | head -2
sec "amcl pose (needs an initial pose)"; timeout 4 ros2 topic echo /amcl_pose --once 2>&1 | head -12 || echo "no /amcl_pose — set the initial pose"
sec "tf map -> base_link"; timeout 4 ros2 run tf2_ros tf2_echo map base_link 2>&1 | head -8
sec "map server file"; ros2 param get /map_server yaml_filename 2>&1 | head -2
sec "twist_mux inputs/output"; ros2 param list /twist_mux 2>/dev/null | head -20 || echo "(no twist_mux node)"
