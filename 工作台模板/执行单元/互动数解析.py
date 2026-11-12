"""统一解析带中文单位的互动数字。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def parse_count(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    text = re.sub(r"\s+", "", text).rstrip("+")
    multiplier = 1
    if text.endswith("亿"):
        multiplier, text = 100_000_000, text[:-1]
    elif text.endswith("万"):
        multiplier, text = 10_000, text[:-1]
    if not text or text in {"-", "."}:
        return None
    try:
        return int(Decimal(text) * multiplier)
    except (InvalidOperation, ValueError):
        return None


def count_text(value: Any) -> str:
    parsed = parse_count(value)
    return "" if parsed is None else str(parsed)
