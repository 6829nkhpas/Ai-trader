"""State_Path_Report — is this deployment's durable state actually durable?

Why this module exists
---------------------
Every SQLite database this service owns used to be written beside the module, i.e.
into a container layer. ``docker-compose.prod.yml`` declared **no volume** for
``deep-quant``, so every redeploy destroyed all of them — including
``compliance.db``, whose append-only hash chain therefore restarted from genesis
each time. Nothing in the logs said so. A five-year retention obligation was being
missed silently, and the only outward sign was that the chain looked short.

The fix is the volume (``deep_quant_data:/data``) plus the four ``*_DB_PATH``
environment variables. But a mount is exactly the kind of configuration that is
easy to forget on a new host and impossible to notice afterwards, because a fresh
empty database is indistinguishable from a working one. So the mount is *reported*
at startup and a path outside the durable set is a loud WARNING.

Design
------
Pure. This module does no path resolution of its own, deliberately: the owning
modules already resolve their paths (``hashchain.db_path()`` reads
``COMPLIANCE_DB_PATH`` per call, ``journal.JOURNAL_DB_PATH`` captures its env at
import, telemetry resolves through its config). Re-deriving them here would create
a second resolution that can drift from the real one, and a report that lies about
where the data is would be worse than no report. The caller passes the paths it
actually uses; this module only classifies and formats them.

The only environment variable read here is ``DEEP_QUANT_STATE_DIRS`` — the set of
directories this deployment considers durable.
"""

from __future__ import annotations

import os
from typing import Callable, Iterable, List, NamedTuple, Optional, Sequence, Tuple

# NOTE ON PUNCTUATION: every emitted line is plain ASCII. These are read through
# `docker compose logs`, and an em-dash arrives mangled on a console using a
# non-UTF-8 code page (measured: it renders as `ù`). A log line whose separator is
# garbage is a line an operator skims past, so the nicer typography is not worth it.

# Directories treated as durable when DEEP_QUANT_STATE_DIRS is unset.
#
# `/data` is where docker-compose.prod.yml mounts the deep_quant_data volume. A
# local checkout has no volume and no mount, which is correct and must NOT warn on
# every developer's console — see `report_state_paths(local=...)`.
DEFAULT_STATE_DIRS: Tuple[str, ...] = ("/data",)

WARN_NOT_DURABLE = "is not under a durable state directory: data will be LOST on redeploy"


class StateEntry(NamedTuple):
    """One database the service depends on.

    ``label`` is what an operator reading the log needs to see ("compliance (P2/P5)"),
    not the variable name. ``critical`` marks state whose loss is a compliance or
    data-integrity event rather than an inconvenience — it selects ERROR-grade
    wording, because "the audit chain is ephemeral" and "the telemetry file is
    ephemeral" should not read identically.
    """

    label: str
    path: Optional[str]
    critical: bool = False


def state_dirs(env: Optional[dict] = None) -> Tuple[str, ...]:
    """The directories this deployment considers durable.

    Read per call, not captured at import, so a test (and an operator debugging a
    live container) can change it without reimporting the module. Empty or
    whitespace-only entries are dropped rather than treated as ``""``, which would
    match every path and make the whole report vacuously green.
    """
    source = os.environ if env is None else env
    raw = (source.get("DEEP_QUANT_STATE_DIRS") or "").strip()
    if not raw:
        return DEFAULT_STATE_DIRS
    parts = tuple(p.strip() for p in raw.split(os.pathsep) if p.strip())
    return parts or DEFAULT_STATE_DIRS


def ensure_parent_dir(path: str) -> None:
    """Create the directory containing ``path`` if it is missing.

    Shared by every store that opens a file under the state directory. Without it,
    a path whose parent does not exist yet fails with a bare
    ``sqlite3.OperationalError: unable to open database file`` — which reads like a
    permissions or corruption problem and sends whoever is debugging it to entirely the
    wrong place. Measured while wiring the session store: exactly that error, for a
    missing directory.

    Lives here rather than in each store because this module is already the one that
    knows about state file locations, and three copies of ``os.makedirs`` would drift.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _normalise(path: str) -> str:
    """Absolute, symlink-free-ish, separator-normalised form for containment tests.

    ``normpath`` collapses ``..`` so ``/data/../app/x.db`` cannot masquerade as
    durable. ``realpath`` is deliberately NOT used: it touches the filesystem, and
    this function must stay pure and usable for a path that does not exist yet.
    """
    return os.path.normpath(os.path.abspath(path))


def is_durable(path: Optional[str], dirs: Sequence[str]) -> bool:
    """Whether ``path`` lives under one of ``dirs``.

    ``None``/empty is NOT durable: an unresolved path means the owning module fell
    back to something this report cannot vouch for, which is exactly the case that
    must warn.

    Containment is tested on path *components*, not on string prefixes. A prefix
    test would accept ``/database/x.db`` as being under ``/data``.
    """
    if not path or not str(path).strip():
        return False
    target = _normalise(str(path))
    for d in dirs:
        if not d or not d.strip():
            continue
        root = _normalise(d)
        if target == root:
            return True
        if target.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def describe(path: Optional[str]) -> str:
    """A one-line factual description of a database file.

    Reports presence and size only. Deliberately does not open the database or
    count rows: this runs on the startup path, and taking a lock on a WAL database
    that another process may be mid-write on is not worth a nicer log line.
    ``missing`` is not an error — a fresh volume legitimately has no files yet.
    """
    if not path:
        return "unresolved"
    try:
        if not os.path.exists(path):
            return "missing (will be created on first write)"
        size = os.path.getsize(path)
    except OSError as exc:
        return f"unreadable ({exc})"
    return f"present, {size:,} bytes"


def build_report(
    entries: Iterable[StateEntry],
    dirs: Optional[Sequence[str]] = None,
    *,
    local: bool = False,
) -> List[str]:
    """Render the startup report as a list of log lines. Pure — no I/O beyond stat.

    ``local=True`` suppresses the durability warnings. A developer running the
    service from a checkout has no volume and no ``/data``, and a warning on every
    start is a warning nobody reads by the third day. The lines still state where
    each file is, so the information is not withheld — only the alarm is.
    """
    resolved_dirs = state_dirs() if dirs is None else tuple(dirs)
    lines: List[str] = [
        f"[state] durable directories: {', '.join(resolved_dirs) or '(none)'}"
        + ("  (local mode - durability warnings suppressed)" if local else "")
    ]
    warnings: List[str] = []

    for entry in entries:
        durable = is_durable(entry.path, resolved_dirs)
        # `!!` is an alarm, so it must not appear in local mode, where the whole point is
        # that a developer's checkout legitimately has no volume. Printing `!!` beside a
        # line whose warning was deliberately suppressed is a contradiction that teaches
        # people to ignore the marker — which is exactly what a marker must not do.
        mark = "ok " if (durable or local) else "!! "
        lines.append(
            f"[state] {mark}{entry.label}: {entry.path or '<unresolved>'} -> {describe(entry.path)}"
        )
        if durable or local:
            continue
        severity = "ERROR" if entry.critical else "WARN"
        detail = (
            "  This is the append-only compliance record: losing it breaks the "
            "five-year retention obligation and restarts the hash chain from genesis."
            if entry.critical
            else ""
        )
        warnings.append(
            f"[state] {severity}: {entry.label} at {entry.path or '<unresolved>'} "
            f"{WARN_NOT_DURABLE}.{detail} "
            f"Expected a path under: {', '.join(resolved_dirs)}. "
            f"Mount the deep_quant_data volume and set the *_DB_PATH variables "
            f"(see docs/DEPLOYMENT.md section 7.1)."
        )

    return lines + warnings


def report_state_paths(
    entries: Iterable[StateEntry],
    dirs: Optional[Sequence[str]] = None,
    *,
    local: bool = False,
    emit: Callable[[str], None] = print,
) -> List[str]:
    """Emit the report and return its lines.

    Never raises. This runs on the startup path and a failure to *describe* the
    state must not stop the service from serving — that would turn an
    observability feature into an outage.
    """
    try:
        lines = build_report(entries, dirs, local=local)
    except Exception as exc:  # noqa: BLE001 - a broken report must not block startup
        lines = [f"[state] WARN: could not build the state report ({exc})."]
    for line in lines:
        try:
            emit(line)
        except Exception:  # noqa: BLE001
            pass
    return lines
