"""Read wallet secrets from mounted secret files or local-development env vars."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("secrets")


def get_secret(name: str, *, required: bool = False) -> str | None:
    file_path = os.getenv(f"{name}_FILE")
    value = os.getenv(name)
    production = os.getenv("VERCEL") or os.getenv("ENVIRONMENT", "").lower() == "production"
    if production and value and not file_path and os.getenv("ALLOW_ENV_WALLET_KEYS", "false").lower() != "true":
        raise RuntimeError(f"Production secret {name} must be mounted using {name}_FILE or explicitly read from an encrypted platform secret store")
    if file_path:
        path = Path(file_path)
        if not path.is_file() or path.stat().st_size > 16_384:
            raise RuntimeError(f"Mounted secret file for {name} is invalid or too large")
        value = path.read_text(encoding="utf-8").strip()
    if required and not value:
        raise RuntimeError(f"Set {name}_FILE to a protected secret file containing this key")
    if value and not production and not file_path:
        logger.warning("Using %s from process environment; prefer a protected secret file", name)
    return value or None
