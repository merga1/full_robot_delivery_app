from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Destination:
    destination_id: str
    label: str
    pose: dict[str, Any]


class DestinationRegistry:
    def __init__(self, destinations: dict[str, Destination]) -> None:
        self._destinations = destinations

    @classmethod
    def from_file(cls, path: Path) -> "DestinationRegistry":
        if not path.is_file():
            return cls({})
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("destinations", raw) if isinstance(raw, dict) else raw
        parsed: dict[str, Destination] = {}
        if not isinstance(entries, list):
            raise ValueError("destinations file must contain a list or {destinations: []}")
        for entry in entries:
            if isinstance(entry, str):
                parsed[entry] = Destination(entry, entry, {})
                continue
            if not isinstance(entry, dict) or not entry.get("id"):
                raise ValueError("each destination needs an id")
            destination_id = str(entry["id"])
            parsed[destination_id] = Destination(
                destination_id=destination_id,
                label=str(entry.get("label") or destination_id),
                pose=dict(entry.get("pose") or {}),
            )
        return cls(parsed)

    def ids(self) -> list[str]:
        return sorted(self._destinations)

    def get(self, destination_id: str) -> Destination | None:
        return self._destinations.get(destination_id)

    def require(self, destination_id: str) -> Destination:
        destination = self.get(destination_id)
        if destination is None:
            raise ValueError(f"unknown destination '{destination_id}'")
        return destination
