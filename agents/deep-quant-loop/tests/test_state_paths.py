"""State_Path_Report tests — the durability warning must fire when it matters.

The bug this guards is silent: deep-quant had no volume, so every SQLite file lived
in a container layer and was destroyed on redeploy, and a fresh empty database looks
exactly like a working one. The report is the only thing that turns that into a
visible fault, so the property that actually matters is *the warning fires for a
non-durable critical path and does not fire for a durable one*. Everything else here
supports that.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import state_paths
from state_paths import StateEntry


# ── state_dirs ────────────────────────────────────────────────────────────────


def test_state_dirs_defaults_to_data():
    assert state_paths.state_dirs({}) == ("/data",)


def test_state_dirs_reads_env():
    joined = os.pathsep.join(["/srv/state", "/mnt/dq"])
    assert state_paths.state_dirs({"DEEP_QUANT_STATE_DIRS": joined}) == ("/srv/state", "/mnt/dq")


@pytest.mark.parametrize("value", ["", "   ", os.pathsep, f"{os.pathsep}{os.pathsep}"])
def test_state_dirs_blank_falls_back_rather_than_matching_everything(value):
    """A blank entry must not survive.

    An empty string in the durable set would match every path via the containment
    test and make the whole report vacuously green — the precise failure mode a
    safety check must not have.
    """
    assert state_paths.state_dirs({"DEEP_QUANT_STATE_DIRS": value}) == ("/data",)


# ── is_durable ────────────────────────────────────────────────────────────────


def test_durable_path_under_state_dir():
    assert state_paths.is_durable("/data/compliance.db", ["/data"]) is True


def test_non_durable_path_beside_the_module():
    assert state_paths.is_durable("/app/compliance.db", ["/data"]) is False


def test_prefix_lookalike_is_not_durable():
    """`/database/x.db` must not pass as being under `/data`.

    A naive `startswith` accepts it. Containment is a path-component question.
    """
    assert state_paths.is_durable("/database/x.db", ["/data"]) is False
    assert state_paths.is_durable("/data-old/x.db", ["/data"]) is False


def test_dot_dot_escape_is_not_durable():
    """A path that climbs out of the durable dir must not be reported as durable."""
    assert state_paths.is_durable("/data/../app/compliance.db", ["/data"]) is False


def test_the_state_dir_itself_counts():
    assert state_paths.is_durable("/data", ["/data"]) is True


@pytest.mark.parametrize("path", [None, "", "   "])
def test_unresolved_path_is_not_durable(path):
    """An unresolved path is the case that most needs warning, not the one to excuse."""
    assert state_paths.is_durable(path, ["/data"]) is False


def test_blank_state_dir_entry_never_matches():
    assert state_paths.is_durable("/app/x.db", ["", "   "]) is False


@given(st.text(min_size=1, max_size=40).filter(lambda s: s.strip() and "\x00" not in s))
@settings(max_examples=100, deadline=None)
def test_no_arbitrary_name_is_durable_under_an_unrelated_root(name):
    """Property: nothing outside the durable root is ever reported as durable."""
    assert state_paths.is_durable(f"/elsewhere/{name}", ["/data"]) is False


# ── describe ──────────────────────────────────────────────────────────────────


def test_describe_missing_is_not_an_error(tmp_path):
    """A fresh volume legitimately has no files; that must not read as a fault."""
    text = state_paths.describe(str(tmp_path / "nope.db"))
    assert "missing" in text
    assert "will be created" in text


def test_describe_present_reports_size(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"0" * 1234)
    assert "present" in state_paths.describe(str(db))
    assert "1,234" in state_paths.describe(str(db))


def test_describe_unresolved():
    assert state_paths.describe(None) == "unresolved"


# ── build_report — the behaviour that matters ────────────────────────────────


def test_non_durable_critical_path_produces_an_error_line():
    lines = state_paths.build_report(
        [StateEntry("compliance (P2/P5)", "/app/compliance.db", critical=True)], ["/data"]
    )
    blob = "\n".join(lines)
    assert state_paths.WARN_NOT_DURABLE in blob
    assert "ERROR" in blob
    # The operator must be told what losing it costs, not merely that it moved.
    assert "five-year" in blob
    assert "/data" in blob


def test_non_durable_ordinary_path_warns_but_does_not_error():
    lines = state_paths.build_report([StateEntry("telemetry", "/app/telemetry.db")], ["/data"])
    blob = "\n".join(lines)
    assert "WARN" in blob
    assert "ERROR" not in blob


def test_durable_path_produces_no_warning():
    lines = state_paths.build_report(
        [StateEntry("compliance (P2/P5)", "/data/compliance.db", critical=True)], ["/data"]
    )
    blob = "\n".join(lines)
    assert state_paths.WARN_NOT_DURABLE not in blob
    assert "ERROR" not in blob
    assert "/data/compliance.db" in blob


def test_local_mode_suppresses_the_alarm_but_not_the_facts():
    """A developer's checkout has no /data and must not be warned on every start.

    But the path is still reported: the alarm is suppressed, the information is not.
    """
    lines = state_paths.build_report(
        [StateEntry("compliance (P2/P5)", "/app/compliance.db", critical=True)],
        ["/data"],
        local=True,
    )
    blob = "\n".join(lines)
    assert state_paths.WARN_NOT_DURABLE not in blob
    assert "/app/compliance.db" in blob
    assert "local mode" in blob


def test_local_mode_does_not_print_an_alarm_marker():
    """`!!` beside a line whose warning was deliberately suppressed is a contradiction.

    It teaches people to ignore the marker, which is the one thing a marker must not do.
    Observed in a real test run: "local mode - durability warnings suppressed" followed by
    five `!!` lines.
    """
    lines = state_paths.build_report(
        [StateEntry("compliance", "/app/compliance.db", critical=True),
         StateEntry("sessions", "/app/sessions.db")],
        ["/data"],
        local=True,
    )
    assert not any("!!" in line for line in lines), lines


def test_non_local_mode_still_marks_a_non_durable_path():
    """The counterpart: outside local mode the marker must still fire."""
    lines = state_paths.build_report(
        [StateEntry("compliance", "/app/compliance.db", critical=True)], ["/data"], local=False
    )
    assert any("!!" in line for line in lines)


def test_unresolved_entry_warns():
    lines = state_paths.build_report([StateEntry("sessions", None)], ["/data"])
    assert any(state_paths.WARN_NOT_DURABLE in line for line in lines)


def test_warnings_come_after_the_inventory():
    """The inventory reads as a block; warnings follow it.

    Interleaved, a four-line report with two warnings is hard to scan in a log tail.
    """
    lines = state_paths.build_report(
        [
            StateEntry("a", "/data/a.db"),
            StateEntry("b", "/app/b.db"),
            StateEntry("c", "/data/c.db"),
        ],
        ["/data"],
    )
    first_warning = next(i for i, l in enumerate(lines) if state_paths.WARN_NOT_DURABLE in l)
    assert all(state_paths.WARN_NOT_DURABLE not in l for l in lines[:first_warning])


# ── report_state_paths — must never raise into startup ───────────────────────


def test_every_emitted_line_is_ascii():
    """These are read through `docker compose logs`.

    An em-dash arrives as mojibake on a console using a non-UTF-8 code page (measured:
    `ù`), and a line whose separator is garbage is a line an operator skims past. This
    is the one place the wording is load-bearing, so the constraint is pinned.
    """
    lines = state_paths.build_report(
        [
            StateEntry("compliance (P2/P5)", "/app/compliance.db", critical=True),
            StateEntry("telemetry", "/app/telemetry.db"),
            StateEntry("sessions", None),
            StateEntry("ok one", "/data/x.db"),
        ],
        ["/data"],
    )
    for line in lines:
        assert line.isascii(), f"non-ASCII in operator output: {line!r}"


def test_report_emits_every_line():
    out: list = []
    lines = state_paths.report_state_paths(
        [StateEntry("compliance", "/app/x.db", critical=True)], ["/data"], emit=out.append
    )
    assert out == lines


def test_report_survives_a_broken_emitter():
    """Observability must not be able to take the service down."""

    def boom(_line: str) -> None:
        raise RuntimeError("stdout is closed")

    state_paths.report_state_paths([StateEntry("compliance", "/data/x.db")], ["/data"], emit=boom)


def test_report_survives_a_broken_entry():
    class Hostile:
        label = "hostile"

        @property
        def path(self):
            raise RuntimeError("nope")

        critical = False

    out: list = []
    state_paths.report_state_paths([Hostile()], ["/data"], emit=out.append)  # type: ignore[list-item]
    assert out, "a failure must still emit something rather than going silent"
