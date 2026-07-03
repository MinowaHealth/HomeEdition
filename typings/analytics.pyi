"""Local stub override for the `analytics` module.

The repo has its own `UserApp/webapp/analytics.py` — a thin local-logging
wrapper with `capture()` and `identify()` — but pyright's bundled
typeshed ships a stub for Segment.io's `analytics-python` PyPI
package under the same module name. Typeshed stubs outrank source
files in extraPaths, so `import analytics` was resolving to the
Segment stub (which doesn't expose `capture`), producing ~22
false-positive reportAttributeAccessIssue warnings repo-wide.

Segment's analytics-python is not installed and is not used here.
This stub points pyright at the real local module's surface.
"""

from typing import Any


def capture(event: str, properties: dict[str, Any] | None = None) -> None: ...
def identify(user: dict[str, Any]) -> None: ...
