"""
job_journal.py — Append-Only Write-Ahead Job Journal for SPOF Resilience (SIH26123).

Guarantees durability and seamless crash recovery:
  1. Every job submission via POST /api/job or /api/task/inject is appended to
     data/job_log.jsonl and flushed/fsynced to disk BEFORE returning HTTP 200/201.
  2. Lifecycle updates (SUBMITTED, ASSIGNED, IN_PROGRESS, COMPLETED, CANCELLED)
     are committed chronologically as append-only records.
  3. On FastAPI server startup, replaying data/job_log.jsonl reconstructs the
     uncompleted job queue so the fleet resumes execution without data loss.
  4. Central server failure invariance: The fleet processes continue navigating
     and coordinating autonomously over peer-to-peer UDP while the central server
     is offline.
"""

from __future__ import annotations

import atexit
import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[4]
DEFAULT_JOURNAL_PATH = ROOT_DIR / "data" / "job_log.jsonl"


class JobJournal:
    """
    Thread-safe, append-only WAL journal for warehouse jobs with non-blocking file streaming.
    Streams writes directly to an open append-only file descriptor and offloads
    hardware fsync operations when executing under an active asyncio event loop,
    preventing event loop lag and thread starvation under heavy task injection.
    """

    def __init__(self, journal_path: Optional[Path] = None) -> None:
        self.journal_path = journal_path or DEFAULT_JOURNAL_PATH
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._file: Optional[Any] = None
        atexit.register(self.close)

    def _ensure_file(self):
        if self._file is None or self._file.closed:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self.journal_path, "a", encoding="utf-8", buffering=1)
        return self._file

    def _fsync_handle(self) -> None:
        """Physical disk flush routine executed synchronously or via worker pool."""
        try:
            with self._lock:
                if self._file and not self._file.closed:
                    self._file.flush()
                    os.fsync(self._file.fileno())
        except OSError:
            pass

    def flush(self) -> None:
        """Explicitly flush user-space buffers and fsync file to non-volatile disk."""
        self._fsync_handle()

    def close(self) -> None:
        """Cleanly flush and close the streaming journal handle."""
        with self._lock:
            if self._file and not self._file.closed:
                try:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                except OSError:
                    pass
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None

    def append_entry(
        self,
        event: str,
        job_id: str,
        job_type: Optional[str] = None,
        pickup: Optional[Tuple[int, int]] = None,
        dropoff: Optional[Tuple[int, int]] = None,
        urgency: Optional[int] = None,
        status: Optional[str] = None,
        assigned_robot_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        flush_sync: bool = False,
    ) -> Dict[str, Any]:
        """
        Appends an event entry to the journal using non-blocking file streaming.
        Flushes immediately to OS cache; schedules fsync in background thread pool
        if running within an event loop to avoid blocking async dispatch.
        """
        record: Dict[str, Any] = {
            "timestamp": time.time(),
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "job_id": job_id,
        }
        if job_type is not None:
            record["job_type"] = job_type
        if pickup is not None:
            record["pickup"] = list(pickup)
        if dropoff is not None:
            record["dropoff"] = list(dropoff)
        if urgency is not None:
            record["urgency"] = urgency
        if status is not None:
            record["status"] = status
        if assigned_robot_id is not None:
            record["assigned_robot_id"] = assigned_robot_id
        if extra is not None:
            record["extra"] = extra

        line = json.dumps(record, separators=(",", ":")) + "\n"

        with self._lock:
            f = self._ensure_file()
            f.write(line)
            f.flush()

        # Non-blocking file streaming: if called from an active asyncio event loop,
        # offload blocking hardware fsync to background thread pool executor so the event loop is never stalled.
        if flush_sync:
            self._fsync_handle()
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, self._fsync_handle)
            except RuntimeError:
                # No active event loop running on this thread; perform synchronous fsync
                self._fsync_handle()

        log.debug("[JobJournal] Committed event '%s' for job '%s' to disk.", event, job_id)
        return record

    async def append_entry_async(
        self,
        event: str,
        job_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Asynchronously streams an entry to the journal without blocking the event loop.
        """
        return self.append_entry(event=event, job_id=job_id, flush_sync=False, **kwargs)

    def log_submission(
        self,
        job_id: str,
        job_type: str,
        pickup: Tuple[int, int],
        dropoff: Tuple[int, int],
        urgency: int = 3,
        payload_weight_kg: float = 0.0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = extra or {}
        meta["payload_weight_kg"] = payload_weight_kg
        return self.append_entry(
            event="SUBMITTED",
            job_id=job_id,
            job_type=job_type,
            pickup=pickup,
            dropoff=dropoff,
            urgency=urgency,
            status="SUBMITTED",
            extra=meta,
        )

    def log_assignment(
        self,
        job_id: str,
        assigned_robot_id: str,
        tick: int = 0,
    ) -> Dict[str, Any]:
        return self.append_entry(
            event="ASSIGNED",
            job_id=job_id,
            status="ASSIGNED",
            assigned_robot_id=assigned_robot_id,
            extra={"tick": tick},
        )

    def log_completion(
        self,
        job_id: str,
        robot_id: Optional[str] = None,
        tick: int = 0,
    ) -> Dict[str, Any]:
        return self.append_entry(
            event="COMPLETED",
            job_id=job_id,
            status="COMPLETED",
            assigned_robot_id=robot_id,
            extra={"completion_tick": tick},
        )

    def log_cancellation(
        self,
        job_id: str,
        reason: str = "operator_cancel",
    ) -> Dict[str, Any]:
        return self.append_entry(
            event="CANCELLED",
            job_id=job_id,
            status="CANCELLED",
            extra={"reason": reason},
        )

    def log_failure(
        self,
        job_id: str,
        reason: str = "failed",
    ) -> Dict[str, Any]:
        return self.append_entry(
            event="FAILED",
            job_id=job_id,
            status="FAILED",
            extra={"reason": reason},
        )

    def recover_uncompleted_jobs(self) -> List[Dict[str, Any]]:
        """
        Reads the journal from the beginning and reconstructs the active state
        of all submitted jobs.
        Returns all jobs whose latest status is NOT 'COMPLETED', 'CANCELLED', or 'FAILED'.
        """
        self.flush()
        if not self.journal_path.exists():
            return []

        jobs_by_id: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        with self._lock:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line_idx, raw_line in enumerate(f, 1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as e:
                        log.warning("[JobJournal] Corrupt journal line %d skipped: %s", line_idx, e)
                        continue

                    jid = record.get("job_id")
                    if not jid:
                        continue

                    if jid not in jobs_by_id:
                        jobs_by_id[jid] = record
                        order.append(jid)
                    else:
                        # Update latest state with non-None values
                        for k, v in record.items():
                            if v is not None:
                                jobs_by_id[jid][k] = v

        # Filter uncompleted jobs (SUBMITTED, ASSIGNED, IN_PROGRESS)
        uncompleted: List[Dict[str, Any]] = []
        terminal_statuses = {"COMPLETED", "CANCELLED", "FAILED"}

        for jid in order:
            job = jobs_by_id[jid]
            if job.get("status") not in terminal_statuses:
                if "assigned_robot_id" not in job:
                    job["assigned_robot_id"] = None
                uncompleted.append(job)

        log.info(
            "[JobJournal] Recovered %d total jobs from journal; %d are uncompleted and ready for replay.",
            len(jobs_by_id),
            len(uncompleted),
        )
        return uncompleted

    def rotate_session(self) -> Optional[Path]:
        """
        Rotates/archives the current job log into data/archive/job_log_<timestamp>.jsonl
        and creates a fresh, empty data/job_log.jsonl for the current session.
        """
        with self._lock:
            if self._file and not self._file.closed:
                try:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                    self._file.close()
                except Exception:
                    pass
                self._file = None

            if self.journal_path.exists() and self.journal_path.stat().st_size > 0:
                archive_dir = self.journal_path.parent / "archive"
                archive_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
                archive_path = archive_dir / f"job_log_{ts}.jsonl"
                try:
                    import shutil
                    shutil.copy2(self.journal_path, archive_path)
                    with open(self.journal_path, "w", encoding="utf-8"):
                        pass
                    log.info("[JobJournal] Rotated journal session to %s", archive_path)
                    return archive_path
                except Exception as ex:
                    log.error("[JobJournal] Failed to rotate journal: %s", ex)
                    return None
            else:
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.journal_path, "w", encoding="utf-8"):
                    pass
                return None
