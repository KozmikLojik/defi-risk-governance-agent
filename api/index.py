"""Vercel Python function entry point for the FastAPI application."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# Vercel function filesystems are ephemeral; local runs keep data in backend/data.
if os.getenv("VERCEL"):
    os.environ.setdefault("GUARDIAN_DB_PATH", "/tmp/guardianai.db")

from main import app  # noqa: E402

