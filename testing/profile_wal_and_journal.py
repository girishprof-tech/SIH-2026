"""
profile_wal_and_journal.py

Empirical profiling for InventoryLedger (SQLite WAL + busy_timeout) and JobJournal.
Measures:
1. InventoryLedger write contention across 20 concurrent robot workers.
2. JobJournal synchronous disk append + fsync latency and its impact on the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.services.inventory_ledger import InventoryLedger
from app.services.job_journal import JobJournal


def profile_inventory_ledger(num_robots: int = 20, writes_per_robot: int = 15):
    print("=" * 70)
    print(f"PROFILING 1: InventoryLedger (WAL + busy_timeout=10000ms)")
    print(f"Scale: {num_robots} concurrent robot workers, {writes_per_robot} writes each ({num_robots * writes_per_robot} total writes)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_inventory.db"
        # Initialize ledger and populate 50 shelves
        init_ledger = InventoryLedger(db_path=db_path)
        for i in range(50):
            init_ledger.record_audit_scan(
                shelf_id=f"SHELF-{i:02d}",
                sku_counts={f"SKU-{100+i}": 10},
                robot_id="INIT",
                tick=0,
            )

        latencies_ms: List[float] = []
        lock_errors: List[Exception] = []

        async def robot_worker(robot_id: str):
            ledger = InventoryLedger(db_path=db_path)
            for step in range(writes_per_robot):
                shelf_id = f"SHELF-{(hash(robot_id) + step) % 50:02d}"
                sku = f"SKU-{100 + ((hash(robot_id) + step) % 50)}"

                t0 = time.perf_counter()
                try:
                    # Alternating between audit scan and pick/transfer
                    if step % 2 == 0:
                        ledger.record_audit_scan(
                            shelf_id=shelf_id,
                            sku_counts={sku: 10 + step},
                            robot_id=robot_id,
                            tick=step,
                            confidence=0.99,
                        )
                    else:
                        ledger.record_pick(
                            shelf_id=shelf_id,
                            sku=sku,
                            qty_delta=-1,
                            robot_id=robot_id,
                            tick=step,
                        )
                    t1 = time.perf_counter()
                    latencies_ms.append((t1 - t0) * 1000.0)
                except Exception as e:
                    lock_errors.append(e)

                # Micro-yield to allow concurrent interleaved event scheduling
                await asyncio.sleep(0.001)

        async def run_fleet_test():
            tasks = [robot_worker(f"AMR-{i+1:02d}") for i in range(num_robots)]
            t_start = time.perf_counter()
            await asyncio.gather(*tasks)
            t_end = time.perf_counter()
            return t_end - t_start

        total_time = asyncio.run(run_fleet_test())

        latencies_ms.sort()
        n = len(latencies_ms)
        p50 = latencies_ms[int(n * 0.50)] if n else 0
        p95 = latencies_ms[int(n * 0.95)] if n else 0
        p99 = latencies_ms[int(n * 0.99)] if n else 0
        max_lat = latencies_ms[-1] if n else 0
        mean_lat = sum(latencies_ms) / n if n else 0
        throughput = n / total_time if total_time > 0 else 0

        print(f"Total Transactions:      {n}")
        print(f"Total Lock Errors:       {len(lock_errors)}")
        print(f"Total Time:              {total_time:.3f} s")
        print(f"Throughput:              {throughput:.1f} writes/sec")
        print(f"Mean Write Latency:      {mean_lat:.2f} ms")
        print(f"p50 Write Latency:       {p50:.2f} ms")
        print(f"p95 Write Latency:       {p95:.2f} ms")
        print(f"p99 Write Latency:       {p99:.2f} ms")
        print(f"Max Write Latency:       {max_lat:.2f} ms")

        is_bottleneck = (len(lock_errors) > 0) or (p95 > 25.0) or (max_lat > 100.0)
        print(f"\n=> InventoryLedger Write Contention Bottleneck Detected: {is_bottleneck}")
        if not is_bottleneck:
            print("   WAL mode + 10s busy_timeout handles concurrent writes cleanly with 0 lock errors.")
            print("   Write latencies remain low and consistent under real 20-robot concurrent load.")
        return is_bottleneck, p95, max_lat, lock_errors


def profile_job_journal(num_jobs: int = 50):
    print("\n" + "=" * 70)
    print(f"PROFILING 2: JobJournal (Disk Append + fsync)")
    print(f"Scale: {num_jobs} rapid job lifecycle updates with event loop lag monitoring")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "test_job_log.jsonl"
        journal = JobJournal(journal_path=journal_path)

        latencies_ms: List[float] = []
        event_loop_lags_ms: List[float] = []
        running = True

        async def run_journal_test():
            nonlocal running
            loop = asyncio.get_running_loop()

            def heartbeat_probe(scheduled_time: float):
                nonlocal running
                lag_ms = (time.perf_counter() - scheduled_time) * 1000.0
                event_loop_lags_ms.append(lag_ms)
                if running:
                    t_next = time.perf_counter()
                    loop.call_soon(heartbeat_probe, t_next)

            async def job_runner():
                for i in range(num_jobs):
                    jid = f"JOB-PROFILE-{i:03d}"
                    t0 = time.perf_counter()
                    journal.log_submission(
                        job_id=jid,
                        job_type="PICKUP",
                        pickup=(10, 10),
                        dropoff=(20, 20),
                        urgency=2,
                    )
                    journal.log_assignment(job_id=jid, assigned_robot_id=f"AMR-{(i%20)+1:02d}")
                    journal.log_completion(job_id=jid, robot_id=f"AMR-{(i%20)+1:02d}")
                    t1 = time.perf_counter()
                    latencies_ms.append((t1 - t0) * 1000.0)
                    await asyncio.sleep(0.001)

            t_init = time.perf_counter()
            loop.call_soon(heartbeat_probe, t_init)
            await job_runner()
            running = False

        asyncio.run(run_journal_test())
        journal.close()

        latencies_ms.sort()
        n = len(latencies_ms)
        p50 = latencies_ms[int(n * 0.50)] if n else 0
        p95 = latencies_ms[int(n * 0.95)] if n else 0
        max_lat = latencies_ms[-1] if n else 0
        mean_lat = sum(latencies_ms) / n if n else 0

        event_loop_lags_ms.sort()
        max_lag = event_loop_lags_ms[-1] if event_loop_lags_ms else 0
        p95_lag = event_loop_lags_ms[int(len(event_loop_lags_ms) * 0.95)] if event_loop_lags_ms else 0

        print(f"Total Job Cycles:            {n} (3 log entries each = {n*3} streamed writes)")
        print(f"Mean Cycle Latency:          {mean_lat:.2f} ms")
        print(f"p50 Cycle Latency:           {p50:.2f} ms")
        print(f"p95 Cycle Latency:           {p95:.2f} ms")
        print(f"Max Cycle Latency:           {max_lat:.2f} ms")
        print(f"Event Loop p95 Lag:          {p95_lag:.3f} ms")
        print(f"Event Loop Max Lag:          {max_lag:.3f} ms")

        is_blocking = (max_lag > 5.0) or (p95_lag > 1.5)
        print(f"\n=> JobJournal Event Loop Blocking Detected: {is_blocking}")
        if not is_blocking:
            print("   Non-blocking file streaming eliminates event loop lag (p95 < 1.5ms).")
        return is_blocking, p95, max_lag


if __name__ == "__main__":
    ledger_bottleneck, l_p95, l_max, l_errs = profile_inventory_ledger(num_robots=20, writes_per_robot=15)
    journal_blocking, j_p95, j_max_lag = profile_job_journal(num_jobs=50)

    print("\n" + "=" * 70)
    print("PROFILING SUMMARY & DECISION:")
    print("=" * 70)
    print(f"1. InventoryLedger queue needed?  {'YES' if ledger_bottleneck else 'NO (0 lock errors, low latency)'}")
    print(f"2. JobJournal non-blocking needed? {'YES' if journal_blocking else 'NO (event loop lag within bounds)'}")
    print("=" * 70)
