#!/usr/bin/env bash
# Start the robot API with ROS 2 and the robot workspace sourced.
# Used by systemd (robot-api.service), by start_backend.sh, and by the tablet app.
set -eo pipefail      # NOT -u: ROS setup scripts reference unset variables
cd "$(dirname "$(readlink -f "$0")")"

ENV_FILE="${ROBOT_ENV_FILE:-./real1_link.env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi

ROS_SETUP="${REAL1_ROS_SETUP:-/opt/ros/humble/setup.bash}"
if [ -f "$ROS_SETUP" ]; then . "$ROS_SETUP"; else
  echo "[run_server_ros] WARNING: $ROS_SETUP not found" >&2; fi
if [ -n "${REAL1_EXTRA_SETUP:-}" ] && [ -f "$REAL1_EXTRA_SETUP" ]; then . "$REAL1_EXTRA_SETUP"; fi
if [ -n "${REAL1_WS:-}" ] && [ -f "$REAL1_WS/install/setup.bash" ]; then . "$REAL1_WS/install/setup.bash"; fi

# Optional virtualenv (create with: python3 -m venv --system-site-packages .venv)
# --system-site-packages keeps ROS' python packages importable inside it.
if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi

echo "[run_server_ros] ROS_DISTRO=${ROS_DISTRO:-?} ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} RMW=${RMW_IMPLEMENTATION:-default}"
exec python3 run_server.py
