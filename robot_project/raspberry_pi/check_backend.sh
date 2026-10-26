#!/usr/bin/env bash
# Quick health report for the API. Usage: bash check_backend.sh [port]
PORT="${1:-${ROBOT_API_PORT:-8000}}"
echo "== /health =="
curl -fsS -m 3 "http://127.0.0.1:${PORT}/health" || echo "NOT RUNNING on :${PORT}"
echo; echo
echo "== processes =="
pgrep -af "run_server.py|uvicorn" || echo "(none)"
echo
echo "== last API log lines (/tmp/robot-api.log or journal) =="
if [ -f /tmp/robot-api.log ]; then tail -n 15 /tmp/robot-api.log
else journalctl -u robot-api -n 15 --no-pager 2>/dev/null || echo "(no log found)"; fi
echo
echo "Tip: the line  'ROS=yes' must appear at startup. 'NO (simulation)' = ROS not sourced."
