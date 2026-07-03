"""Product-event logging. Home Edition: events go to the local app log.

The enterprise build sent these to a cloud analytics service; the Home
Edition appliance has no cloud analytics. The call-site API (capture/identify)
is kept so route code is identical in both builds.
"""
from flask import g, current_app


def _get_distinct_id() -> str | None:
    """Extract user ID from g.user, handling both key conventions.

    authenticate_user() returns {'id': uuid}, but get_session() and
    token auth return {'user_id': uuid}. Handle both.
    """
    user = getattr(g, 'user', None)
    if not user:
        return None
    uid = user.get('user_id') or user.get('id')
    return str(uid) if uid else None


def capture(event: str, properties: dict | None = None):
    """Log a product event for the current request's user."""
    current_app.logger.debug(
        "analytics_event event=%s user=%s properties=%s",
        event, _get_distinct_id(), properties or {},
    )


def identify(user: dict):
    """No-op in Home Edition (no analytics profile store)."""
