"""Runs the real rclpy node code in app/ros_hub.py against fake_ros stubs (no ROS install)."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def test_rclpy_wiring_with_fake_ros():
    env = dict(os.environ, PYTHONPATH=str(HERE / "fake_ros"))
    env.pop("ROBOT_ALLOW_SIM", None)
    res = subprocess.run([sys.executable, str(HERE / "wiring_script.py")],
                         capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode == 0 and "WIRING-OK" in res.stdout, res.stdout + res.stderr
