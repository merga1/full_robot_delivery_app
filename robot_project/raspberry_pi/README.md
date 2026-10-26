# Raspberry Pi backend

FastAPI service that sits between the tablet app and the robot's ROS 2 (Humble) stack.

## Files

| Path | Purpose |
|---|---|
| `run_server_ros.sh` | **Start here.** Sources ROS + your workspace + `real1_link.env`, then runs the API |
| `run_server.py` | Python entry point (loads env file, refuses to start without ROS unless `ROBOT_ALLOW_SIM=1`) |
| `start_backend.sh` | Idempotent "make sure it's running" (used by the app over SSH); exit 0 = healthy |
| `check_backend.sh` / `drive_diagnose.sh` | Health report / ROS graph diagnosis (run on the Pi) |
| `install_pi_services.sh` + `robot-api.service` + `robot-beacon.service` | systemd auto-start |
| `pi_beacon.py` | Answers the app's UDP discovery on port 9999 (+ optional mDNS) |
| `real1_link.env.example` | All configuration (copy to `real1_link.env`) |
| `app/ros_hub.py` | The one rclpy node: `/cmd_vel`, `/amcl_pose`, `/map`, `/initialpose`, Nav2 action client |
| `app/robot_link.py` | Start/stop the launch, status, node health, logs |
| `app/teleop.py` · `app/missions.py` · `app/main.py` · `app/auth.py` · `app/database.py` | Routes and persistence |
| `ros2_nodes/navigate_client.py` | Stand-alone CLI to test Nav2 without the app |
| `tests/` | 32 tests (no ROS needed) |

## Setup

```bash
cd ~/raspberry_pi
python3 -m venv --system-site-packages .venv && . .venv/bin/activate
pip install -r requirements.txt
cp real1_link.env.example real1_link.env     # edit: REAL1_WS, passwords, ROBOT_JWT_SECRET
bash run_server_ros.sh                        # foreground, to see the log
bash install_pi_services.sh --with-api      # later: start API + beacon at boot
```

The startup log must contain `ROS=yes`. `/health` reports `"ros": true, "sim": false`.

## Configuration (`real1_link.env`)

Values with spaces must be quoted. Everything is optional except `REAL1_WS`.

| Variable | Default | Meaning |
|---|---|---|
| `REAL1_ROS_SETUP` | `/opt/ros/humble/setup.bash` | ROS base setup |
| `REAL1_EXTRA_SETUP` | – | extra workspace sourced first (e.g. `l2jd_ws`) |
| `REAL1_WS` | – | robot workspace (`install/setup.bash` is sourced) |
| `REAL1_START_CMD` | `ros2 launch articubot_one master.launch.py` | what START runs |
| `REAL1_ENABLE_MOTORS_MODE` | `none` | `none` · `restart_launch` (old behaviour) · `script` |
| `ROBOT_EXPECTED_NODES` | `controller_manager,amcl,bt_navigator,map_server` | nodes that mean "stack is up" |
| `ROBOT_LAUNCH_GRACE_S` | `45` | after this, "launching" becomes "running" even if a node is missing |
| `REAL1_CMD_VEL_TOPIC` | `/cmd_vel` | where teleop publishes (see nav checklist for twist_mux) |
| `REAL1_CMD_VEL_LINEAR_SCALE` / `_ANGULAR_SCALE` | `1.0` | unit scaling |
| `ROBOT_MAX_LINEAR` / `ROBOT_MAX_ANGULAR` | `0.6` / `1.5` | server-side speed clamp |
| `ROBOT_TELEOP_STALE_S` | `0.5` | no command for this long ⇒ robot stops |
| `ROBOT_NAV_ACTION` | `navigate_to_pose` | Nav2 action name |
| `ROBOT_REQUIRE_LOCALIZED` | `1` | refuse goals until a pose estimate exists |
| `ROBOT_LOCALIZED_MAX_COV` | `0.5` | max x/y variance (m²) to count as localized |
| `ROBOT_NAV_TIMEOUT_S` | `300` | per-waypoint timeout in missions |
| `ROBOT_MISSION_DWELL_S` | `0` | pause at each mission stop |
| `ROBOT_MAP_YAML` | – | fallback map file if Nav2's `/map` isn't available |
| `ROBOT_BATTERY_TOPIC` | – | `sensor_msgs/BatteryState`; unset ⇒ app shows "N/A" |
| `ROBOT_ADMIN_USER/PASS`, `ROBOT_USER_USER/PASS` | admin/admin, user/user | **change these** |
| `ROBOT_JWT_SECRET` | random per start | set it to keep tokens valid across restarts |
| `ROBOT_TOKEN_TTL_HOURS` | `12` | login lifetime |
| `ROBOT_ALLOW_SIM` | `0` | `1` = fake robot, no ROS (development) |
| `ROBOT_DATA_DIR` | `app/` | where `robot.db` / `destinations.json` live |
| `ROBOT_API_PORT` | `8000` | |

## API

All routes except `/health` and `/auth/login` need `Authorization: Bearer <token>`.

| Route | Notes |
|---|---|
| `POST /auth/login` | `{username,password}` → `{access_token, role}` |
| `GET /health` | `{ok, service:"robot-control", ros, sim, host, user}` |
| `POST /robot/start` · `/robot/stop` | return immediately; poll status. Stop shuts down the whole process group |
| `GET /robot/status` | cached, never blocks. Includes `nav`, `nav_ready`, `localized`, `battery_available`, `sim` |
| `GET /robot/bridge-status` · `/robot/link-check` | stack/graph health, ROS importability |
| `POST /robot/ready` · `/robot/enable-motors` | |
| `POST /teleop/enable {enabled}` | arming cancels any navigation/mission |
| `POST /teleop/cmd_vel {linear,angular,seq?}` | reply has `armed`, `applied`; stale `seq` dropped |
| `POST /teleop/stop` · `/teleop/test-drive` · `GET /teleop/debug` · `/teleop/connection` | |
| `GET/POST /destinations` · `DELETE /destinations/{name}` | named map poses (degrees) |
| `GET /navigation/map` · `/navigation/map/image` | live Nav2 map (PNG) or file fallback; `source`, `warning` |
| `POST /navigation/goal {destination}` | waits ≤3 s for Nav2; **503** Nav2/localization unavailable, **409** rejected / mission running |
| `POST /navigation/cancel` · `GET /navigation/status` | |
| `POST /navigation/initial-pose {x,y,theta°}` | publishes `/initialpose` (like RViz "2D Pose Estimate") |
| `POST /missions {destinations}` · `GET /missions` · `GET /missions/{id}` · `POST /missions/{id}/cancel` | `steps[]`, `progress`, `error` |
| `GET /logs?severity=&limit=` | launch output, severity parsed from `[ERROR]/[WARN]/…` |

Goal state machine (`nav.state`): `sending → running → succeeded | failed | canceled`, plus
`rejected` and `unavailable` (never started). Mission statuses: `pending, in_progress, completed, failed, cancelled`.

## Design rules (why it no longer lags)

1. **No `ros2` CLI calls anywhere.** Graph info is sampled once a second inside the rclpy node
   and cached; status endpoints read the cache.
2. **No blocking in the event loop.** Anything slow is a plain `def` route (thread pool) or a
   background thread. Login (bcrypt) included.
3. **rclpy is touched only from its own thread**; HTTP handlers enqueue commands.
4. **Velocity is published by the hub at 20 Hz only while armed**, with a 0.5 s watchdog; when
   disarmed the hub is silent so it never fights Nav2.
5. **Failures are reported.** Goals resolve to an explicit state; missions only complete when Nav2 says so.

## On-robot checklist (first run)

The rclpy code is verified against a fake `rclpy` (`tests/test_ros_wiring.py`) and the HTTP
layer in simulation — **not** against a real robot. Check these once:

1. `bash run_server_ros.sh` → log shows `ROS=yes`; `curl localhost:8000/health` → `"ros": true`.
2. App → START → within ~1 min `GET /robot/status` shows `ros_running: true`, `nav_ready: true`
   and `node_health` all `running`. If `node_health` shows `stopped` for nodes that *do* exist
   under another name, fix `ROBOT_EXPECTED_NODES`.
3. 📍 Set robot position → `localized: true`; `ros2 topic echo /amcl_pose --once` agrees.
4. Short GO (≈1 m). Watch `nav.state`: `running` → `succeeded`. Compare with `ros2 action list`.
5. Joystick: arm, nudge, release. `ros2 topic hz /cmd_vel` ≈ 20 Hz while armed, silent after disarm.
6. Kill Wi-Fi on the tablet while driving → robot must stop within ~0.5 s.
7. `time ros2 node list` — if it takes seconds the Pi is overloaded; Nav2 will suffer too.

## Development

```bash
pip install pytest httpx
python -m pytest -q                       # simulation + fake-rclpy tests
ROBOT_ALLOW_SIM=1 python run_server.py    # fake robot on :8000
python ros2_nodes/navigate_client.py 1.0 0.5 90   # test Nav2 directly (needs ROS)
```
