"""Async HTTP client for DesignMCP -> UserApp.

A single client is shared across the process. The Authorization header is
set once from USERAPP_API_KEY (a long-lived hbk_* token bound to the
rodrigo@borgia.family demo user) and never changes. There is no per-request
auth — DesignMCP exposes a fixed identity to the Claude Design tool.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class UserAppClient:
    """Thin async wrapper around httpx.AsyncClient with the demo API key baked in."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            timeout=timeout,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        headers: dict | None = None,
    ) -> dict:
        """Forward a request to UserApp and return a structured envelope.

        Returns a dict with status_code, headers, and either json (parsed body)
        or text (raw text if non-JSON). This is the contract DesignMCP
        exposes to the Design tool — no transformation, just the wire shape.
        """
        try:
            response = await self._client.request(
                method=method.upper(),
                url=path,
                params=params,
                json=json,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("UserApp request failed: %s %s -> %s", method, path, exc)
            return {
                'status_code': 0,
                'error': f'{type(exc).__name__}: {exc}',
                'json': None,
                'headers': {},
            }

        body: dict[str, Any] = {
            'status_code': response.status_code,
            'headers': dict(response.headers),
        }
        if not response.text:
            body['json'] = None
            return body

        try:
            body['json'] = response.json()
        except ValueError:
            body['json'] = None
            body['text'] = response.text[:50000]
        return body

    async def close(self) -> None:
        await self._client.aclose()
