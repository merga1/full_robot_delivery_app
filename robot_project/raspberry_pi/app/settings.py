"""
settings.py — tiny helpers for reading configuration from environment variables.

All tunables live in real1_link.env (see real1_link.env.example).
"""

from __future__ import annotations

import os


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [p.strip().lstrip("/") for p in raw.split(",") if p.strip()]


# Nodes that must be visible in the ROS graph for the robot stack to count as "up".
# Matches articubot_one master.launch.py; override with ROBOT_EXPECTED_NODES.
EXPECTED_NODES_DEFAULT = "controller_manager,amcl,bt_navigator,map_server"
