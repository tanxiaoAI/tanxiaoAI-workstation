"""Normalize platform publish-time values to the workspace display format."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any


DISPLAY_FORMAT = "%Y/%m/%d %H:%M"


def _reference_datetime(reference_time: datetime | None) -> datetime:
    return reference_time or datetime.now()


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value.strftime(DISPLAY_FORMAT)


def _midnight(value: date) -> datetime:
    return datetime.combine(value, time.min)


def _parse_numeric(text: str) -> datetime | None:
    try:
        numeric = float(text)
    except ValueError:
        return None
    if abs(numeric) >= 10_000_000_000:
        numeric /= 1000
    try:
        return datetime.fromtimestamp(numeric).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _parse_relative(text: str, reference: datetime) -> datetime | None:
    # Platform labels often append a region (for example, "6天前 安徽")
    # or a clock value ("昨天 12:30"). Keep the date semantics and ignore
    # non-time location text instead of treating the whole label as invalid.
    normalized = re.sub(r"\s+", " ", text.strip())
    normalized = re.sub(r"\s+(?:[\u4e00-\u9fff]{2,8})$", "", normalized)
    if normalized in {"刚刚", "刚才"}:
        return reference
    day_text, _, clock = normalized.partition(" ")
    if day_text in {"今天", "今日"}:
        base = _midnight(reference.date())
    elif day_text == "昨天":
        base = _midnight(reference.date() - timedelta(days=1))
    elif day_text == "前天":
        base = _midnight(reference.date() - timedelta(days=2))
    else:
        match = re.fullmatch(r"(\d+)\s*天前", day_text)
        if not match:
            hour_match = re.fullmatch(r"(\d+)\s*小时前", day_text)
            if hour_match:
                return reference - timedelta(hours=int(hour_match.group(1)))
            minute_match = re.fullmatch(r"(\d+)\s*分钟前", day_text)
            if minute_match:
                return reference - timedelta(minutes=int(minute_match.group(1)))
            return None
        base = _midnight(reference.date() - timedelta(days=int(match.group(1))))
    if clock:
        clock_match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", clock)
        if clock_match:
            return base.replace(
                hour=int(clock_match.group(1)),
                minute=int(clock_match.group(2)),
                second=int(clock_match.group(3) or 0),
            )
    return base


def _parse_text_date(text: str, reference: datetime) -> datetime | None:
    normalized = text.strip()
    normalized = normalized.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("T", " ").replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for pattern in (
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
    ):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue

    match = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})", normalized)
    if match:
        try:
            return datetime(reference.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


def normalize_publish_time_text(
    value: Any,
    reference_time: datetime | None = None,
) -> str:
    """Return a normalized local display value, or an empty string if unparseable."""
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, date):
        return _format_datetime(_midnight(value))

    text = str(value).strip() if value is not None else ""
    if not text:
        return ""
    reference = _reference_datetime(reference_time)
    parsed = _parse_numeric(text)
    if parsed is None:
        parsed = _parse_relative(text, reference)
    if parsed is None:
        parsed = _parse_text_date(text, reference)
    return _format_datetime(parsed) if parsed is not None else ""


__all__ = ["normalize_publish_time_text"]
