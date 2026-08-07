"""CSV / XLSX row helpers (shared by import views)."""

from __future__ import annotations

from datetime import date, datetime


def row_raw(d, *keys):
    """Return the first non-None value among ``keys`` from dict ``d``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def row_val(d, *keys):
    """Return the first non-empty trimmed string value among ``keys``.

    Treats the literal string ``"none"`` (case-insensitive) as empty.
    """
    for k in keys:
        if k in d and d[k] is not None:
            v = str(d[k]).strip()
            if v != "" and v.lower() != "none":
                return v
    return None


def row_val_bool(d, *keys):
    """Parse a boolean from common truthy/falsy spellings; None if absent/unknown."""
    v = row_val(d, *keys)
    if v is None:
        return None
    s = v.strip().lower()
    if s in ("y", "yes", "true", "t", "1"):
        return True
    if s in ("n", "no", "false", "f", "0"):
        return False
    return None


def row_val_date(d, *keys):
    """Parse a date from a date/datetime object or common string formats."""
    rv = row_raw(d, *keys)
    if isinstance(rv, datetime):
        return rv.date()
    if isinstance(rv, date):
        return rv
    v = row_val(d, *keys)
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def get_academic_year_ending(today: date = None) -> int:
    """Return the academic year ending (e.g., 2025 for 2024-25 school year).
    July 1 rollover:
    - before July 1: academic year ending = current year
    - on/after July 1: academic year ending = next year
    """
    if today is None:
        today = date.today()
    if today.month < 7:
        return today.year
    else:
        return today.year + 1
