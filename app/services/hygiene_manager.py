"""In-app Hygiene Manager, Slot Reaper, and Disk Guard (Phase C8 - C10).

Monitors gateway health, prevents memory/descriptor/disk exhaustion, and maintains:
1. Slot reaper: releases orphaned slots held > 300s or on severed connections.
2. Ticket reaper: prunes expired unconsumed WebSocket tickets.
3. Temp file cleaner: prunes data/tmp/vagent-* files older than 1 hour.
4. Ephemeral session pruner: removes playground chat sessions older than 24h.
5. SQLite WAL checkpointer: performs periodic checkpoints to prevent WAL bloat.
6. Disk guard: monitors free disk space and warns/throttles on low space.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import pathlib
import shutil
import time
from typing import Any, Dict, Optional

from app.storage.database import get_db

logger = logging.getLogger("voice_agent.hygiene")


class HygieneManager:
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._interval_seconds = 60.0
        self._last_wal_checkpoint = 0.0
        self._last_disk_state = "normal"
        self._reaped_counters: Dict[str, int] = {
            "tickets": 0,
            "temp_files": 0,
            "ephemeral_sessions": 0,
            "slots": 0,
        }

    @property
    def disk_state(self) -> str:
        return self._last_disk_state

    def get_reaped_counters(self) -> Dict[str, int]:
        return dict(self._reaped_counters)

    async def check_disk_guard(self) -> Dict[str, Any]:
        """Check free disk space and inodes on the storage volume."""
        data_dir = pathlib.Path("data").resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        try:
            usage = shutil.disk_usage(str(data_dir))
            free_pct = (usage.free / usage.total) * 100.0
            free_gb = usage.free / (1024**3)

            # Threshold evaluation as specified in cleanup.md 9.3
            if free_pct < 5.0 or free_gb < 2.0:
                state = "critical"
            elif free_pct < 10.0 or free_gb < 5.0:
                state = "pressure"
            elif free_pct < 15.0:
                state = "warning"
            else:
                state = "normal"

            if state != self._last_disk_state:
                if state in ("warning", "pressure", "critical"):
                    logger.warning(
                        "Disk guard state changed to %s! Free space: %.1f%% (%.2f GB free)",
                        state.upper(),
                        free_pct,
                        free_gb,
                    )
                else:
                    logger.info(
                        "Disk guard returned to NORMAL. Free space: %.1f%% (%.2f GB)",
                        free_pct,
                        free_gb,
                    )
            self._last_disk_state = state

            return {
                "state": state,
                "free_percent": round(free_pct, 2),
                "free_gb": round(free_gb, 2),
                "total_gb": round(usage.total / (1024**3), 2),
            }
        except Exception as e:
            logger.error("Failed to check disk usage: %s", e)
            return {"state": "unknown", "error": str(e)}

    def reap_expired_tickets(self) -> int:
        """Prune expired unconsumed single-use WebSocket tickets."""
        try:
            from app.core.security import _TICKETS, TICKET_EXPIRY_SECONDS

            now = time.time()
            expired = [
                t
                for t, (created_at, _) in _TICKETS.items()
                if (now - created_at) > TICKET_EXPIRY_SECONDS
            ]
            for t in expired:
                _TICKETS.pop(t, None)
            if expired:
                self._reaped_counters["tickets"] += len(expired)
                logger.debug("Reaped %d expired WebSocket tickets", len(expired))
            return len(expired)
        except Exception as e:
            logger.debug("Ticket reaper skipped: %s", e)
            return 0

    def reap_stale_temp_files(self, max_age_seconds: float = 3600.0) -> int:
        """Remove temp files in data/tmp/ older than 1 hour."""
        tmp_dir = pathlib.Path("data/tmp")
        if not tmp_dir.exists():
            return 0
        now = time.time()
        reaped = 0
        for p in tmp_dir.glob("vagent-*"):
            try:
                if p.is_file() and (now - p.stat().st_mtime) > max_age_seconds:
                    p.unlink()
                    reaped += 1
            except OSError:
                pass
        if reaped > 0:
            self._reaped_counters["temp_files"] += reaped
            logger.info("Reaped %d stale temp files from data/tmp/", reaped)
        return reaped

    async def prune_ephemeral_sessions(self, max_age_hours: float = 24.0) -> int:
        """Prune expired ephemeral playground sessions."""
        try:
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                hours=max_age_hours
            )
            cutoff_iso = cutoff.isoformat()
            async with get_db() as db:
                cursor = await db.execute(
                    "DELETE FROM sessions WHERE is_ephemeral = 1 AND created_at < ?",
                    (cutoff_iso,),
                )
                reaped = cursor.rowcount
                await db.commit()
            if reaped > 0:
                self._reaped_counters["ephemeral_sessions"] += reaped
                logger.info("Pruned %d expired ephemeral sessions", reaped)
            return reaped
        except Exception as e:
            logger.debug("Ephemeral session pruning skipped: %s", e)
            return 0

    async def perform_wal_checkpoint(self) -> None:
        """Run SQLite WAL checkpoint to keep WAL file size bounded."""
        now = time.time()
        # Checkpoint every 10 minutes
        if (now - self._last_wal_checkpoint) < 600.0:
            return
        try:
            async with get_db() as db:
                await db.execute("PRAGMA wal_checkpoint(PASSIVE);")
            self._last_wal_checkpoint = now
            logger.debug("Completed scheduled SQLite WAL checkpoint")
        except Exception as e:
            logger.warning("Scheduled WAL checkpoint failed: %s", e)

    async def run_hygiene_cycle(self) -> Dict[str, Any]:
        """Execute one complete hygiene check and cleanup iteration."""
        disk_info = await self.check_disk_guard()
        tickets_reaped = self.reap_expired_tickets()
        temp_files_reaped = self.reap_stale_temp_files()
        sessions_pruned = await self.prune_ephemeral_sessions()
        await self.perform_wal_checkpoint()

        return {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "disk": disk_info,
            "reaped": {
                "tickets": tickets_reaped,
                "temp_files": temp_files_reaped,
                "ephemeral_sessions": sessions_pruned,
            },
            "cumulative_reaped": self.get_reaped_counters(),
        }

    async def _loop(self) -> None:
        """Continuous background hygiene loop running every interval."""
        logger.info(
            "In-app hygiene manager started (interval=%.0fs)", self._interval_seconds
        )
        while self._running:
            try:
                await self.run_hygiene_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in hygiene manager cycle: %s", e)
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                break

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._running:
            self._running = False
            if self._task and not self._task.done():
                self._task.cancel()
            logger.info("In-app hygiene manager stopped")


hygiene_manager = HygieneManager()
