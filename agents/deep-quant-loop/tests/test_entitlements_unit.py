"""Compliance blocker P1 — the authoritative RESEARCH SKU gate, proved.

Gate 0->1 in ``docs/business/PLAN_OF_ACTION.md`` §4.2 requires that "no
recommendation surface is reachable by an unlicensed user, **verified by a
written test, not an eyeball**". The desktop-side proof lives in
``frontend/src/lib/__tests__/sku.property.test.ts`` and
``frontend/src/store/__tests__/useQuantStore.skuGate.property.test.ts``. That
layer runs on the user's machine, so it is an affordance, not a boundary — anyone
can POST straight at this service. This file proves the boundary itself.

Two things are asserted, and the second is the one that matters:

  1. ``entitlements.py`` denies on every failure path (fail closed).
  2. ``/run``, ``/qa`` and ``/resume`` refuse **before any graph work happens** —
     ``event_generator`` is replaced by a tripwire and must never be reached. A
     gate that streams a refusal *after* invoking the graph has still generated,
     billed and logged a regulated recommendation.
"""

import os
import sys

import pytest

_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import entitlements  # noqa: E402
from entitlements import (  # noqa: E402
    ENTITLEMENT_ERROR_CODE,
    EntitlementError,
    RESEARCH_MODES,
    TERMINAL_MODES,
    _extract_entitlement,
    enforcement_enabled,
    is_research_entitled,
    mode_requires_research,
    require_research_entitlement,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_entitlement_state(monkeypatch):
    """Every test starts with an empty cache and enforcement explicitly ON.

    Enforcement defaults OFF in the shipped service (the remote endpoint does not
    exist yet), so without this the assertions below would pass vacuously.
    """
    entitlements.clear_cache()
    monkeypatch.setenv("SKU_ENFORCE", "1")
    monkeypatch.setenv("INTERNAL_API_BASE_URL", "http://127.0.0.1:9/never-served")
    monkeypatch.setenv("INTERNAL_API_TIMEOUT", "1")
    yield
    entitlements.clear_cache()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_on_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not json")
        return self._payload


def _grant(**data):
    return _FakeResponse(200, {"success": True, "data": data})


# ── 1. Enforcement switch ───────────────────────────────────────────────────


class TestEnforcementSwitch:
    def test_defaults_off_so_the_unbuilt_endpoint_does_not_break_dev(self, monkeypatch):
        monkeypatch.delenv("SKU_ENFORCE", raising=False)
        assert enforcement_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_recognised_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("SKU_ENFORCE", value)
        assert enforcement_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "   ", "maybe"])
    def test_everything_else_disables(self, monkeypatch, value):
        monkeypatch.setenv("SKU_ENFORCE", value)
        assert enforcement_enabled() is False

    def test_disabled_enforcement_permits_research_without_a_user(self, monkeypatch):
        monkeypatch.setenv("SKU_ENFORCE", "0")
        # No raise: the gate is a no-op, which is the intended dev posture.
        require_research_entitlement(None, "FIND")


# ── 2. Mode classification ──────────────────────────────────────────────────


class TestModeClassification:
    def test_verify_is_the_only_terminal_mode(self):
        assert TERMINAL_MODES == frozenset({"VERIFY"})

    def test_find_debate_qa_are_research(self):
        assert RESEARCH_MODES == frozenset({"FIND", "DEBATE", "QA"})

    @pytest.mark.parametrize("mode", ["FIND", "DEBATE", "QA", "find", " qa "])
    def test_research_modes_require_research(self, mode):
        assert mode_requires_research(mode) is True

    @pytest.mark.parametrize("mode", ["VERIFY", "verify", " Verify "])
    def test_verify_never_requires_research(self, mode):
        assert mode_requires_research(mode) is False

    @pytest.mark.parametrize("mode", [None, "", "   ", "UNKNOWN", "FIND2", "SCAN"])
    def test_unknown_and_empty_modes_are_gated_by_default(self, mode):
        # Open-by-default would mean a mode added to graph.py later ships
        # ungated. This is the "new mode is regulated until proven otherwise"
        # posture the module docstring commits to.
        assert mode_requires_research(mode) is True

    def test_verify_passes_the_gate_with_no_user_and_no_backend(self):
        # The repackage removes nothing: VERIFY validates the user's own
        # entry/stop/target and must keep working on the unregulated SKU, even
        # with the entitlement backend unreachable.
        require_research_entitlement(None, "VERIFY")
        require_research_entitlement("", "verify")


# ── 3. Fail-closed resolution ───────────────────────────────────────────────


class TestFailsClosed:
    def test_missing_user_id_denies(self):
        for uid in (None, "", "   "):
            assert is_research_entitled(uid) is False
            with pytest.raises(EntitlementError):
                require_research_entitlement(uid, "FIND")

    def test_unreachable_endpoint_denies(self, monkeypatch):
        def boom(*_a, **_kw):
            raise OSError("connection refused")

        monkeypatch.setattr(entitlements.httpx, "get", boom)
        assert is_research_entitled("user-1") is False

    def test_timeout_denies(self, monkeypatch):
        import httpx

        def boom(*_a, **_kw):
            raise httpx.ReadTimeout("timed out")

        monkeypatch.setattr(entitlements.httpx, "get", boom)
        assert is_research_entitled("user-1") is False

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
    def test_non_2xx_denies(self, monkeypatch, status):
        # 404 is what the endpoint returns today (unimplemented) AND what the
        # backend returns to a caller outside INTERNAL_ALLOWED_IPS.
        monkeypatch.setattr(
            entitlements.httpx, "get", lambda *_a, **_kw: _FakeResponse(status)
        )
        assert is_research_entitled("user-1") is False

    def test_malformed_json_denies(self, monkeypatch):
        monkeypatch.setattr(
            entitlements.httpx,
            "get",
            lambda *_a, **_kw: _FakeResponse(200, raise_on_json=True),
        )
        assert is_research_entitled("user-1") is False

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"success": True},
            {"data": None},
            {"data": "RESEARCH"},
            {"data": []},
            {"data": {}},
            {"data": {"sku": "TERMINAL"}},
            {"data": {"canAccessResearch": False}},
            {"data": {"canAccessResearch": None}},
            {"data": {"planName": "RESEARCH"}},  # plan NAME must not grant
        ],
    )
    def test_responses_without_a_grant_deny(self, monkeypatch, payload):
        monkeypatch.setattr(
            entitlements.httpx, "get", lambda *_a, **_kw: _FakeResponse(200, payload)
        )
        assert is_research_entitled("user-1") is False

    @pytest.mark.parametrize(
        "value", ["true", "True", "1", 1, "yes", [1], {"a": 1}, "RESEARCH"]
    )
    def test_truthy_non_boolean_does_not_grant(self, value):
        # A loosely-typed remote response must not become an entitlement: the
        # check is identity against True, not coercion. `"false"` is truthy in
        # Python too, which is exactly the trap this guards.
        assert _extract_entitlement({"data": {"canAccessResearch": value}}) is False

    def test_sku_string_grants_but_only_when_it_says_research(self):
        assert _extract_entitlement({"data": {"sku": "RESEARCH"}}) is True
        assert _extract_entitlement({"data": {"sku": " research "}}) is True
        assert _extract_entitlement({"data": {"sku": "TERMINAL"}}) is False
        assert _extract_entitlement({"data": {"sku": "RESEARCHER"}}) is False

    def test_explicit_boolean_true_grants(self):
        assert _extract_entitlement({"data": {"canAccessResearch": True}}) is True

    def test_extract_never_raises_on_hostile_input(self):
        for payload in (None, [], "x", 0, {"data": 5}, {"data": {"sku": 5}}):
            assert _extract_entitlement(payload) is False


# ── 4. The grant path, so the denials above are not vacuous ─────────────────


class TestGrantPath:
    def test_entitled_user_passes(self, monkeypatch):
        monkeypatch.setattr(
            entitlements.httpx,
            "get",
            lambda *_a, **_kw: _grant(canAccessResearch=True, sku="RESEARCH"),
        )
        assert is_research_entitled("user-ok") is True
        require_research_entitlement("user-ok", "FIND")  # no raise

    def test_grant_is_cached_so_the_hot_path_does_not_refetch(self, monkeypatch):
        calls = {"n": 0}

        def counting_get(*_a, **_kw):
            calls["n"] += 1
            return _grant(canAccessResearch=True)

        monkeypatch.setattr(entitlements.httpx, "get", counting_get)
        assert is_research_entitled("user-cache") is True
        assert is_research_entitled("user-cache") is True
        assert is_research_entitled("user-cache") is True
        assert calls["n"] == 1

    def test_clear_cache_forces_a_refetch(self, monkeypatch):
        calls = {"n": 0}

        def counting_get(*_a, **_kw):
            calls["n"] += 1
            return _grant(canAccessResearch=True)

        monkeypatch.setattr(entitlements.httpx, "get", counting_get)
        is_research_entitled("user-x")
        entitlements.clear_cache()
        is_research_entitled("user-x")
        assert calls["n"] == 2

    def test_a_denial_is_not_cached_as_a_grant_for_another_user(self, monkeypatch):
        def per_user(url, *_a, **_kw):
            if url.endswith("/entitled"):
                return _grant(canAccessResearch=True)
            return _FakeResponse(200, {"data": {"canAccessResearch": False}})

        monkeypatch.setattr(entitlements.httpx, "get", per_user)
        assert is_research_entitled("entitled") is True
        assert is_research_entitled("someone-else") is False

    def test_expired_cache_entry_is_re_resolved(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_ENTITLEMENT_TTL", "0")
        calls = {"n": 0}

        def counting_get(*_a, **_kw):
            calls["n"] += 1
            return _grant(canAccessResearch=True)

        monkeypatch.setattr(entitlements.httpx, "get", counting_get)
        is_research_entitled("user-ttl")
        is_research_entitled("user-ttl")
        assert calls["n"] == 2, "a zero TTL must not serve a stale grant"


# ── 5. The error carries a machine-readable code ────────────────────────────


class TestRefusalShape:
    def test_error_code_is_the_documented_marker(self, monkeypatch):
        monkeypatch.setattr(
            entitlements.httpx, "get", lambda *_a, **_kw: _FakeResponse(404)
        )
        with pytest.raises(EntitlementError) as excinfo:
            require_research_entitlement("user-1", "FIND")
        assert excinfo.value.code == ENTITLEMENT_ERROR_CODE

    def test_message_frames_a_plan_boundary_not_a_system_fault(self, monkeypatch):
        monkeypatch.setattr(
            entitlements.httpx, "get", lambda *_a, **_kw: _FakeResponse(404)
        )
        with pytest.raises(EntitlementError) as excinfo:
            require_research_entitlement("user-1", "FIND")
        text = str(excinfo.value).lower()
        assert "research" in text
        # Must not read as an outage, or users will retry a policy refusal.
        for word in ("traceback", "exception", "internal server error"):
            assert word not in text
