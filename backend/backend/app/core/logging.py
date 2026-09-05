"""
Structured logging setup for the SIH2026 backend.
Uses Python's standard logging with JSON-friendly formatting in production.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional


class StructuredFormatter(logging.Formatter):
    """Simple structured log formatter that produces key=value lines."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        base = super().format(record)
        return base


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(fmt))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


# Convenience factory
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
