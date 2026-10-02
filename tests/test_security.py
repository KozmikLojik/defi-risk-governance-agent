import asyncio

import pytest

from services import auth
from services.rate_limiter import RateLimiter
from services.secrets import get_secret


def test_api_key_hashing_supports_rotation_and_rejects_weak_local_keys(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_REQUIRED", False)
    key_a = "a" * 40
    key_b = "b" * 40
    configured = auth._normalize_keys({"trader": [auth.hash_api_key(key_a), auth.hash_api_key(key_b)]})
    assert configured["trader"] == (auth.hash_api_key(key_a).split("$", 1)[1],
                                    auth.hash_api_key(key_b).split("$", 1)[1])
    monkeypatch.setattr(auth, "API_KEYS", configured)
    assert auth.api_key_identity(key_a) == "key:" + auth.hash_api_key(key_a).split("$", 1)[1]
    assert auth.api_key_identity("invalid-key") is None
    monkeypatch.setattr(auth, "AUTH_REQUIRED", True)
    with pytest.raises(RuntimeError, match="must be sha256"):
        auth._normalize_keys({"trader": key_a})
    assert auth._normalize_keys({"trader": auth.hash_api_key(key_a)})["trader"]
    with pytest.raises(ValueError):
        auth.hash_api_key("weak")


def test_production_wallet_secret_requires_file_or_explicit_secret_store_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AGENT_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("AGENT_PRIVATE_KEY", "secret-value")
    monkeypatch.delenv("ALLOW_ENV_WALLET_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="must be mounted"):
        get_secret("AGENT_PRIVATE_KEY")

    secret_file = tmp_path / "wallet.key"
    secret_file.write_text("0xprivate-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PRIVATE_KEY_FILE", str(secret_file))
    assert get_secret("AGENT_PRIVATE_KEY", required=True) == "0xprivate-key"


def test_rate_limiter_enforces_limit_for_fixed_window(monkeypatch):
    from services import rate_limiter as module

    clock = [120.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    limiter = RateLimiter()
    assert asyncio.run(limiter.check("client-key", "write", 2))[0] is True
    assert asyncio.run(limiter.check("client-key", "write", 2))[0] is True
    allowed, retry_after = asyncio.run(limiter.check("client-key", "write", 2))
    assert allowed is False
    assert retry_after == 60
    clock[0] = 180.0
    assert asyncio.run(limiter.check("client-key", "write", 2))[0] is True
