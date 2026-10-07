"""Turning CSV text into numbers and dates (and numbers back into text)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

_TIMESTAMP_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
]


def parse_amount(text) -> Decimal | None:
    """'1,250.50' / '$40' -> Decimal. Returns None if it is not a number."""
    cleaned = re.sub(r"[,$\s]", "", str(text or ""))
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse_timestamp(text) -> datetime | None:
    """Returns None if the text is blank or not a date we recognise."""
    text = (text or "").strip()
    if not text:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in _TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def money(value: Decimal | None) -> str:
    """Decimal -> '1250.50' (blank for None)."""
    return "" if value is None else f"{value:.2f}"
