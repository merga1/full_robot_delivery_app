# Smart Restaurant Ordering and Delivery Robot

This repository now contains the complete software path for a smart restaurant:
customer ordering, kitchen workflow, administration, robot mission orchestration,
robot touchscreen, Flutter operations app, and a separate ROS 2 robot gateway.

The original Raspberry Pi controller is still included for low-level robot control.
The new `backend/` is the business source of truth and does not pretend that a
database transition means the robot physically moved.

```
┌──────────────┐  HTTP + JWT   ┌───────────────────────────────────────────┐
│ Flutter app  │ ────────────► │ Raspberry Pi 4                            │
│ (tablet)     │   port 8000   │                                           │
│ joystick,    │ ◄──────────── │  FastAPI  (raspberry_pi/app)              │
│ map, goals,  │   status      │    ├─ robot_link.py  start/stop the stack │
│ missions     │               │    ├─ ros_hub.py     ONE rclpy node:      │
└──────────────┘               │    │     /cmd_vel · /amcl_pose · /map     │
                               │    │     /initialpose · NavigateToPose    │
                               │    └─ missions.py    multi-stop missions  │
                               │                    │ DDS                  │
                               │  articubot_one1 workspace (Nav2, AMCL,    │
                               │  ros2_control, rplidar, twist_mux, …)     │
                               └───────────────────────────────────────────┘
```

| Folder | What it is |
|---|---|
| `raspberry_pi/` | The API, start scripts, systemd units, discovery beacon, tests → [README](raspberry_pi/README.md) |
| `flutter_app/` | The tablet app → [README](flutter_app/README.md) |
| `backend/` | FastAPI/PostgreSQL restaurant and mission backend → [README](backend/README.md) |
| `web/` | Customer, kitchen, admin, and robot touchscreen UIs served by the backend |
| `robot_gateway/` | Authenticated WebSocket bridge between the backend and ROS 2/Nav2 robot |
| `docker-compose.yml` | Local PostgreSQL + backend deployment → [DEPLOYMENT](DEPLOYMENT.md) |
| `PROJECT_STATUS.md` | Implemented scope and hardware-dependent work |
| `docs/NAV_SIDE_CHECKLIST.md` | What to verify in the ROS workspace (`articubot_one1`) |
| `CHANGES.md` | Every fix, why it was needed, and how it was verified |

The navigation stack itself (Nav2 parameters, launch files, maps) lives in the separate
`articubot_one1` ROS 2 workspace and is **not** part of this repository.

## Quick start

**Restaurant backend (local development)**

```powershell
cd robot_project/backend
Copy-Item .env.example .env
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

Or use `docker compose up --build` from `robot_project`; see [DEPLOYMENT.md](DEPLOYMENT.md).

The default development seed accounts are `admin/admin123`, `kitchen/kitchen123`,
and `courier/courier123`. Change them before any shared deployment.

**Robot gateway simulation**

The gateway is simulation-disabled by default. It can be run with `GATEWAY_ALLOW_SIM=1`
for API/UI development, but simulation is not a hardware test. See
[robot_gateway/README.md](robot_gateway/README.md).

**Legacy Pi controller**

**On the Pi (once)**

```bash
cd ~/raspberry_pi
python3 -m venv --system-site-packages .venv && . .venv/bin/activate   # keeps access to ROS python packages
pip install -r requirements.txt
cp real1_link.env.example real1_link.env && nano real1_link.env       # set REAL1_WS, passwords, secret
bash install_pi_services.sh --with-api                                  # optional: auto-start API + beacon on boot
bash start_backend.sh                                                  # or: bash run_server_ros.sh
bash check_backend.sh                                                  # expect "ros": true in /health
```

**On the tablet** — build/run the app (see [flutter_app/README.md](flutter_app/README.md)), log in,
let it discover the Pi (or enter `http://<pi-ip>:8000`).

**No robot at hand?** `ROBOT_ALLOW_SIM=1 python3 run_server.py` starts a fake robot that
"drives" to goals, so you can develop and demo the app without ROS.

## Operating the robot (correct order)

1. **START** — launches the robot stack. Wait until the status card says *running*
   (the app polls; Nav2 needs about 30–60 s on a Pi 4). Nothing else is needed;
   *Enable Motors* is a no-op unless you configured it (`REAL1_ENABLE_MOTORS_MODE`).
2. **Set robot position** (map screen → 📍 button): tap where the robot is and drag to its
   heading. Required after every START, and again if the robot is carried or loses track.
   Until then GO is disabled and says why.
3. **Navigate**: pick a destination → **GO**. The app shows distance remaining and, when it
   ends, *Arrived*, *Cancelled* or the failure reason.
   **or Joystick**: tap *Joystick* (arms the robot), drive; releasing sends stop.
4. **STOP ROBOT** shuts the whole stack down cleanly.

Destinations are named poses saved on the Pi. Add them with the map screen's *Place tables*
button (tap the map where the table is).

## When something doesn't work

| Symptom | Most likely cause | What to do |
|---|---|---|
| App says *Connection lost* | Pi off, wrong Wi-Fi, API crashed | `bash check_backend.sh` on the Pi; `journalctl -u robot-api -n 50` |
| API exits at start: "ROS 2 is not available" | Started without ROS sourced | Use `run_server_ros.sh` (or the systemd service), never plain `python3` |
| GO greyed out: *Set the robot position…* | AMCL has no pose | 📍 Set robot position |
| GO greyed out: *Navigation (Nav2) is still starting* | `navigate_to_pose` not up yet | Wait; if it never comes: `bash drive_diagnose.sh` |
| Goal fails: *Nav2 rejected the goal* | bt_navigator not active / bad TF | `drive_diagnose.sh`, check Nav2 log in the app's Logs screen |
| Goal fails: *Nav2 aborted the goal* | No path / stuck / blocked costmap | Look at the Logs screen (controller/planner errors) |
| Joystick does nothing | Not armed, or nothing listens on `/cmd_vel` | App now says so; Debug button shows the subscriber list |
| Robot moves with joystick but not with GO (or vice-versa) | `/cmd_vel` routing / twist_mux | [docs/NAV_SIDE_CHECKLIST.md](docs/NAV_SIDE_CHECKLIST.md) |
| Map looks right but goals land in the wrong place | App map ≠ map Nav2 uses | Fixed by design (app shows Nav2's live `/map`); a warning appears if the saved file differs |
| Release APK can't connect | Missing `INTERNET` permission | Fixed in `AndroidManifest.xml` |

## Tests

```bash
cd raspberry_pi && pip install pytest httpx && python -m pytest -q     # 32 tests, no ROS needed
cd ../flutter_app && flutter analyze && flutter test                    # run these on your PC
```
