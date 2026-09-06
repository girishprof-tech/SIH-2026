"""
hmac_envelope.py — Lightweight HMAC-SHA256 Message Security.

Signs outgoing peer messages and verifies incoming messages with constant-time comparison.
Uses Python standard library `hmac` and `hashlib`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional, Tuple

DEFAULT_SECRET_KEY = "sih2026-edge-robot-shared-secret"


def canonical_json_bytes(data: Any) -> bytes:
    """Serializes data to canonical JSON bytes with sorted keys and compact separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(
    payload: Dict[str, Any],
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
    timestamp: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Wraps a payload into a signed envelope containing signature, timestamp, and optional sequence number.
    """
    ts = timestamp if timestamp is not None else time.time()
    envelope_body = {
        "payload": payload,
        "timestamp": ts,
    }
    if seq is not None:
        envelope_body["seq"] = seq

    canonical_bytes = canonical_json_bytes(envelope_body)
    signature = hmac.new(secret_key.encode("utf-8"), canonical_bytes, hashlib.sha256).hexdigest()

    return {
        "body": envelope_body,
        "signature": signature,
    }


def verify_envelope(
    envelope: Dict[str, Any],
    secret_key: str = DEFAULT_SECRET_KEY,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Verifies the HMAC signature of an envelope using constant-time comparison.
    Returns: (is_valid, payload, error_message)
    """
    if not isinstance(envelope, dict):
        return False, None, "Invalid envelope format: must be dict"

    body = envelope.get("body")
    signature = envelope.get("signature")

    if not isinstance(body, dict) or not isinstance(signature, str):
        return False, None, "Missing body or signature in envelope"

    canonical_bytes = canonical_json_bytes(body)
    expected_sig = hmac.new(secret_key.encode("utf-8"), canonical_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return False, None, "HMAC signature mismatch (tampered payload)"

    payload = body.get("payload")
    if not isinstance(payload, dict):
        return False, None, "Envelope body missing valid payload dict"

    return True, payload, ""
