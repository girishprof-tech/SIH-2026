"""
test_transport.py — Unit tests for the pluggable Transport layer.
Verifies packet loss, duplicates, offline peers, and loopback delivery.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from app.transport.udp_transport import UdpTransport


def test_loopback_normal_delivery():
    """Verifies standard clean message delivery between two nodes."""
    hub = LoopbackNetworkHub()
    t1 = LoopbackTransport("AMR-01", hub=hub)
    t2 = LoopbackTransport("AMR-02", hub=hub)

    payload = {"type": "TEST", "data": 42}
    t1.send("AMR-02", payload)

    received = t2.recv_all()
    assert len(received) == 1
    assert received[0] == payload
    # Drained
    assert t2.recv_all() == []


def test_packet_loss_simulation():
    """Verifies that 100% packet loss drops all messages, and 0% drops none."""
    hub = LoopbackNetworkHub()
    t1 = LoopbackTransport("AMR-01", hub=hub, packet_loss_pct=100.0)
    t2 = LoopbackTransport("AMR-02", hub=hub)

    for i in range(10):
        t1.send("AMR-02", {"msg_id": i})

    assert t2.recv_all() == []

    # Reset packet loss
    t1.set_packet_loss(0.0)
    t1.send("AMR-02", {"msg_id": "hello"})
    assert len(t2.recv_all()) == 1


def test_duplicate_message_injection():
    """Verifies duplicate message injection rate."""
    hub = LoopbackNetworkHub()
    t1 = LoopbackTransport("AMR-01", hub=hub, duplicate_pct=100.0)
    t2 = LoopbackTransport("AMR-02", hub=hub)

    t1.send("AMR-02", {"type": "HEARTBEAT"})
    received = t2.recv_all()
    assert len(received) == 2
    assert received[0] == received[1]


def test_offline_peer_simulation():
    """Verifies that offline nodes do not receive messages."""
    hub = LoopbackNetworkHub()
    t1 = LoopbackTransport("AMR-01", hub=hub)
    t2 = LoopbackTransport("AMR-02", hub=hub)

    # Take AMR-02 offline
    t2.set_offline(True)
    t1.send("AMR-02", {"important": True})
    assert t2.recv_all() == []

    # Bring AMR-02 back online
    t2.set_offline(False)
    t1.send("AMR-02", {"reconnected": True})
    received = t2.recv_all()
    assert len(received) == 1
    assert received[0]["reconnected"] is True


def test_udp_transport_instantiation_and_close():
    """Verifies UdpTransport sets up non-blocking socket and closes cleanly."""
    u1 = UdpTransport(node_id="AMR-TEST-01", port=9991, peer_ports={"AMR-TEST-02": 9992})
    u2 = UdpTransport(node_id="AMR-TEST-02", port=9992, peer_ports={"AMR-TEST-01": 9991})

    u1.send("AMR-TEST-02", {"hello": "world"})
    # Give OS a millisecond
    import time
    time.sleep(0.01)
    msgs = u2.recv_all()
    assert len(msgs) == 1
    assert msgs[0]["hello"] == "world"

    u1.close()
    u2.close()
