"""Role-based API keys, stored as SHA-256 digests for high-entropy secrets."""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from typing import Callable

from fastapi import Header, HTTPException

logger = logging.getLogger("auth")
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "true" if os.getenv("VERCEL") or
                          os.getenv("ENVIRONMENT", "").lower() == "production" else "false").lower() == "true"
_RAW_KEYS = json.loads(os.getenv("AUTH_API_KEYS", "{}"))
_DIGEST_FORMAT = re.compile(r"^sha256\$[0-9a-f]{64}$")
_KEY_MIN_LENGTH = 32


def _normalize_keys(raw: dict) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        raise RuntimeError("AUTH_API_KEYS must be a JSON object mapping roles to keys or key lists")
    keys: dict[str, tuple[str, ...]] = {}
    for role, values in raw.items():
        if role not in {"admin", "operator", "trader"}:
            raise RuntimeError(f"Unknown API key role: {role}")
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not values:
            raise RuntimeError(f"AUTH_API_KEYS role {role} must map to a key or non-empty key list")
        normalized = []
        for value in values:
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"AUTH_API_KEYS contains an invalid key for role {role}")
            if value.startswith("sha256$"):
                if not _DIGEST_FORMAT.fullmatch(value):
                    raise RuntimeError(f"AUTH_API_KEYS has an invalid SHA-256 digest for role {role}")
                normalized.append(value.removeprefix("sha256$"))
            else:
                if AUTH_REQUIRED:
                    raise RuntimeError("Production AUTH_API_KEYS entries must be sha256$ digests; generate one with python -m services.auth hash-key")
                if len(value) < _KEY_MIN_LENGTH:
                    raise RuntimeError(f"Local API keys must be at least {_KEY_MIN_LENGTH} characters")
                logger.warning("Plaintext AUTH_API_KEYS are only allowed outside production; store digests instead")
                normalized.append(hashlib.sha256(value.encode()).hexdigest())
        keys[role] = tuple(normalized)
    if AUTH_REQUIRED and not keys:
        raise RuntimeError("AUTH_REQUIRED is enabled; configure hashed AUTH_API_KEYS by role")
    return keys


API_KEYS = _normalize_keys(_RAW_KEYS)


def hash_api_key(raw_key: str) -> str:
    if len(raw_key) < _KEY_MIN_LENGTH:
        raise ValueError(f"API keys must be at least {_KEY_MIN_LENGTH} characters")
    return "sha256$" + hashlib.sha256(raw_key.encode()).hexdigest()


def require_role(*allowed_roles: str) -> Callable:
    async def guard(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
        if not API_KEYS and not AUTH_REQUIRED:
            return "local-development"
        if not x_api_key:
            raise HTTPException(status_code=401, detail="Valid X-API-Key required")
        digest = hashlib.sha256(x_api_key.encode()).hexdigest()
        matched_role = next((role for role, digests in API_KEYS.items()
                             if any(hmac.compare_digest(digest, item) for item in digests)), None)
        if matched_role is None:
            raise HTTPException(status_code=401, detail="Valid X-API-Key required")
        if matched_role != "admin" and matched_role not in allowed_roles:
            raise HTTPException(status_code=403, detail="API key does not have permission for this action")
        return matched_role
    return guard


def api_key_identity(candidate: str | None) -> str | None:
    """Return a stable identity only for a configured key; invalid keys share IP limits."""
    if not candidate:
        return None
    digest = hashlib.sha256(candidate.encode()).hexdigest()
    for digests in API_KEYS.values():
        if any(hmac.compare_digest(digest, configured) for configured in digests):
            return f"key:{digest}"
    return None


def _main() -> None:
    if len(sys.argv) == 3 and sys.argv[1:] == ["hash-key", "--stdin"]:
        raw = sys.stdin.readline().rstrip("\r\n")
    elif len(sys.argv) == 2 and sys.argv[1] == "hash-key":
        raw = getpass.getpass("New API key (minimum 32 characters): ")
    else:
        raise SystemExit("Usage: python -m services.auth hash-key [--stdin]")
    print(hash_api_key(raw))


if __name__ == "__main__":
    _main()
