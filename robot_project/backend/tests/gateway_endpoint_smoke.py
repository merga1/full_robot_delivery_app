"""Manual gateway endpoint smoke helper; run with ``python gateway_endpoint_smoke.py``."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def main() -> None:
    client = TestClient(app)
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["access_token"]
    robot_token = client.post(
        "/api/v1/robots/robot_01/gateway-token",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["token"]
    try:
        with client.websocket_connect(
            "/api/v1/robot-gateway",
            headers={"Authorization": f"Bearer {robot_token}"},
        ) as websocket:
            assert websocket.receive_json()["frame"] == "CONNECTED"
            websocket.send_text(json.dumps({
                "frame": "HELLO", "robot_id": "robot_01", "destinations": ["home"]
            }))
            assert websocket.receive_json()["frame"] == "HELLO_ACK"
            websocket.send_text(json.dumps({
                "frame": "HEARTBEAT", "robot_id": "robot_01", "battery_percent": 88
            }))
    except WebSocketDisconnect as exc:
        raise AssertionError(f"gateway endpoint disconnected with code {exc.code}") from exc
    print("gateway endpoint smoke ok")


if __name__ == "__main__":
    main()
