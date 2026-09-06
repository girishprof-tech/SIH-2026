"""
test_security.py — Unit tests for lightweight HMAC-SHA256 message security and ReplayGuard.
"""

from __future__ import annotations

import time
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.security.hmac_envelope import sign_payload, verify_envelope
from app.security.replay_guard import ReplayGuard


def test_valid_signature_verification():
    """Verifies that untampered signed envelope is accepted."""
    secret = "test-secret-key-123"
    payload = {"robot_id": "AMR-01", "tick": 5, "position": [10, 6]}

    envelope = sign_payload(payload, secret_key=secret, seq=1)
    is_valid, extracted_payload, err = verify_envelope(envelope, secret_key=secret)

    assert is_valid is True
    assert extracted_payload == payload
    assert err == ""


def test_tampered_payload_rejected():
    """Verifies that any modification to payload invalidates signature."""
    secret = "test-secret-key-123"
    payload = {"robot_id": "AMR-01", "priority_score": 100.0}

    envelope = sign_payload(payload, secret_key=secret)
    # Tamper with priority score in payload
    envelope["body"]["payload"]["priority_score"] = 9999.0

    is_valid, extracted_payload, err = verify_envelope(envelope, secret_key=secret)
    assert is_valid is False
    assert extracted_payload is None
    assert "mismatch" in err.lower()


def test_wrong_secret_key_rejected():
    """Verifies that an envelope signed with a different key fails verification."""
    envelope = sign_payload({"data": 1}, secret_key="key-A")
    is_valid, extracted_payload, err = verify_envelope(envelope, secret_key="key-B")
    assert is_valid is False
    assert extracted_payload is None


def test_replay_guard_sequence_check():
    """Verifies that ReplayGuard rejects duplicate or decreasing sequence numbers."""
    guard = ReplayGuard(freshness_window_s=5.0)
    now = time.time()

    # Seq 1 -> accepted
    valid, _ = guard.validate("AMR-01", seq=1, timestamp=now, current_time=now)
    assert valid is True

    # Seq 2 -> accepted
    valid, _ = guard.validate("AMR-01", seq=2, timestamp=now, current_time=now)
    assert valid is True

    # Replay of Seq 2 -> rejected
    valid, reason = guard.validate("AMR-01", seq=2, timestamp=now, current_time=now)
    assert valid is False
    assert "replay" in reason.lower()

    # Out of order Seq 1 -> rejected
    valid, reason = guard.validate("AMR-01", seq=1, timestamp=now, current_time=now)
    assert valid is False
    assert "replay" in reason.lower()


def test_replay_guard_freshness_window():
    """Verifies that messages older than freshness window are rejected."""
    guard = ReplayGuard(freshness_window_s=5.0)
    now = 1000.0

    # Message from 2 seconds ago -> accepted
    valid, _ = guard.validate("AMR-01", seq=1, timestamp=now - 2.0, current_time=now)
    assert valid is True

    # Message from 10 seconds ago -> rejected
    valid, reason = guard.validate("AMR-01", seq=2, timestamp=now - 10.0, current_time=now)
    assert valid is False
    assert "expired" in reason.lower()
