from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _gateway_url() -> str:
    raw = os.getenv("GATEWAY_BACKEND_URL", "ws://127.0.0.1:8000/api/v1/robot-gateway").strip()
    if raw.endswith("/api/v1/robot-gateway"):
        return raw
    if raw.startswith("http://"):
        raw = "ws://" + raw[len("http://") :]
    elif raw.startswith("https://"):
        raw = "wss://" + raw[len("https://") :]
    return raw.rstrip("/") + "/api/v1/robot-gateway"


@dataclass(frozen=True)
class GatewayConfig:
    backend_url: str = _gateway_url()
    robot_id: str = os.getenv("GATEWAY_ROBOT_ID", "robot_01")
    robot_token: str = os.getenv("GATEWAY_ROBOT_TOKEN", "")
    version: str = os.getenv("GATEWAY_VERSION", "1.0.0")
    ros_distro: str = os.getenv("GATEWAY_ROS_DISTRO", "unknown")
    navigation_action: str = os.getenv("GATEWAY_NAV_ACTION", "/navigate_to_pose")
    heartbeat_seconds: float = float(os.getenv("GATEWAY_HEARTBEAT_SECONDS", "5"))
    reconnect_seconds: float = float(os.getenv("GATEWAY_RECONNECT_SECONDS", "3"))
    command_ttl_seconds: float = float(os.getenv("GATEWAY_COMMAND_TTL_SECONDS", "30"))
    destinations_file: Path = Path(os.getenv("GATEWAY_DESTINATIONS_FILE", "config/destinations.json"))
    allow_simulation: bool = _bool("GATEWAY_ALLOW_SIM", False)
    home_destination: str = os.getenv("GATEWAY_HOME_DESTINATION", "home")
    tts_binary: str = os.getenv("GATEWAY_TTS_BINARY", "")
    connect_timeout_seconds: float = float(os.getenv("GATEWAY_CONNECT_TIMEOUT_SECONDS", "15"))


def load_config() -> GatewayConfig:
    config = GatewayConfig()
    if not config.robot_token:
        raise RuntimeError("GATEWAY_ROBOT_TOKEN is required")
    return config
