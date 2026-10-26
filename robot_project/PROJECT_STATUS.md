# Smart restaurant project status

## Implemented in this workspace

- FastAPI/PostgreSQL source of truth for menu, tables, orders, users, missions, robots, audit history, notifications, and WebSockets.
- Customer ordering and private tracking at `web/customer`.
- Kitchen workflow dashboard at `web/kitchen`.
- Admin overview, order dispatch, robot, table, and audit screens at `web/admin`.
- Robot touchscreen mission and collection/assistance controls at `web/robot-screen`.
- Authenticated robot gateway with reconnects, command expiry, duplicate protection, mission events, heartbeats, optional simulation, and optional ROS 2/Nav2 adapter.
- Flutter ordering/customer/kitchen/table screens now target the unified `/api/v1` backend, while existing low-level robot control still targets the Raspberry Pi API.
- Docker Compose for PostgreSQL and the backend, idempotent seed data, environment examples, and a corrected backend Docker build context.
- Legacy branded customer and kitchen pages now use the unified backend instead of the old Render WebSocket service.

## Still requires real deployment or hardware

- Create `backend/.env` from `backend/.env.example` and set a real JWT secret and production passwords. The example credentials are development-only.
- Add real table `destination_id` values after measuring/validating poses in the robot's map.
- Create a robot gateway token using the protected backend endpoint and install the gateway in the robot's ROS 2 environment.
- Replace `robot_gateway/config/destinations.example.json` with measured poses.
- Verify Nav2, localization, motor controller, e-stop, battery telemetry, TTS, and obstacle recovery on the physical robot.
- Generate/print QR codes for the seeded table public codes (`T01`–`T05`) using the production customer URL.

The Windows workspace can validate Python imports, API contracts, static JavaScript syntax, and Flutter source structure. It cannot prove that motors, sensors, Nav2, or a real ROS 2 graph work.
