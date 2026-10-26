# Smart Restaurant backend

This is the authoritative FastAPI service for orders, kitchen workflow, staff
permissions, robot missions, notifications, and the outbound robot-gateway
connection. The Raspberry Pi local API remains available for manual teleoperation
and emergency/operator control; it must not be used as the restaurant order store.

## Local setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000`, documentation at `/docs`, and
the browser surfaces at `/customer`, `/kitchen`, `/admin`, and `/robot-screen`.

The Dockerfile is built with the parent `robot_project` directory as its context;
use `docker compose up --build` from that directory.

## Database

PostgreSQL is required. The initial migration is in `alembic/versions/`. Do not
use the Pi SQLite database as the shared production database.

## Test policy

Run `pytest` with a clean PostgreSQL test database. Hardware navigation is not
claimed by software tests; the gateway has a deterministic simulated adapter for
development and a ROS 2 adapter that must be tested on the actual robot.

