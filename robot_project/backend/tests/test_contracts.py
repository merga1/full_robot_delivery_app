from datetime import datetime, timedelta, timezone

import pytest

from app.events.schemas import GatewayHello, GatewayMissionEvent, parse_gateway_frame
from app.state_machines import mission as mission_sm
from app.state_machines import order as order_sm


def test_order_workflow_rejects_skipping_kitchen() -> None:
    assert order_sm.can_transition("PENDING", "ACCEPTED", "kitchen")
    assert not order_sm.can_transition("PENDING", "OUT_FOR_DELIVERY", "admin")
    with pytest.raises(order_sm.OrderTransitionError):
        order_sm.require_transition("PENDING", "DELIVERED", "admin")


def test_customer_timeline_has_arrival_and_collection() -> None:
    timeline = order_sm.customer_visible_timeline("ARRIVED_AT_TABLE")
    assert [step["state"] for step in timeline][-2:] == ["current", "upcoming"]
    assert order_sm.customer_status_detail("ARRIVED_AT_TABLE")


def test_gateway_frames_are_typed() -> None:
    frame = parse_gateway_frame('{"frame":"HELLO","robot_id":"robot_01","destinations":["home"]}')
    assert isinstance(frame, GatewayHello)
    event = parse_gateway_frame('{"frame":"MISSION_EVENT","robot_id":"robot_01","mission_uid":"msn_1","event_type":"NAVIGATION_STARTED"}')
    assert isinstance(event, GatewayMissionEvent)


def test_mission_requires_real_robot_event() -> None:
    assert mission_sm.can_transition("DISPATCH_REQUESTED", "NAVIGATING_TO_TABLE", "system")
    assert not mission_sm.can_transition("DISPATCH_REQUESTED", "COMPLETED", "admin")
