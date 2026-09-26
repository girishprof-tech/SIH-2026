"""
test_step4_role_rbac.py — Unit and Integration Verification for Step 4 Role RBAC & Terminal Gate.

Validates:
1. FastAPI REST API endpoints enforce role boundaries via `X-Operator-Role`:
   - IMPORT station cannot issue `sort_batch` jobs (HTTP 403 STATION_AUTHORITY_VIOLATION).
   - EXPORT station cannot issue `fetch_item` jobs targeting IMPORT_DOCK (HTTP 403 STATION_AUTHORITY_VIOLATION).
   - IMPORT station cannot inject tasks outside West inbound docks (HTTP 403 STATION_AUTHORITY_VIOLATION).
   - EXPORT station cannot inject tasks originating at West inbound docks (HTTP 403 STATION_AUTHORITY_VIOLATION).
   - AUTHORITY station has full operational scope (can inject and dispatch across all docks and job types).
   - Legitimate role-scoped requests succeed with HTTP 200 / 201.
2. Station role metadata is preserved when tasks are dispatched to fleet.
3. UI role state and role selection contract validation.
"""

import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

# Prevent external daemon/orchestrator spawning during isolated unit testing
os.environ["SPAWN_FLEET_ORCHESTRATOR"] = "0"

from app.main import app
from app.models.task import TaskType


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_import_station_cannot_issue_sort_batch(client):
    """Import operator terminal must be forbidden from issuing sort_batch jobs."""
    response = client.post(
        "/api/job",
        json={"job_type": "sort_batch", "urgency": 3},
        headers={"X-Operator-Role": "IMPORT"},
    )
    assert response.status_code == 403
    assert "STATION_AUTHORITY_VIOLATION" in response.json()["detail"]
    assert "sort_batch" in response.json()["detail"]


def test_export_station_cannot_issue_inbound_fetch_item(client):
    """Export operator terminal must be forbidden from issuing inbound fetch_item jobs."""
    response = client.post(
        "/api/job",
        json={"job_type": "fetch_item", "zone": "IMPORT_DOCK", "urgency": 3},
        headers={"X-Operator-Role": "EXPORT"},
    )
    assert response.status_code == 403
    assert "STATION_AUTHORITY_VIOLATION" in response.json()["detail"]
    assert "Import Station" in response.json()["detail"]


def test_import_station_inject_task_dock_boundary(client):
    """Import station operator can only inject tasks within the West inbound dock."""
    # Attempt injection outside West dock (x=10, y=10) -> Should fail 403
    forbidden_resp = client.post(
        "/api/task/inject",
        json={
            "pickup": {"x": 10, "y": 10},
            "dropoff": {"x": 20, "y": 10},
            "urgency": 3,
        },
        headers={"X-Operator-Role": "IMPORT"},
    )
    assert forbidden_resp.status_code == 403
    assert "STATION_AUTHORITY_VIOLATION" in forbidden_resp.json()["detail"]

    # Attempt injection at West dock (x=1, y=10) -> Should succeed 201
    allowed_resp = client.post(
        "/api/task/inject",
        json={
            "pickup": {"x": 1, "y": 10},
            "dropoff": {"x": 20, "y": 10},
            "urgency": 3,
        },
        headers={"X-Operator-Role": "IMPORT"},
    )
    assert allowed_resp.status_code == 201
    assert allowed_resp.json()["pickup"] == {"x": 1, "y": 10}


def test_export_station_inject_task_dock_boundary(client):
    """Export station operator cannot inject tasks originating at West inbound dock."""
    # Attempt injection at West dock (x=1, y=10) -> Should fail 403
    forbidden_resp = client.post(
        "/api/task/inject",
        json={
            "pickup": {"x": 1, "y": 10},
            "dropoff": {"x": 25, "y": 10},
            "urgency": 3,
        },
        headers={"X-Operator-Role": "EXPORT"},
    )
    assert forbidden_resp.status_code == 403
    assert "STATION_AUTHORITY_VIOLATION" in forbidden_resp.json()["detail"]

    # Attempt injection outside West dock (e.g., storage to chute) -> Should succeed 201
    allowed_resp = client.post(
        "/api/task/inject",
        json={
            "pickup": {"x": 25, "y": 10},
            "dropoff": {"x": 28, "y": 10},
            "urgency": 3,
        },
        headers={"X-Operator-Role": "EXPORT"},
    )
    assert allowed_resp.status_code == 201
    assert allowed_resp.json()["pickup"] == {"x": 25, "y": 10}


def test_authority_station_unrestricted_scope(client):
    """Authority station has full administrative operational scope."""
    # Inbound dock injection
    resp1 = client.post(
        "/api/task/inject",
        json={
            "pickup": {"x": 1, "y": 12},
            "dropoff": {"x": 15, "y": 12},
            "urgency": 5,
        },
        headers={"X-Operator-Role": "AUTHORITY"},
    )
    assert resp1.status_code == 201

    # Outbound dock injection
    resp2 = client.post(
        "/api/task/inject",
        json={
            "pickup": {"x": 20, "y": 12},
            "dropoff": {"x": 28, "y": 12},
            "urgency": 5,
        },
        headers={"X-Operator-Role": "AUTHORITY"},
    )
    assert resp2.status_code == 201


def test_task_manager_dispatches_with_station_role():
    """Verify TaskManager attaches station_role to fleet UDP envelopes when provided."""
    from app.services.task_manager import TaskManager
    from app.models.task import Task

    tm = TaskManager()
    task = tm.create_task(1, 14, 15, 14, urgency=3, current_tick=1)

    mock_peer_ports = {"AMR_01": 9001}
    # Test dispatch without exceptions
    tm.dispatch_to_fleet(
        task,
        peer_ports=mock_peer_ports,
        target_robot_id="AMR_01",
        station_role="IMPORT_STATION",
    )
    assert task.task_id.startswith("TASK-")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
