"""Minimal logging configuration for DesignMCP.

JSON to stdout when running in Docker (detected via /.dockerenv), human-
readable otherwise. Level taken from UVICORN_LOG_LEVEL (default info) so the
service follows the same env-var conventions as the other Python services in
this repo.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str) -> None:
    level = os.getenv("UVICORN_LOG_LEVEL", "info").upper()
    if level == "TRACE":
        level = "DEBUG"

    handler = logging.StreamHandler(sys.stdout)
    if Path("/.dockerenv").exists():
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            f"%(asctime)s [{service_name}] %(levelname)s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        ))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
