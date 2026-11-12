#!/usr/bin/env python3
from __future__ import annotations

import errno
from typing import Any


PERMISSION_MARKERS = (
    "eperm",
    "eacces",
    "operation not permitted",
    "permission denied",
)
STABLE_PERMISSION_CODES = (
    "filesystem_permission_denied",
    "process_permission_denied",
    "network_permission_denied",
)


def permission_error_code(error: Any, capability: str) -> str | None:
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in visited:
            continue
        visited.add(id(current))
        current_code = getattr(current, "code", None)
        if current_code in STABLE_PERMISSION_CODES:
            return current_code
        if isinstance(current, PermissionError) or getattr(current, "errno", None) in {
            errno.EPERM,
            errno.EACCES,
        }:
            return f"{capability}_permission_denied"
        message = str(current).lower()
        for stable_code in STABLE_PERMISSION_CODES:
            if stable_code in message:
                return stable_code
        if any(marker in message for marker in PERMISSION_MARKERS):
            return f"{capability}_permission_denied"
        pending.extend(
            candidate
            for candidate in (
                getattr(current, "reason", None),
                getattr(current, "__cause__", None),
                getattr(current, "__context__", None),
            )
            if candidate is not None
        )
    return None
