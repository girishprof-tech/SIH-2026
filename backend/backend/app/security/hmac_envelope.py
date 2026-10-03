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
from cryptography.hazmat.primitives.asymmetric import ed25519

import os

DEFAULT_SECRET_KEY = os.environ.get("MASTER_SECURITY_KEY", "sih2026-edge-robot-shared-secret")


def get_node_keypair(node_id: str, master_secret: Optional[str] = None) -> Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Deterministically derives an Ed25519 private/public keypair for a known station or robot node."""
    secret = master_secret or os.environ.get("MASTER_SECURITY_KEY", DEFAULT_SECRET_KEY)
    seed = hashlib.sha256(f"{secret}:{node_id}".encode("utf-8")).digest()
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    return private_key, private_key.public_key()


def get_node_public_key(node_id: str) -> ed25519.Ed25519PublicKey:
    """Returns the registered Ed25519 public key for a given node identity."""
    return get_node_keypair(node_id)[1]


def get_node_private_key(node_id: str) -> ed25519.Ed25519PrivateKey:
    """Returns the registered Ed25519 private key for a given node identity."""
    return get_node_keypair(node_id)[0]


KNOWN_NODE_IDS = [f"AMR-{i:02d}" for i in range(1, 21)] + [
    "IMPORT_STATION",
    "EXPORT_STATION",
    "AUTHORITY_STATION",
    "DISPATCHER",
    "DASHBOARD",
]

NODE_PUBLIC_KEYS: Dict[str, str] = {
    nid: get_node_public_key(nid).public_bytes_raw().hex() for nid in KNOWN_NODE_IDS
}


def canonical_json_bytes(data: Any) -> bytes:
    """Serializes data to canonical JSON bytes with sorted keys and compact separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(
    payload: Dict[str, Any],
    secret_key: Optional[str] = None,
    seq: Optional[int] = None,
    timestamp: Optional[float] = None,
    sender_id: Optional[str] = None,
    use_asymmetric: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Wraps a payload into a signed envelope containing signature, timestamp, sender_id,
    and optional sequence number.

    Uses asymmetric Ed25519 signing when node identity is available or use_asymmetric=True.
    Falls back to legacy symmetric HMAC-SHA256 if use_asymmetric=False or when a custom
    secret_key != DEFAULT_SECRET_KEY is explicitly specified.
    """
    ts = timestamp if timestamp is not None else time.time()
    sender = (
        sender_id
        or payload.get("station_id")
        or payload.get("robot_id")
        or payload.get("source_robot_id")
        or payload.get("sender_id")
        or payload.get("bidder_id")
        or payload.get("winner_id")
    )

    should_use_ed25519 = False
    if use_asymmetric is True:
        should_use_ed25519 = True
    elif use_asymmetric is False:
        should_use_ed25519 = False
    elif sender is not None and (secret_key is None or secret_key == DEFAULT_SECRET_KEY):
        should_use_ed25519 = True

    envelope_body: Dict[str, Any] = {
        "payload": payload,
        "timestamp": ts,
    }
    if sender is not None:
        envelope_body["sender_id"] = str(sender)
    if seq is not None:
        envelope_body["seq"] = seq

    canonical_bytes = canonical_json_bytes(envelope_body)

    if should_use_ed25519 and sender is not None:
        private_key = get_node_private_key(str(sender))
        signature = private_key.sign(canonical_bytes).hex()
        return {
            "body": envelope_body,
            "signature": signature,
            "algorithm": "ed25519",
        }

    # Legacy HMAC-SHA256
    key_to_use = secret_key if secret_key is not None else DEFAULT_SECRET_KEY
    signature = hmac.new(key_to_use.encode("utf-8"), canonical_bytes, hashlib.sha256).hexdigest()
    return {
        "body": envelope_body,
        "signature": signature,
        "algorithm": "hmac-sha256",
    }


def verify_envelope(
    envelope: Dict[str, Any],
    secret_key: Optional[str] = None,
    expected_sender: Optional[str] = None,
    enforce_asymmetric: bool = False,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Verifies the signature of an envelope using constant-time comparison or Ed25519 public key.
    Returns: (is_valid, payload, error_message)
    """
    if not isinstance(envelope, dict):
        return False, None, "Invalid envelope format: must be dict"

    body = envelope.get("body")
    signature = envelope.get("signature")
    algorithm = envelope.get("algorithm")

    if not isinstance(body, dict) or not isinstance(signature, str):
        return False, None, "Missing body or signature in envelope"

    canonical_bytes = canonical_json_bytes(body)

    # 1. Asymmetric Ed25519 Verification
    if algorithm == "ed25519":
        payload = body.get("payload")
        if not isinstance(payload, dict):
            return False, None, "Envelope body missing valid payload dict"

        sender = (
            expected_sender
            or body.get("sender_id")
            or payload.get("station_id")
            or payload.get("robot_id")
            or payload.get("source_robot_id")
            or payload.get("sender_id")
        )
        if not sender:
            return False, None, "Missing sender_id in envelope for Ed25519 verification"

        try:
            public_key = get_node_public_key(str(sender))
            public_key.verify(bytes.fromhex(signature), canonical_bytes)
        except Exception as e:
            return False, None, f"Ed25519 signature mismatch (tampered payload) for sender '{sender}': {e}"

        return True, payload, ""

    # 2. Symmetric HMAC-SHA256 Verification
    if enforce_asymmetric:
        return False, None, "Asymmetric Ed25519 signature required: symmetric HMAC envelope rejected"

    key_to_use = secret_key if secret_key is not None else DEFAULT_SECRET_KEY
    expected_sig = hmac.new(key_to_use.encode("utf-8"), canonical_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return False, None, "HMAC signature mismatch (tampered payload)"

    payload = body.get("payload")
    if not isinstance(payload, dict):
        return False, None, "Envelope body missing valid payload dict"

    return True, payload, ""


def build_inventory_update_envelope(
    shelf_id: str,
    x: int,
    y: int,
    sku_manifest: Dict[str, int],
    current_box_count: int,
    confidence: float,
    tick: int,
    source_robot_id: str,
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
    channel: str = "MESH",
    version: int = 1,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for INVENTORY_UPDATE."""
    payload = {
        "type": "INVENTORY_UPDATE",
        "shelf_id": shelf_id,
        "x": x,
        "y": y,
        "sku_manifest": sku_manifest,
        "current_box_count": current_box_count,
        "confidence": round(confidence, 4),
        "tick": tick,
        "source_robot_id": source_robot_id,
        "channel": channel,
        "version": version,
    }
    return sign_payload(payload, secret_key=secret_key, seq=seq)


def build_resource_claim_envelope(
    resource_type: str,
    resource_id: Any,
    robot_id: str,
    tick: int,
    lease_ticks: int = 40,
    priority_score: float = 0.0,
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for RESOURCE_CLAIM."""
    payload = {
        "type": "RESOURCE_CLAIM",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "robot_id": robot_id,
        "tick": tick,
        "lease_ticks": lease_ticks,
        "priority_score": round(priority_score, 2),
    }
    return sign_payload(payload, secret_key=secret_key, seq=seq)


def build_resource_release_envelope(
    resource_type: str,
    resource_id: Any,
    robot_id: str,
    tick: int,
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for RESOURCE_RELEASE."""
    payload = {
        "type": "RESOURCE_RELEASE",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "robot_id": robot_id,
        "tick": tick,
    }
    return sign_payload(payload, secret_key=secret_key, seq=seq)


def build_pod_occupancy_envelope(
    pos: Tuple[int, int],
    occupant: Optional[str],
    shelf_id: Optional[str] = None,
    tick: int = 0,
    source_robot_id: str = "UNKNOWN",
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for POD_SLOT_OCCUPANCY."""
    payload = {
        "type": "POD_SLOT_OCCUPANCY",
        "pos": [int(pos[0]), int(pos[1])],
        "occupant": occupant,
        "shelf_id": shelf_id,
        "tick": tick,
        "source_robot_id": source_robot_id,
    }
    return sign_payload(payload, secret_key=secret_key, seq=seq)


def build_station_command_envelope(
    command_type: str,
    station_id: str,
    station_role: str,
    task_dict: Dict[str, Any],
    tick: int = 0,
    target_robot_id: Optional[str] = None,
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for station commands with station_role claim."""
    payload = {
        "type": command_type,
        "station_id": station_id,
        "station_role": station_role,
        "task": task_dict,
        "tick": tick,
        "target_robot_id": target_robot_id,
    }
    return sign_payload(payload, secret_key=secret_key, seq=seq)


