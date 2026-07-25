"""Per-user OpenRouter key resolution via the backend internal endpoint.

The Deep Quant service runs on the droplet whose IP is whitelisted in the
backend's ``INTERNAL_ALLOWED_IPS``. For each analysis run it resolves the
requesting user's provisioned OpenRouter key server-to-server:

    GET {INTERNAL_API_BASE_URL}/api/v1/internal/api-key/{user_id}
    -> { "success": true, "data": { "apiKeys": [ { "key": "sk-or-...",
                                                    "provider": "openrouter" } ] } }

The raw key never touches the desktop client — only the droplet fetches it and
uses it to bind the LLM for that run. Results are cached per user_id with a
short TTL so a burst of turns in one session doesn't hammer the backend.

Configuration (env):
    INTERNAL_API_BASE_URL  backend base URL (default https://api-web.stratai.live)
    OPENROUTER_BASE_URL    OpenAI-compatible LLM base (default https://openrouter.ai/api/v1)
    INTERNAL_API_KEY_TTL   cache seconds (default 300)
    INTERNAL_API_TIMEOUT   HTTP timeout seconds (default 8)
"""

import os
import time
import threading
from typing import Optional, Tuple

import httpx


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


def internal_api_base_url() -> str:
    return _env("INTERNAL_API_BASE_URL", "https://api-web.stratai.live").rstrip("/")


def openrouter_base_url() -> str:
    # LangChain appends /chat/completions, so store the base without it.
    base = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base


def _ttl_seconds() -> float:
    try:
        return float(_env("INTERNAL_API_KEY_TTL", "300"))
    except ValueError:
        return 300.0


def _timeout_seconds() -> float:
    try:
        return float(_env("INTERNAL_API_TIMEOUT", "8"))
    except ValueError:
        return 8.0


# user_id -> (openrouter_key, expiry_epoch)
_cache: dict = {}
_cache_lock = threading.Lock()


class ApiKeyResolutionError(Exception):
    """Raised when the user's OpenRouter key cannot be resolved."""


def _cached(user_id: str) -> Optional[str]:
    with _cache_lock:
        entry = _cache.get(user_id)
        if entry and entry[1] > time.time():
            return entry[0]
        if entry:
            _cache.pop(user_id, None)
    return None


def _store(user_id: str, key: str) -> None:
    with _cache_lock:
        _cache[user_id] = (key, time.time() + _ttl_seconds())


def _extract_openrouter_key(payload: dict) -> Optional[str]:
    """Pull the active OpenRouter key from the internal endpoint response.

    Prefers an entry whose provider is ``openrouter``; falls back to the first
    key with a non-empty ``key`` field. Never raises on a malformed shape.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    keys = data.get("apiKeys") if isinstance(data, dict) else None
    if not isinstance(keys, list):
        return None
    # Prefer an explicit openrouter provider.
    for entry in keys:
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", "")).lower()
        k = entry.get("key")
        if provider == "openrouter" and isinstance(k, str) and k.strip():
            return k.strip()
    # Fallback: first usable key regardless of provider label.
    for entry in keys:
        if isinstance(entry, dict):
            k = entry.get("key")
            if isinstance(k, str) and k.strip():
                return k.strip()
    return None


def resolve_openrouter_key(user_id: str) -> str:
    """Resolve the OpenRouter API key for ``user_id`` (cached).

    Raises ``ApiKeyResolutionError`` when the user_id is missing, the backend is
    unreachable, returns a non-2xx (e.g. the droplet IP is not whitelisted), or
    the response carries no usable key — so the caller can surface a clean
    "LLM key unavailable" error instead of falling back to a shared key.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise ApiKeyResolutionError("no user_id supplied for key resolution")

    cached = _cached(uid)
    if cached:
        return cached

    url = f"{internal_api_base_url()}/api/v1/internal/api-key/{uid}"
    try:
        resp = httpx.get(url, timeout=_timeout_seconds())
    except Exception as exc:  # noqa: BLE001
        raise ApiKeyResolutionError(f"internal api-key endpoint unreachable: {exc}") from exc

    if resp.status_code == 403 or resp.status_code == 401:
        raise ApiKeyResolutionError(
            "internal api-key endpoint denied access — is the droplet IP in "
            "INTERNAL_ALLOWED_IPS?"
        )
    if resp.status_code // 100 != 2:
        raise ApiKeyResolutionError(
            f"internal api-key endpoint returned HTTP {resp.status_code}"
        )

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ApiKeyResolutionError(f"invalid internal api-key response: {exc}") from exc

    key = _extract_openrouter_key(payload)
    if not key:
        raise ApiKeyResolutionError(f"no active OpenRouter key provisioned for user {uid}")

    _store(uid, key)
    return key
