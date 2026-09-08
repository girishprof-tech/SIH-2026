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
    Thread-safe, append-only WAL journal for warehouse jobs.
    """

    def __init__(self, journal_path: Optional[Path] = None) -> None:
        self.journal_path = journal_path or DEFAULT_JOURNAL_PATH
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

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
    ) -> Dict[str, Any]:
        """
        Appends an event entry to the journal and immediately flushes & fsyncs to disk.
        Guarantees durability prior to HTTP response return.
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
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

        log.debug("[JobJournal] Committed event '%s' for job '%s' to disk.", event, job_id)
        return record

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

    def recover_uncompleted_jobs(self) -> List[Dict[str, Any]]:
        """
        Reads the journal from the beginning and reconstructs the active state
        of all submitted jobs.
        Returns all jobs whose latest status is NOT 'COMPLETED' and NOT 'CANCELLED'.
        """
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
        terminal_statuses = {"COMPLETED", "CANCELLED"}

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
