"""RESEARCH SKU entitlement enforcement — the authoritative compliance gate.

WHY THIS EXISTS
---------------
Under the SEBI Research Analyst regulations, emitting a directional
recommendation (buy/sell with entry, target and stop) is a regulated activity.
``docs/business/PLAN_OF_ACTION.md`` §4.2 blocker P1 requires that the
recommendation surface be gated **at the API layer, not the UI**, and Gate 0->1
requires that "no recommendation surface is reachable by an unlicensed user,
verified by a written test, not an eyeball".

This module is that gate. The desktop client also checks
``frontend/src/lib/sku.ts``, but that runs on the user's machine and is trivially
bypassable — anyone can POST directly to this service. This is the check that
actually holds, so it is deliberately the strictest layer.

MODE -> SKU
-----------
VERIFY is TERMINAL: the user supplies their own entry/stop/target and VERIFY
validates the arithmetic against ATR and the reward-to-risk floor. Checking a
user's own numbers is not research. FIND, DEBATE and QA all produce or elaborate
a directional recommendation, so all three require RESEARCH.

Note that DEBATE is routable on ``/run`` (``graph.py`` ``DEBATE_MODE``) even
though no UI exposes it. An unknown mode string is treated as RESEARCH, so a new
mode added to the graph is gated by default rather than open by default.

FAIL CLOSED
-----------
Every failure path denies access: no user_id, backend unreachable, non-2xx,
malformed JSON, or no entitlement in the response. A compliance gate that opens
when its dependency is down is not a gate. This means that while
``INTERNAL_API_BASE_URL`` lacks the entitlement endpoint, enabling
``SKU_ENFORCE`` denies all RESEARCH traffic — which is the correct posture and
why the flag defaults off until the endpoint ships.

REQUIRED REMOTE ENDPOINT (not yet implemented)
----------------------------------------------
Auth, plans and credits live in a separate deployment, not this repository. That
service must expose, alongside the existing ``/api/v1/internal/api-key/{user_id}``:

    GET {INTERNAL_API_BASE_URL}/api/v1/internal/entitlement/{user_id}

    200 -> { "success": true,
             "data": { "sku": "RESEARCH",          # or "TERMINAL"
                       "canAccessResearch": true,   # authoritative boolean
                       "kycVerified": true,         # RA client onboarding done
                       "planName": "RESEARCH" } }

    404 -> user unknown, or the caller IP is not in INTERNAL_ALLOWED_IPS

Either ``sku == "RESEARCH"`` or ``canAccessResearch == true`` grants access.
``kycVerified`` is read and logged when present but is NOT currently part of the
grant decision — see the open item in
``docs/compliance/AI_MODEL_GOVERNANCE.md``. Until the endpoint exists, requests
fail closed as described above.

Configuration (env):
    SKU_ENFORCE             "1"/"true" to enforce (default OFF — see above)
    INTERNAL_API_BASE_URL   backend base URL (default https://api-web.stratai.live)
    INTERNAL_ENTITLEMENT_TTL  cache seconds (default 300)
    INTERNAL_API_TIMEOUT    HTTP timeout seconds (default 8)
"""

import os
import threading
import time
from typing import Optional

import httpx

# Modes that require the RESEARCH SKU. Anything not explicitly TERMINAL is
# treated as RESEARCH so new graph modes are gated by default.
TERMINAL_MODES = frozenset({"VERIFY"})
RESEARCH_MODES = frozenset({"FIND", "DEBATE", "QA"})

# Structured code the desktop maps to its upgrade CTA. Keep in sync with the
# `entitlement_required` handling in the frontend SSE consumer.
ENTITLEMENT_ERROR_CODE = "entitlement_required"


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


def enforcement_enabled() -> bool:
    """Whether the gate is active.

    Defaults to OFF because the remote entitlement endpoint does not exist yet
    and fail-closed enforcement would deny every RESEARCH run. Turn on with
    ``SKU_ENFORCE=1`` once that endpoint ships.
    """
    return _env("SKU_ENFORCE", "0").lower() in ("1", "true", "yes", "on")


def internal_api_base_url() -> str:
    return _env("INTERNAL_API_BASE_URL", "https://api-web.stratai.live").rstrip("/")


def _ttl_seconds() -> float:
    try:
        return float(_env("INTERNAL_ENTITLEMENT_TTL", "300"))
    except ValueError:
        return 300.0


def _timeout_seconds() -> float:
    try:
        return float(_env("INTERNAL_API_TIMEOUT", "8"))
    except ValueError:
        return 8.0


class EntitlementError(Exception):
    """Raised when a RESEARCH-gated request is not entitled.

    Carries ``code`` so the SSE layer can emit a machine-readable marker the UI
    turns into an upgrade prompt, rather than a generic failure.
    """

    def __init__(self, message: str, code: str = ENTITLEMENT_ERROR_CODE):
        super().__init__(message)
        self.code = code


# user_id -> (is_research_entitled, expiry_epoch)
_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(user_id: str) -> Optional[bool]:
    with _cache_lock:
        entry = _cache.get(user_id)
        if entry and entry[1] > time.time():
            return entry[0]
        if entry:
            _cache.pop(user_id, None)
    return None


def _store(user_id: str, entitled: bool) -> None:
    with _cache_lock:
        _cache[user_id] = (entitled, time.time() + _ttl_seconds())


def clear_cache() -> None:
    """Drop all cached entitlements. Used by tests and after a plan change."""
    with _cache_lock:
        _cache.clear()


def mode_requires_research(mode: Optional[str]) -> bool:
    """Whether ``mode`` needs the RESEARCH SKU.

    Unknown or empty modes return True (gated by default). ``/run`` defaults
    ``mode`` to "FIND" in its request model, so an omitted mode is a FIND and
    correctly requires RESEARCH.
    """
    normalised = (mode or "").strip().upper()
    if not normalised:
        return True
    return normalised not in TERMINAL_MODES


def _extract_entitlement(payload: dict) -> bool:
    """Read the research grant from the internal endpoint response.

    Accepts either an explicit ``canAccessResearch`` boolean or ``sku ==
    "RESEARCH"``. Requires a real boolean ``True`` (not a truthy string) so a
    loosely-typed ``"false"`` cannot grant access. Never raises.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return False

    if data.get("canAccessResearch") is True:
        return True

    sku = data.get("sku")
    if isinstance(sku, str) and sku.strip().upper() == "RESEARCH":
        return True

    return False


def is_research_entitled(user_id: Optional[str]) -> bool:
    """Resolve whether ``user_id`` holds the RESEARCH SKU (cached).

    Returns False on every failure path rather than raising, so callers that
    only need a boolean cannot accidentally treat an error as a grant.
    """
    uid = (user_id or "").strip()
    if not uid:
        return False

    cached = _cached(uid)
    if cached is not None:
        return cached

    url = f"{internal_api_base_url()}/api/v1/internal/entitlement/{uid}"
    try:
        resp = httpx.get(url, timeout=_timeout_seconds())
    except Exception as exc:  # noqa: BLE001
        print(f"[entitlements] DENY user={uid}: endpoint unreachable: {exc}")
        return False

    if resp.status_code // 100 != 2:
        # 404 is the expected response until the endpoint is implemented, and
        # also what the backend returns to a non-whitelisted caller IP.
        print(
            f"[entitlements] DENY user={uid}: endpoint returned HTTP "
            f"{resp.status_code} (endpoint may not be implemented yet, or this "
            f"host is not in the backend INTERNAL_ALLOWED_IPS)"
        )
        return False

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[entitlements] DENY user={uid}: malformed response: {exc}")
        return False

    entitled = _extract_entitlement(payload)
    _store(uid, entitled)
    if not entitled:
        print(f"[entitlements] DENY user={uid}: no RESEARCH entitlement on plan")
    return entitled


def require_research_entitlement(user_id: Optional[str], mode: Optional[str]) -> None:
    """Raise ``EntitlementError`` unless this request may proceed.

    A no-op when the mode is TERMINAL (VERIFY) or enforcement is disabled. This
    is the single call site guarding the FastAPI recommendation endpoints; it
    must run BEFORE any graph work, LLM call or SSE stream begins so an
    unentitled caller receives no research output at all.
    """
    if not mode_requires_research(mode):
        return
    if not enforcement_enabled():
        return

    uid = (user_id or "").strip()
    if not uid:
        raise EntitlementError(
            "This analysis requires a RESEARCH subscription. No authenticated "
            "user was supplied with the request."
        )

    if not is_research_entitled(uid):
        raise EntitlementError(
            "This analysis requires a RESEARCH subscription. Trade "
            "recommendations are available only to subscribers of our "
            "SEBI-registered research service."
        )
