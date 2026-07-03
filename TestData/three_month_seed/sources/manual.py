"""REST POST adapter for the manual-input source.

Per-persona bearer-token cache: login once per persona at first use, cache
token for the run. Uses httpx per CLAUDE.md direction-of-travel.

Adapter responsibility: translate the seeder's internal vocabulary
(measured_at, value, text, observed_at, kind) into each route's actual
request shape (timestamp, weight, observation, source_type, ...). Routes
predate the seeder spec and use a different field vocabulary that mobile
clients depend on; the boundary is here.
"""
from __future__ import annotations
import time

import httpx


class ManualClient:
    def __init__(
        self, base_url: str, timeout: float = 30.0, post_throttle: float = 0.0
    ):
        """`post_throttle` (seconds) is slept before each authenticated POST.
        UserApp's local_limiter defaults to 120/min/IP — all activity calls
        from the seeder share that budget. 0.55s/call → ~109/min, just
        under. Tests set 0 to skip sleeping."""
        self.base_url = base_url.rstrip("/")
        # Disable HTTP keep-alive. UserApp runs gunicorn sync workers, whose
        # ~2s keep-alive window races with the seeder's inter-request gaps: a
        # pooled connection the worker has already closed gets reused and the
        # request fails with "Server disconnected without sending a response".
        # A fresh connection per request removes the race; the cost is
        # negligible for a seed run.
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self._token_cache: dict[str, str] = {}
        self.post_throttle = post_throttle

    def token_for(self, email: str, password: str) -> str:
        """Login as `email` and cache the bearer token. Subsequent calls
        hit the cache."""
        if email in self._token_cache:
            return self._token_cache[email]
        resp = self._http.post("/login", json={"email": email, "password": password})
        resp.raise_for_status()
        token = resp.json()["token"]
        self._token_cache[email] = token
        return token

    # ---- POST helpers, one per route ----
    def _post_json(self, email: str, path: str, body: dict) -> dict:
        """POST authenticated JSON, raise on 4xx/5xx, return response body
        as dict. Caller should not assume any specific field (routes return
        a mix of {'id': ...} and {'message': ...}).

        Sleeps `post_throttle` seconds first to stay under the IP-keyed
        local rate limit (120/min default on UserApp).

        On 429, retries with exponential backoff (15s, 30s, 60s) — the
        post_throttle margin can be exceeded by bursty per-day events, so
        we re-arm rather than fail the entire run."""
        if self.post_throttle > 0:
            time.sleep(self.post_throttle)
        token = self._token_cache[email]
        headers = {"Authorization": f"Bearer {token}"}
        backoffs = (15, 30, 60)
        for delay in backoffs + (0,):   # final attempt: no further sleep
            resp = self._http.post(path, json=body, headers=headers)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()
            if delay:
                time.sleep(delay)
        # All retries exhausted on 429 — let raise_for_status surface it.
        resp.raise_for_status()
        return resp.json()

    def post_condition(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/conditions", body)

    def post_allergy(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/allergies", body)

    def post_family_history(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/family-history", body)

    def post_social_history(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/social-history", body)

    def post_surgical_history(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/surgical-history", body)

    def post_health_input(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/health-inputs", body)

    def post_timeframe(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/timeframes", body)

    def post_stack(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/stacks", body)

    def post_vaccination(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/vaccinations", body)

    def post_blood_pressure(self, email: str, body: dict) -> dict:
        """Translate {measured_at, systolic, diastolic, [heart_rate]} →
        route {timestamp, systolic, diastolic, [heart_rate]}."""
        route_body = {
            "timestamp": body["measured_at"],
            "systolic": body["systolic"],
            "diastolic": body["diastolic"],
        }
        if "heart_rate" in body:
            route_body["heart_rate"] = body["heart_rate"]
        return self._post_json(email, "/api/v1/blood-pressure", route_body)

    def post_weight(self, email: str, body: dict) -> dict:
        """Translate {measured_at, value} → route {timestamp, weight, unit}."""
        return self._post_json(email, "/api/v1/weight", {
            "timestamp": body["measured_at"],
            "weight": body["value"],
            "unit": body.get("unit", "lbs"),
        })

    def post_food_item(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/food-items", body)

    def post_meal(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/meals", body)

    def post_log_stack(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/log-stack", body)

    def post_log_meal(self, email: str, body: dict) -> dict:
        return self._post_json(email, "/api/v1/log-meal", body)

    def post_observation(self, email: str, body: dict) -> dict:
        """Translate {kind, text, observed_at} → route
        {observation, timestamp, source_type}."""
        return self._post_json(email, "/api/v1/observations", {
            "observation": body["text"],
            "timestamp": body["observed_at"],
            "source_type": body.get("kind", "text"),
        })

    def close(self) -> None:
        self._http.close()
