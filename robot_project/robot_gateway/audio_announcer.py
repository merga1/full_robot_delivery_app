from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

log = logging.getLogger("robot.gateway.audio")


class AudioAnnouncer:
    """Best-effort local announcement; it never pretends audio was played."""

    def __init__(self, binary: str = "") -> None:
        self.binary = binary or shutil.which("espeak-ng") or shutil.which("espeak") or ""

    async def announce(self, text: str) -> bool:
        if not self.binary:
            log.info("announcement queued (no TTS binary configured): %s", text)
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary, text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            if process.returncode:
                log.warning("TTS failed: %s", stderr.decode(errors="replace").strip())
                return False
            return True
        except (OSError, asyncio.TimeoutError) as exc:
            log.warning("TTS unavailable: %s", exc)
            return False
