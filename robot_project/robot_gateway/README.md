# Robot gateway

This process runs on the robot computer and is the only component that talks to ROS 2/Nav2. It opens an outbound authenticated WebSocket to the FastAPI backend, validates commands, reports mission events, and sends heartbeats.

## Development simulation

```powershell
$env:GATEWAY_ROBOT_TOKEN = "<token created by POST /api/v1/robots/robot_01/gateway-token>"
$env:GATEWAY_ALLOW_SIM = "1"
$env:GATEWAY_DESTINATIONS_FILE = "config/destinations.example.json"
python -m robot_gateway
```

Simulation is deliberately opt-in. It is useful for API and UI integration tests but it does not validate motors, sensors, localization, or collision recovery.

## Physical robot

Install ROS 2, Nav2, `rclpy`, `nav2_msgs`, and `geometry_msgs` in the robot's ROS environment. Copy `config/destinations.example.json` to `config/destinations.json` and replace the example poses with the real map coordinates. Set `GATEWAY_ALLOW_SIM=0`, `GATEWAY_ROS_DISTRO`, and the gateway token, then run from the ROS environment.

For an always-on Pi installation, use `.env.example` as `/etc/restaurant-robot-gateway.env` and adapt `robot-gateway.service` to source the ROS setup and workspace before `python3 -m robot_gateway`.
