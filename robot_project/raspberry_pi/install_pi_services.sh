#!/usr/bin/env bash
# Install systemd units so the Pi beacon and robot API start automatically
# whenever the Raspberry Pi receives power.
set -euo pipefail

if [ "${EUID}" -eq 0 ]; then
  echo "Run this script as the normal Raspberry Pi user, without sudo." >&2
  echo "The script calls sudo only where systemd installation needs it." >&2
  exit 1
fi

USER_NAME="$(whoami)"
HOME_DIR="${HOME}"
# Use the directory containing this script. This also works when the folder is
# deployed somewhere other than ~/raspberry_pi.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${ROBOT_APP_DIR:-${SCRIPT_DIR}}"
SKIP_API=false

for arg in "$@"; do
  case "$arg" in
    --no-api) SKIP_API=true ;;
    --with-api) SKIP_API=false ;; # retained for compatibility
    -h|--help)
      echo "Usage: $0 [--no-api]"
      echo "  Enables robot-beacon.service and robot-api.service at boot."
      echo "  robot-api.service runs run_server_ros.sh automatically."
      echo "  --no-api   Install only the beacon service."
      exit 0
      ;;
    *)
      echo "Unknown option: ${arg}" >&2
      exit 2
      ;;
  esac
done

if [ ! -f "${APP_DIR}/pi_beacon.py" ] || [ ! -f "${APP_DIR}/run_server_ros.sh" ]; then
  echo "Missing Pi service files in ${APP_DIR}." >&2
  echo "Expected pi_beacon.py and run_server_ros.sh there." >&2
  exit 1
fi

echo "Installing Pi services for user ${USER_NAME}..."

chmod +x "${APP_DIR}/pi_beacon.py" "${APP_DIR}/run_server_ros.sh" \
  "${APP_DIR}/start_backend.sh" 2>/dev/null || true

# mDNS is optional; UDP discovery continues to work if installation is not
# available. The API itself uses the project virtual environment below.
pip3 install --user zeroconf 2>/dev/null || pip3 install zeroconf 2>/dev/null || true

PYTHON_BIN="/usr/bin/python3"
if [ -x "${APP_DIR}/.venv/bin/python3" ]; then
  PYTHON_BIN="${APP_DIR}/.venv/bin/python3"
fi

install_unit() {
  local unit_name="$1"
  local template="${APP_DIR}/${unit_name}"
  if [ ! -f "${template}" ]; then
    echo "Missing ${template}" >&2
    exit 1
  fi

  sed \
    -e "s|@USER@|${USER_NAME}|g" \
    -e "s|@HOME@|${HOME_DIR}|g" \
    -e "s|@APP_DIR@|${APP_DIR}|g" \
    -e "s|@PYTHON@|${PYTHON_BIN}|g" \
    "${template}" | sudo tee "/etc/systemd/system/${unit_name}" >/dev/null

  echo "  installed /etc/systemd/system/${unit_name}"
}

install_unit "robot-beacon.service"
install_unit "robot-api.service"

sudo systemctl daemon-reload
sudo systemctl enable --now robot-beacon.service

if ! ${SKIP_API}; then
  sudo systemctl enable --now robot-api.service
fi

echo ""
echo "Beacon:  $(systemctl is-active robot-beacon.service || true)"
if ! ${SKIP_API}; then
  echo "API:     $(systemctl is-active robot-api.service || true)"
  echo "Enabled: $(systemctl is-enabled robot-api.service || true)"
  echo "The API will run run_server_ros.sh automatically after every boot."
else
  echo "API: not enabled (--no-api was supplied)"
fi
echo ""
echo "Check logs with: journalctl -u robot-api.service -f"
