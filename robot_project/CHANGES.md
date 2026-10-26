# What was fixed, and how it was checked

Legend — **Ran**: executed against the backend in this review (32 tests, simulation + a fake
rclpy). **Fake-ROS**: executed against a stand-in for rclpy (API shapes mirrored, not a real
ROS). **Review**: written carefully, not executed here (Dart; no SDK available). **On robot**:
needs your confirmation (see `raspberry_pi/README.md`, "On-robot checklist").

## A. "I give a position and the robot doesn't move"

| # | Problem found | Fix | Where | Checked |
|---|---|---|---|---|
| A1 | API silently ran in simulation mode when ROS wasn't sourced and still answered `ok:true` | Refuses to start without ROS (unless `ROBOT_ALLOW_SIM=1`); `run_server_ros.sh` sources ROS; `/health` reports `ros`/`sim` | `ros_hub.py`, `run_server.py`, `run_server_ros.sh` | Ran |
| A2 | Goal sent via a subprocess whose result was never read; "ok" returned before Nav2 answered | In-process `NavigateToPose` action client; explicit states; HTTP 503/409 with a reason | `ros_hub.py`, `main.py` | Ran + Fake-ROS |
| A3 | Subprocess stdout pipe never drained ⇒ client could freeze mid-trip | Subprocess removed (client kept only as a CLI test tool, output throttled) | `navigate_client.py` | Ran |
| A4 | Wrong `PYTHONPATH` for the subprocess | Gone with A2 | – | – |
| A5 | Failures looked like success (button reset silently) | App shows Arrived / Cancelled / failure reason | `navigation_screen.dart` | Review |
| A6 | App never set AMCL's initial pose; showed robot at (0,0) | `POST /navigation/initial-pose`, 📍 button, `localized` flag, GO disabled with explanation | `ros_hub.py`, `main.py`, `navigation_screen.dart` | Ran + Fake-ROS |
| A7 | GO enabled as soon as the launch process existed, before Nav2 was active | `nav_ready` (action server + bt_navigator lifecycle) gates GO | `ros_hub.py`, `robot_state.dart` | Fake-ROS |
| A8 | App map file could differ from the map Nav2 localizes in | App draws Nav2's live `/map` (PNG); warns if the saved file differs | `ros_hub.py`, `main.py` | Ran |
| A9 | *Enable Motors* relaunched the entire stack | Default `none`; `restart_launch` still available, but in the background | `robot_link.py` | Ran |
| A10 | CPU starved by constant `ros2` CLI processes (hurts AMCL/controller) | No CLI calls | `robot_link.py`, `ros_hub.py` | Ran |
| A11 | Missions reported "completed" when nothing moved | Step completes only on Nav2 SUCCEEDED; failures carry the reason | `missions.py` | Ran |

## B. Buttons with errors and delays

| # | Problem found | Fix | Where | Checked |
|---|---|---|---|---|
| B1 | `/robot/status` = 3 `ros2` CLI calls; `bridge-status`, `/teleop/enable` = 2 each, all blocking the event loop (measured: status 4.5 s, arm 3.0 s with a 1.5 s stand-in) | Cached graph; endpoints answer in ms (test asserts <1 s with a CLI that sleeps 3 s) | `robot_link.py`, `ros_hub.py` | Ran |
| B2 | App polled without waiting + new TCP connection per request + 75 s timeout ⇒ backlog (measured 2 s → 9 s and growing) | Persistent client, non-overlapping poll, latest-wins joystick, 1 s drive timeout | `robot_service.dart` | Review (backend side Ran) |
| B3 | Stop could queue behind stale "go" commands | Latest-wins + `seq` dropping + 0.5 s watchdog + 20 Hz hub publishing; stop ×3 | `robot_service.dart`, `teleop.py`, `ros_hub.py` | Ran + Fake-ROS |
| B4 | Joystick commands silently ignored when not armed | Reply has `armed`; app tells the user | `teleop.py`, `home_screen.dart` | Ran / Review |
| B5 | Quick buttons only hit a 28 px icon; Stop spammed requests | 64×64 opaque touch target; Stop is one-shot | `joystick_widget.dart` | Review |
| B6 | Debug values hard-coded `True`; `cmd_vel` subscriber check returned `True` on error | Real values from the graph | `teleop.py`, `robot_link.py` | Ran |
| B7 | Status looked for a `robot_bridge` node that doesn't exist in this stack | Configurable `ROBOT_EXPECTED_NODES`; UI says "Robot stack" | `settings.py`, `home_screen.dart` | Ran / Review |
| B8 | `_start()` had an unguarded call in its `catch`; `setState` after dispose; STOP not awaited | Guarded + `mounted` checks | `home_screen.dart`, `navigation_screen.dart`, `missions_screen.dart` | Review |
| B9 | Mission screen crashed (`steps` missing from API) and showed nothing useful | API returns `steps`, `progress`, `error`; screen is defensive; Cancel added | `missions.py`, `missions_screen.dart` | Ran / Review |
| B10 | Stop only killed the parent `ros2 launch` (orphaned nodes ⇒ duplicate nodes next start) | Process-group SIGINT → SIGTERM → SIGKILL, non-blocking | `robot_link.py` | Fake-ROS (logic) / On robot |
| B11 | Status poll errors swallowed; UI frozen on stale data | `disconnected` after 3 failed polls | `robot_service.dart` | Review |

## C. Other defects

| # | Problem | Fix | Checked |
|---|---|---|---|
| C1 | Fresh `pip install` crashed: passlib 1.7.4 + bcrypt 5 | Use `bcrypt` directly; login runs off the event loop | Ran (bcrypt 5.0.0) |
| C2 | Release APK had no `INTERNET` permission | Added to main manifest | Review |
| C3 | `start_backend.sh`, `run_server_ros.sh`, `check_backend.sh`, `drive_diagnose.sh`, `pi_beacon.py`, both `.service` files were **empty** (so "start backend from the app" did nothing) | Written | Syntax-checked; beacon protocol matches app |
| C4 | `real1_link.env.example` had unquoted values; `source`-ing it broke `REAL1_START_CMD` | Quoted; Python loader strips quotes | Ran |
| C5 | Battery always 100 % / 12.6 V (fake) | Real `BatteryState` if configured, else "N/A" | Ran / Review |
| C6 | Log severity always INFO | Parsed from the ROS log prefix | Ran |
| C7 | Default `admin/admin`, hard-coded JWT secret, CORS `*` + credentials | Configurable; random secret per start; warnings logged; credentials off | Ran |
| C8 | `/teleop/test-drive` bypassed arming | Requires armed + subscriber | Ran |
| C9 | Destinations JSON written non-atomically, no validation | Atomic write, validation | Ran |
| C10 | Missions left "in progress" after a crash | Marked failed at startup; DB migrates old files | Ran |
| C11 | 11 empty/dead files, `cmd_vel_relay.py` (second `/cmd_vel` publisher) | Removed `cmd_vel_relay.py`, `ros_context.py`, `ros_monitor.py`, `app_deploy_run_server.py`, `test_rpm_pulse.sh` | – |
| C12 | `hs_err_pid608.log` committed | Git-ignored; run `git rm hs_err_pid608.log` in your repo root | – |

## Behaviour changes you should know about

* **Enable Motors** does nothing by default now (it used to relaunch everything). Restore with
  `REAL1_ENABLE_MOTORS_MODE=restart_launch`.
* **Login lasts 12 h** (was 1 h) and tokens are invalidated by a server restart unless
  `ROBOT_JWT_SECRET` is set.
* **Goals are refused until the robot is localized** (`ROBOT_REQUIRE_LOCALIZED=0` to disable).
* **Teleop is clamped** to 0.6 m/s / 1.5 rad/s server-side, regardless of the app.
* `/robot/stop` returns `stopping` immediately; the status becomes `idle` once the stack is down.
* Map is a **PNG**, served from Nav2's `/map` when available.
* A stack started in another terminal is detected and reused (START won't launch a duplicate).

## Not changed / not verifiable here

* The ROS workspace (`articubot_one1`) — its files were empty in the archive. See
  `docs/NAV_SIDE_CHECKLIST.md`.
* The real rclpy node has not run on a robot. See the on-robot checklist.
* Dart files were not compiled (no Flutter SDK). Run `flutter analyze`.
* Python tested on 3.12; the Pi (Ubuntu 22.04 / Humble) uses 3.10 — the code avoids 3.11+ features,
  but run `python -m pytest` on the Pi once.
