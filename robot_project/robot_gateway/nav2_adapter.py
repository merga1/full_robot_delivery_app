from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .destinations import Destination

log = logging.getLogger("robot.gateway.nav2")
ProgressCallback = Callable[[float], Awaitable[None]]


class NavigationUnavailable(RuntimeError):
    pass


class NavigationAdapter:
    destination_ids: list[str]

    async def navigate(
        self,
        destination: Destination,
        *,
        on_progress: ProgressCallback,
        cancel_event: asyncio.Event,
    ) -> bool:
        raise NotImplementedError


@dataclass
class SimulationNavigationAdapter(NavigationAdapter):
    destination_ids: list[str]
    seconds: float = 3.0

    async def navigate(self, destination: Destination, *, on_progress: ProgressCallback,
                       cancel_event: asyncio.Event) -> bool:
        if destination.destination_id not in self.destination_ids:
            raise NavigationUnavailable(f"destination '{destination.destination_id}' is not configured")
        steps = 10
        for step in range(steps + 1):
            if cancel_event.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(self.seconds / steps)
            await on_progress(round(step * 100 / steps, 1))
        return True

    async def cancel(self) -> None:
        return None


class RosNav2Adapter(NavigationAdapter):
    """Small Nav2 adapter. ROS imports remain optional on development machines."""

    def __init__(self, destination_ids: list[str], action_name: str) -> None:
        try:
            import rclpy
            from rclpy.action import ActionClient
            from rclpy.executors import MultiThreadedExecutor
            from nav2_msgs.action import NavigateToPose
            from geometry_msgs.msg import PoseStamped
        except ImportError as exc:
            raise NavigationUnavailable("ROS 2 rclpy/nav2_msgs are not installed") from exc
        self.destination_ids = destination_ids
        self._rclpy = rclpy
        self._ActionClient = ActionClient
        self._NavigateToPose = NavigateToPose
        self._PoseStamped = PoseStamped
        rclpy.init(args=None)
        self._node = rclpy.create_node("restaurant_robot_gateway")
        self._executor = MultiThreadedExecutor()
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._thread.start()
        self._client = ActionClient(self._node, NavigateToPose, action_name)
        self._goal_handle = None

    async def navigate(self, destination: Destination, *, on_progress: ProgressCallback,
                       cancel_event: asyncio.Event) -> bool:
        if destination.destination_id not in self.destination_ids:
            raise NavigationUnavailable(f"destination '{destination.destination_id}' is not configured")
        pose = destination.pose
        if not pose:
            raise NavigationUnavailable(f"destination '{destination.destination_id}' has no pose")
        if not self._client.wait_for_server(timeout_sec=5.0):
            raise NavigationUnavailable("Nav2 NavigateToPose action server is not available")
        goal = self._NavigateToPose.Goal()
        msg = self._PoseStamped()
        msg.header.frame_id = str(pose.get("frame_id", "map"))
        msg.pose.position.x = float(pose.get("x", 0))
        msg.pose.position.y = float(pose.get("y", 0))
        msg.pose.position.z = float(pose.get("z", 0))
        msg.pose.orientation.z = float(pose.get("qz", 0))
        msg.pose.orientation.w = float(pose.get("qw", 1))
        goal.pose = msg
        future = self._client.send_goal_async(goal)
        goal_handle = await asyncio.wrap_future(future)
        if not goal_handle.accepted:
            raise NavigationUnavailable("Nav2 rejected the navigation goal")
        self._goal_handle = goal_handle
        result_future = goal_handle.get_result_async()
        while not result_future.done():
            if cancel_event.is_set():
                await asyncio.wrap_future(goal_handle.cancel_goal_async())
                raise asyncio.CancelledError
            await on_progress(0.0)
            await asyncio.sleep(0.5)
        result = await asyncio.wrap_future(result_future)
        self._goal_handle = None
        return getattr(result, "status", 4) == 4

    async def cancel(self) -> None:
        if self._goal_handle is not None:
            await asyncio.wrap_future(self._goal_handle.cancel_goal_async())
