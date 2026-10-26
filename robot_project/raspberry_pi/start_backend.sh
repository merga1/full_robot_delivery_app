#!/usr/bin/env bash
# Idempotent "make sure the API is running". Exit 0 = healthy, 1 = failed.
# The tablet app calls this over SSH when the API is not reachable.
PORT="${ROBOT_API_PORT:-${PORT:-8000}}"
HERE="$(dirname "$(readlink -f "$0")")"
LOG=/tmp/robot-api.log

healthy() { curl -fsS -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; }

if healthy; then echo "API already running on :${PORT}"; exit 0; fi

if systemctl is-enabled robot-api.service >/dev/null 2>&1; then
  sudo -n systemctl start robot-api.service 2>/dev/null || true
else
  nohup bash "$HERE/run_server_ros.sh" >"$LOG" 2>&1 </dev/null &
fi

for _ in $(seq 1 45); do
  sleep 1
  if healthy; then echo "API started on :${PORT}"; exit 0; fi
done
echo "API did not become healthy within 45 s. Last log lines:" >&2
tail -n 25 "$LOG" >&2 2>/dev/null || true
exit 1
