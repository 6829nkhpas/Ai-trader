"""Per-run request context shared across the deep-quant modules.

Holds the authenticated user id for the current run in a context variable so
tools (e.g. ``watch_price_condition`` in tools.py) can stamp it onto the
tool-server request WITHOUT a circular import between ``graph.py`` and
``tools.py``. Set once per request by ``main.py``'s ``event_generator``.

Concurrency: a ``ContextVar`` is per-async-task, so overlapping runs each see
their own user id (LangChain propagates the context into its executor threads).
"""

from contextvars import ContextVar
from typing import Optional

_run_user_id: ContextVar = ContextVar("_run_user_id", default=None)


def set_run_user_id(user_id: Optional[str]) -> None:
    """Bind the requesting user's id for the current run (or clear it)."""
    _run_user_id.set(user_id if (user_id and str(user_id).strip()) else None)


def get_run_user_id() -> Optional[str]:
    """Return the current run's user id, or None when unset."""
    return _run_user_id.get()
