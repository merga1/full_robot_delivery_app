import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="robot-test-")
os.environ.update(
    ROBOT_ALLOW_SIM="1",
    ROBOT_DATA_DIR=_tmp,
    ROBOT_SIM_SPEED="50",
    ROBOT_ADMIN_PASS="adminpw",
    ROBOT_USER_PASS="userpw",
    ROBOT_KITCHEN_PASS="kitchenpw",
    ROBOT_JWT_SECRET="test-secret",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200, r.text
        c.headers.update({"Authorization": "Bearer " + r.json()["access_token"]})
        yield c
