"""General utilities for rpchat."""
from __future__ import annotations

import os


def _safe_int_env(key: str, default: int | None = None) -> int | None:
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            return default


def _as_int(v, default=0):
    try:
        if isinstance(v, bool):
            return int(v)
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return default
