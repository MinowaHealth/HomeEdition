"""Livetest package — make the webapp's db_driver shim importable.

Submodules use ``import db_driver`` to share the same psycopg3 driver
wiring as the webapp; the shim itself lives at
``UserApp/webapp/db_driver.py``. Adjusting ``sys.path`` here means
individual submodules don't each have to repeat the path manipulation.
"""
import os
import sys

_WEBAPP_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp")
)
if _WEBAPP_DIR not in sys.path:
    sys.path.insert(0, _WEBAPP_DIR)
