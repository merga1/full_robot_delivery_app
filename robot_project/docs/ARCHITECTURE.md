# Architecture and implementation status

**Project:** Smart Restaurant Ordering, Kitchen Operations, Flutter Mobile Control,
ROS 2 Autonomous Food Delivery Robot
**Assessment date:** 2026-10-22
**Assessor:** architecture review of the delivered workspace + live inspection of the
deployed customer website.

> The first half of this document is the original gap assessment. The implementation work
> that followed is summarized in [PROJECT_STATUS.md](../PROJECT_STATUS.md). This report
> states only what was **verified by inspection or executed**. Anything that
> could not be verified is listed in [§9 Blockers](#9-blockers--what-was-not-verified) and
> is never reported as working.

---

## 1. Existing project structure

```
robot_project/
├── README.md                     # robot controller overview (existing)
├── CHANGES.md                    # prior fix log for the robot API (existing)
├── docs/
│   ├── NAV_SIDE_CHECKLIST.md     # ROS-side verification checklist (existing)
│   └── ARCHITECTURE.md           # THIS FILE
├── backend/                      # AUTHORITATIVE FastAPI/PostgreSQL restaurant backend
├── web/                          # customer, kitchen, admin, robot-screen UIs
├── robot_gateway/                # outbound authenticated ROS 2/Nav2 gateway
├── raspberry_pi/                 # EXISTING robot-edge FastAPI (SQLite + ROS 2 hub)
│   ├── app/  main.py, robot_link.py, ros_hub.py, missions.py, teleop.py,
│   │          ordering.py, qr.py, database.py, auth.py, settings.py
│   ├── ros2_nodes/navigate_client.py
│   ├── tests/  test_backend.py, test_qr.py, test_ros_wiring.py + fake_ros/ stubs
│   └── *.sh, *.service, real1_link.env.example
├── flutter_app/                  # EXISTING Flutter tablet controller
│   └── lib/ screens/{home,navigation,missions,logs,login,settings},
│       services/{robot_service,pi_discovery,pi_remote,notification,prefs}_service.dart
└── flutter_app/android/, windows/, assets/
```

The original assessment found the items above missing. They are now implemented under
`backend/`, `web/`, `robot_gateway/`, and `docker-compose.yml`; the physical ROS 2
workspace and hardware remain outside this repository.

---

## 2. Current technology stack (verified)

| Layer | Technology | Evidence |
|---|---|---|
| Robot edge API | Python 3.10 target, FastAPI, Uvicorn | `raspberry_pi/app/main.py:53` |
| Robot edge DB | **SQLite** (`robot.db`) + JSON file for destinations | `raspberry_pi/app/database.py:27-29` |
| Robot edge auth | `python-jose` HS256 JWT + `bcrypt` | `raspberry_pi/app/auth.py:24-27` |
| Robotics | ROS 2 **Humble** (`/opt/ros/humble/setup.bash` default) + Nav2 | `raspberry_pi/app/robot_link.py:38` |
| Nav2 interface | `NavigateToPose` action, `/bt_navigator` lifecycle node | `raspberry_pi/app/ros_hub.py:69-70` |
| Teleop | `/cmd_vel` publisher, `twist_mux` integration | `raspberry_pi/app/ros_hub.py:59` |
| Map | Nav2 live `/map` topic → PNG (Pillow), fallback map YAML | `raspberry_pi/app/main.py:178-222` |
| Mobile app | Flutter 3.47.7 stable, Dart ^3.12.2, `http`, `web_socket_channel` | `flutter_app/pubspec.yaml` |
| Customer site | **Static vanilla HTML/CSS/JS** (no framework, no build step) | fetched from Netlify |
| Order transport (current) | HTTPS/WSS → FastAPI `/api/v1` | `backend/app/api/routes/`, `web/` |
| Local toolchain (this machine) | Python 3.14.3, PostgreSQL **18.4** (running), Docker 29.5.2, Flutter 3.47.7, JDK 17, Android SDK present | shell probes |

**Python version note:** the machine has 3.14.3; the Pi runs 3.10 (Ubuntu 22.04). The new
backend therefore avoids 3.11+ syntax and is pinned to `requires-python = ">=3.10"`.

---

## 3. Existing functionality that must be preserved

| # | Capability | Location | Keep? |
|---|---|---|---|
| E1 | Nav2 `NavigateToPose` client with explicit succeeded/failed/cancelled states | `app/ros_hub.py` | Keep — mirrored in the gateway's Nav2 adapter |
| E2 | Robot stack lifecycle (start/stop `ros2 launch`, process-group kill) | `app/robot_link.py` | Keep — used by the robot-edge app only |
| E3 | Live `/map` → PNG so app map == Nav2 map | `app/main.py` | Keep |
| E4 | AMCL initial-pose setting, `localized` gate | `app/ros_hub.py:425` | Keep |
| E5 | Joystick teleop with arming + clamp + watchdog | `app/teleop.py` | Keep — emergency/operator control |
| E6 | Multi-waypoint local missions | `app/missions.py` | Keep — **operator ad-hoc navigation only** (see §5.3) |
| E7 | Dependency-free QR encoder (byte mode, ECC M, v1–10) | `app/qr.py` | Keep — reused for table QR codes |
| E8 | Order/menu/table **business logic** (server-side pricing, status machine) | `app/ordering.py` | **Port** to the new backend service layer |
| E9 | Flutter nav/joystick/mission screens | `flutter_app/lib/screens/` | Keep — extend with ordering/robot-ops features |
| E10 | Customer website look & feel (robot idle screen, dark mode, Amharic TTS) | Netlify `style.css`, `script.js` | Keep — re-implemented with API integration |

**`app/ordering.py` is dead code today:** `main.py:34` imports only
`database, missions, robot_link, ros_hub` — `ordering` and `qr` are never wired into a
route. The order status machine (`placed→accepted→preparing→ready→delivering→delivered`)
therefore exists but is unreachable over HTTP. This is the single largest gap.

---

## 4. Missing functionality (gap analysis)

| # | Gap | Severity | Target phase |
|---|---|---|---|
| G1 | No PostgreSQL persistence (SQLite on the Pi cannot be the authoritative multi-client store) | Critical | Phase 3 |
| G2 | No SQLAlchemy models / Alembic migrations | Critical | Phase 3 |
| G3 | No WebSocket event distribution to kitchen/admin/Flutter | Critical | Phase 5 |
| G4 | No ROS 2 **gateway** that speaks to the backend (the Pi API is a local HTTP API, not a secure outbound gateway) | Critical | Phase 11 |
| G5 | Order/table/menu endpoints unreachable (`ordering.py` unwired) | Critical | Phase 4 |
| G6 | No kitchen dashboard | Critical | Phase 8 |
| G7 | No admin dashboard | Critical | Phase 9 |
| G8 | No robot touchscreen UI | High | Phase 13 |
| G9 | Customer site cannot track an order; no order number; no menu from DB; prices are hard-coded | High | Phase 7 |
| G10 | No robot assignment / loading-confirmation / collection-confirmation steps | High | Phase 4 |
| G11 | No automatic or manual return-home workflow | High | Phase 14 |
| G12 | No notification service, outbox, audit log, notification history | High | Phase 6 |
| G13 | No role model (customer / kitchen / admin / gateway / robot touchscreen) | High | Phase 4 |
| G14 | No idempotency on order creation or dispatch | Medium | Phase 5 |
| G15 | No heartbeat/connection-state tracking for robots | High | Phase 11 |
| G16 | No TTS on the robot itself (only browser `speechSynthesis`) | Medium | Phase 13 |
| G17 | No Docker Compose / deployment manifests | Medium | Phase 17 |

---

## 5. Target architecture

### 5.1 Logical topology (as required)

```
CUSTOMER WEBSITE ─┐
KITCHEN DASHBOARD─┼─ HTTPS/REST + WSS ─▶ FASTAPI BACKEND ◀─ WSS ─▶ ROS 2 GATEWAY
ADMIN DASHBOARD ──┤                            │                  (on the robot)
FLUTTER APP ──────┤                            ▼
ROBOT TOUCHSCREEN─┘                      POSTGRESQL (+ Redis-less
                                          in-process event hub + DB outbox)
```

The **backend never commands motors**. It issues *mission commands* to a gateway over an
authenticated outbound WebSocket and only advances a mission's state when the gateway
reports a validated event. This satisfies "FastAPI must not pretend to control the
physical robot directly merely by updating a database row."

### 5.2 Why the gateway connects *outbound*

The robot sits on a venue LAN behind NAT. The gateway dials the backend over TLS
(`wss://…/api/v1/robot-gateway`), authenticates with a per-robot credential, and heartbeats.
No ROS 2 DDS port is exposed to the internet; the backend is the only public surface.

### 5.3 Coexistence with the existing robot-edge API

Two navigation paths exist and are **explicitly separated**:

| Path | Owner | Used for | Mission records |
|---|---|---|---|
| Robot-edge API (`raspberry_pi/`) | local operator tablet | manual navigation, joystick, ad-hoc multi-waypoint waypointing, local map editing | `missions` table on the Pi (local only) |
| Delivery path (new gateway) | backend | order delivery, return-home | `robot_missions` + `mission_events` in PostgreSQL |

The gateway drives Nav2 through the **action server** (no `/cmd_vel` publishing), so it
cannot fight the operator's teleop channel. Backend missions and Pi-local missions are
never interchangeable; the backend is authoritative for deliveries.

### 5.4 Data flow of one delivery

```
1  customer web  POST /api/v1/orders           → Order(PENDING)            → outbox
2  kitchen WS    order.created                 → sound + popup
3  kitchen       POST /orders/{id}/accept      → Order(ACCEPTED)
4  kitchen       POST /orders/{id}/start-preparation → PREPARING
5  kitchen       POST /orders/{id}/ready       → READY_FOR_PICKUP → READY_FOR_DISPATCH
6  admin         POST /orders/{id}/assign-robot→ RobotMission(ASSIGNED), cmd ASSIGN_MISSION
7  staff        POST /orders/{id}/confirm-loaded → Order(LOADED), mission AWAITING_LOAD_CONFIRMATION
8  admin         POST /orders/{id}/dispatch     → mission DISPATCH_REQUESTED, cmd DISPATCH_MISSION
9  gateway       NAVIGATION_STARTED            → Order(OUT_FOR_DELIVERY)
10 gateway       NAVIGATION_SUCCEEDED (+pose)  → mission ARRIVED_AT_DESTINATION, Order(ARRIVED_AT_TABLE)
11 touchscreen / customer  POST …/confirm-collection → COLLECTION_CONFIRMED
12 backend       auto return → RETURN_HOME cmd → mission RETURNING_HOME → DOCKED_OR_HOME → COMPLETED
13 backend       Order(DELIVERED) + outbox events
```

Every arrow is a **persisted** transition. Step 11 cannot happen without a collection
confirmation; step 12 cannot happen while the robot is in a fault state.

---

## 6. Integration boundaries (exact contracts)

| # | Boundary | Protocol | Contract location |
|---|---|---|---|
| B1 | Browser/Flutter → backend | HTTPS REST `/api/v1/*` | `backend/app/api/routes/` |
| B2 | Browser/Flutter → backend (live) | WSS `/api/v1/ws/{channel}` | `backend/app/api/routes/websocket.py` |
| B3 | Gateway → backend | WSS `/api/v1/robot-gateway` | `backend/app/api/routes/websocket.py` + `backend/app/services/gateway_registry.py` |
| B4 | Backend → Postgres | asyncpg / SQLAlchemy 2.x async | `backend/app/db/` |
| B5 | Backend → robot (commands) | Gateway WS, `command` frames | `backend/app/events/schemas.py` |
| B6 | Robot → backend (events) | Gateway WS, `mission_event` / `telemetry` frames | `backend/app/events/schemas.py` |
| B7 | Gateway → Nav2 | ROS 2 action `navigate_to_pose` | `robot_gateway/nav2_adapter.py` |
| B8 | Gateway → ROS topics | optional telemetry adapter boundary | `robot_gateway/nav2_adapter.py`, gateway heartbeat contract |
| B9 | Gateway → speaker | `espeak-ng`/`piper` subprocess (configurable) | `robot_gateway/audio_announcer.py` |
| B10 | Touchscreen → backend | REST (robot-screen token) | `backend/app/api/routes/` |
| B11 | Netlify site → backend | CORS allow-list for the site origin | `backend/app/core/config.py` |

---

## 7. Data model (Phase 3)

`User`, `Customer`, `MenuItem`, `RestaurantTable`, `Order`, `OrderItem`,
`OrderStatusHistory`, `Robot`, `RobotMission`, `MissionEvent`, `Notification`,
`AuditLog`, `OutboxEvent`, `IdempotencyKey`.

Key invariants enforced by the database, not only by application code:

* `Order.code` unique; `OrderItem` stores the **price snapshot** at order time.
* `RobotMission.mission_uid` unique; `Order.mission_id` → `RobotMission.id` unique
  (one order can never have two live missions).
* `OrderStatusHistory` is append-only.
* `OutboxEvent.payload` + `type` unique on `(event_id)` → a crash cannot double-publish.
* `IdempotencyKey.key` unique per `(scope, key)`.

---

## 8. Environment variables

Canonical list: `backend/.env.example`. Critical ones:

`DATABASE_URL`, `JWT_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`,
`CORS_ORIGINS` (comma-separated, **no wildcard**), `ROBOT_GATEWAY_ENABLED`,
`ROBOT_GATEWAY_HEARTBEAT_TIMEOUT_SECONDS`, `AUTO_RETURN_HOME`,
`LOW_BATTERY_THRESHOLD_PERCENT`, `FCM_PROJECT_ID`, `FCM_CREDENTIALS_PATH`,
`LOG_LEVEL`, `ROBOT_SCREEN_TOKEN`, `OUTBOX_POLL_INTERVAL_SECONDS`.

Gateway side: `GATEWAY_BACKEND_URL`, `GATEWAY_ROBOT_ID`, `GATEWAY_ROBOT_TOKEN`,
`GATEWAY_HEARTBEAT_SECONDS`, `GATEWAY_NAV_ACTION`, `GATEWAY_DESTINATIONS_FILE`.

---

## 9. Blockers — what was **not** verified

| Blocker | Detail | Handling |
|---|---|---|
| B-1 | **No physical robot access** in this environment (Windows dev box, no ROS 2, no `articubot_one1` workspace, no lidar/odom/battery topics) | The gateway has a deterministic simulation adapter and optional ROS 2/Nav2 adapter. **No physical result is claimed.** |
| B-2 | **Separate customer hosting has no credentials here** | The customer source is now in `web/customer/` and is served by FastAPI; no external hosting push was performed. |
| B-3 | **The old Render order server is not a supported dependency** | The new backend replaces it; the legacy branded pages are rewired to `/api/v1`. |
| B-4 | **FCM credentials absent** | Push is implemented behind a provider abstraction with a **console/no-op provider** fallback; push delivery is reported as *not verified*. |
| B-5 | **Table destination coordinates unknown** | Table→destination mapping is stored in `RestaurantTable.destination_id` and must be **measured and verified on the robot**. The gateway keeps its own destination registry and rejects unknown destination ids. **No coordinates are invented.** |
| B-6 | **Battery topic / speaker hardware unknown** | Gateway reads battery from a configurable topic (absent → `None`, reported as *unavailable*, never faked) and speaks through a configurable TTS binary (absent → announcements are queued and shown on the touchscreen only). |

---

## 10. Risks

| R | Risk | Mitigation |
|---|---|---|
| R1 | Two mission systems (Pi-local + backend) both claim the robot | Mission ownership is exclusive: the gateway rejects a second mission while one is live; the Pi path never writes backend mission rows. |
| R2 | Dispatched-but-never-arrived (backend thinks it is delivering, robot is idle) | Only the gateway's `NAVIGATION_SUCCEEDED` event (with pose validation) advances the mission; heartbeat timeout marks the robot `OFFLINE` and the mission `RECOVERY_REQUIRED`. |
| R3 | Duplicate order creation on retry | `Idempotency-Key` header + unique DB constraint. |
| R4 | Robot executes an expired/duplicate command | Gateway validates `command_id` uniqueness, `expires_at`, mission correlation and destination registry membership before touching Nav2. |
| R5 | Frontend shows "delivered" before collection confirmation | The order state machine has no edge into `DELIVERED` except `COLLECTION_CONFIRMED`. |
| R6 | Battery/heartbeat telemetry fabricated | Gateway sends `None` when the source is absent; the UI renders "N/A". |
| R7 | CORS wildcard + credentials | `CORS_ORIGINS` is an explicit allow-list; `allow_credentials` is only enabled when explicit origins are configured. |

---

## 11. Implementation sequence

| Phase | Deliverable | Gate (evidence required) |
|---|---|---|
| 1 | This assessment + `PROJECT_STATUS.md` | ✅ inspection evidence above |
| 2 | `backend/` FastAPI app, config, security, health, logging | `/api/health` + `/docs` respond; pytest green |
| 3 | SQLAlchemy models + Alembic migration on PostgreSQL 18 | `alembic upgrade head` on a clean DB; `alembic downgrade base` |
| 4 | Repositories, services, order + mission state machines, RBAC | unit tests for every legal/illegal transition |
| 5 | REST `/api/v1/*`, WS channels, gateway WS, idempotency, outbox | API + WS integration tests |
| 6 | Notification service (browser/Flutter/push/TTS routing, dedup, history) | notification tests |
| 7 | Customer website integration (menu, cart, order, live timeline) | website served + order created in real DB |
| 8 | Kitchen dashboard (sound, popup, large buttons, authoritative refetch) | browser flow vs backend |
| 9 | Admin dashboard (ops overview, orders, robots, tables, commands) | browser flow vs backend |
| 10 | Flutter app: customer ordering + robot ops + return home | `flutter analyze` + `flutter test` |
| 11 | ROS 2 gateway package (auth, heartbeat, Nav2 adapter, telemetry, safety, recovery) | mock-rclpy test suite |
| 12 | Robot touchscreen + TTS | served UI + gateway announcement tests |
| 13 | Return-home (auto + manual), conflict handling | service tests |
| 14 | Security hardening, failure/recovery paths | tests |
| 15 | Docker Compose, deployment docs | `docker compose config` |
| 16 | End-to-end acceptance scenario A–H | automated E2E script; **hardware legs marked unverified** |

---

## 12. Acceptance criteria (summary)

A phase is **complete** only with executable evidence. Concretely:

* `pytest` in `backend/` green, using a real PostgreSQL test database.
* `alembic upgrade head` / `downgrade base` succeed against PostgreSQL 18.
* `flutter analyze` clean and `flutter test` green.
* ROS 2 gateway tests run under mock `rclpy` — **no claim of physical navigation**.
* An end-to-end script drives: customer order → kitchen accept → ready → load →
  dispatch → gateway ack → simulated arrival → collection → return home → delivered,
  asserting the order never skips a state and never reports success without an event.
* Physical robot legs (sections 18.6 / Test D, E, F, G on hardware) are explicitly
  reported **NOT VERIFIED** until a human runs them on the robot with e-stop access.
