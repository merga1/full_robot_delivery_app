from datetime import datetime, timedelta, timezone

from robot_gateway.backend_contract import BackendCommand, command_expired
from robot_gateway.destinations import DestinationRegistry


def test_expired_command_is_rejected() -> None:
    command = BackendCommand(
        command_id="cmd_1",
        command="DISPATCH_MISSION",
        robot_id="robot_01",
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    assert command_expired(command)


def test_destination_registry_reads_ids(tmp_path) -> None:
    path = tmp_path / "destinations.json"
    path.write_text('[{"id":"home","pose":{"x":0}}]', encoding="utf-8")
    registry = DestinationRegistry.from_file(path)
    assert registry.ids() == ["home"]
    assert registry.require("home").pose["x"] == 0
