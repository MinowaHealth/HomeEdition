"""
View-link absolutizer.

UserApp emits session-gated relative links ({web, download}); the SPA is
same-origin so it uses them as-is, but MCP consumers need absolute URLs the
user can open in a logged-in browser. APP_BASE_URL points at the web app
origin — on this appliance that's the LAN address of the box (set via
APP_BASE_URL in local.env / compose); the loopback default suits local dev.
"""
from __future__ import annotations

import os
from typing import Any

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost").rstrip("/")


def absolutize_links(links: Any) -> Any:
    """Prefix relative link paths with APP_BASE_URL. Non-dicts pass through."""
    if not isinstance(links, dict):
        return links
    return {
        k: (APP_BASE_URL + v) if isinstance(v, str) and v.startswith("/") else v
        for k, v in links.items()
    }
