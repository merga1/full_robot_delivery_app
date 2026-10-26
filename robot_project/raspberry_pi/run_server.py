#!/usr/bin/env python3
"""
run_server.py — entry point for the robot Pi backend.

Normally started through run_server_ros.sh (which sources ROS first).  Manual:

    source /opt/ros/humble/setup.bash
    source ~/articubot_one1/install/setup.bash
    python3 run_server.py

Development without a robot (fake robot, no ROS needed):

    ROBOT_ALLOW_SIM=1 python3 run_server.py
"""

import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE loader (supports quotes, `export`, comments)."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key.strip(), val)


_env = Path(os.getenv("ROBOT_ENV_FILE", _HERE / "real1_link.env"))
if _env.exists():
    _load_env_file(_env)
    print(f"[run_server] loaded {_env}")

# Fail fast with an actionable message instead of silently "simulating".
try:
    import rclpy  # noqa: F401
    from nav2_msgs.action import NavigateToPose  # noqa: F401
    _ros_ok = True
except Exception as exc:  # pragma: no cover
    _ros_ok = False
    _ros_err = f"{type(exc).__name__}: {exc}"

if not _ros_ok and os.getenv("ROBOT_ALLOW_SIM", "0").lower() not in ("1", "true", "yes"):
    sys.stderr.write(
        "\n[run_server] ERROR: ROS 2 is not available in this shell "
        f"({_ros_err}).\n"
        "  Start the API with:  bash run_server_ros.sh\n"
        "  (or source /opt/ros/humble/setup.bash and your workspace first).\n"
        "  For development without a robot set ROBOT_ALLOW_SIM=1.\n\n")
    sys.exit(2)

import uvicorn  # noqa: E402

if __name__ == "__main__":
    host = os.getenv("ROBOT_API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("ROBOT_API_PORT", "8000")))
    print(f"[run_server] http://{host}:{port}  ROS={'yes' if _ros_ok else 'NO (simulation)'}"
          f"  REAL1_WS={os.getenv('REAL1_WS', '(not set)')}")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")
