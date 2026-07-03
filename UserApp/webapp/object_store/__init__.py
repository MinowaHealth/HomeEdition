"""ObjectStore factory.

Home Edition ships only the local-filesystem backend; documents stay on
the appliance's disk.

Usage:
    from object_store import get_object_store
    store = get_object_store()
"""
from functools import lru_cache

from object_store.local import LocalStore


@lru_cache(maxsize=1)
def get_object_store() -> LocalStore:
    """Return the local ObjectStore singleton."""
    return LocalStore()
