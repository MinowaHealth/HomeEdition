"""Fire-and-forget background helper for non-critical work.

Usage:
    from background import fire_and_forget

    fire_and_forget(some_slow_task, arg1, arg2)
"""

import logging
import threading

logger = logging.getLogger(__name__)


def fire_and_forget(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) in a daemon thread.

    Logs exceptions but never blocks the caller.  Daemon threads die
    automatically when the Gunicorn worker process recycles.
    """
    def _wrapper():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("fire_and_forget failed: %s", fn.__name__)

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
