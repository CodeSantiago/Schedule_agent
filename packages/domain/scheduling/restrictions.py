"""Haircut-only restriction parsing and evaluation.

Legacy format (from `solo-tenant-bot/api.py`):

    SOLO_CORTE = {
        "O": ["11:30", "19:30"],
        "R": ["15:00", "19:00"],
        "A": ["11:30", "19:30"],
    }

That is a per-barber dict of weekday-codes -> list of "HH:MM" times at which
the barber ONLY accepts a haircut (C) — any other service at those times must
be rejected.

In the new platform we keep the same rule, but encode it on the `barbers`
row in a single `restrictions` text column. The format we use is a compact
semicolon-separated list:

    "mon:11:30,19:30;fri:15:00,19:00"

(weekday codes are the same `mon..sun` codes used everywhere else; times
must be on the 30-minute slot grid; empty string = no restrictions.)

This module parses that string into a Python mapping and answers
"is this (weekday, time) a haircut-only slot?" deterministically.
"""

from __future__ import annotations

from datetime import time

from packages.domain.scheduling.models import ServiceCode, TimeGrid

from .errors import ServiceRestrictionError


def parse_haircut_only(restrictions: str | None) -> dict[str, list[time]]:
    """Parse a `barbers.restrictions` string into `{weekday: [time, ...]}`.

    Returns an empty dict when `restrictions` is None or empty. Raises
    `ValueError` for malformed entries so bad tenant data fails loudly
    instead of silently dropping the rule.
    """
    if not restrictions:
        return {}

    out: dict[str, list[time]] = {}
    for chunk in restrictions.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"bad haircut-only chunk {chunk!r}: expected 'weekday:HH:MM,...'")
        weekday, _, times_csv = chunk.partition(":")
        weekday = weekday.strip().lower()
        if len(weekday) != 3 or weekday not in {
            "mon", "tue", "wed", "thu", "fri", "sat", "sun"
        }:
            raise ValueError(f"bad weekday {weekday!r} in haircut-only chunk {chunk!r}")
        times: list[time] = []
        for hhmm in times_csv.split(","):
            hhmm = hhmm.strip()
            if not hhmm:
                continue
            try:
                hour_str, minute_str = hhmm.split(":")
                t = time(int(hour_str), int(minute_str))
            except (ValueError, AttributeError) as exc:
                raise ValueError(
                    f"bad time {hhmm!r} in haircut-only chunk {chunk!r}"
                ) from exc
            if not TimeGrid.is_aligned_to_grid(t):
                raise ValueError(
                    f"time {hhmm!r} is not on the 30-minute slot grid"
                )
            times.append(t)
        if times:
            out[weekday] = times
    return out


def is_haircut_only_slot(
    restrictions: str | None,
    weekday: str,
    slot_start: time,
) -> bool:
    """True when (weekday, slot_start) is a haircut-only slot for the barber."""
    parsed = parse_haircut_only(restrictions)
    return slot_start in parsed.get(weekday, [])


def enforce_haircut_only(
    service: ServiceCode,
    restrictions: str | None,
    weekday: str,
    slot_start: time,
) -> None:
    """Raise `ServiceRestrictionError` if the service is not allowed at that
    slot per the barber's haircut-only rules.

    HAIRCUT is always allowed. HAIRCUT_AND_BEARD is rejected (its first half
    would be a non-haircut service at a restricted slot). BEARD and OTHER are
    also rejected. This matches the legacy rule: a barber marked as
    "haircut-only" at certain times only accepts haircuts there.
    """
    if service is ServiceCode.HAIRCUT:
        return  # always allowed
    if is_haircut_only_slot(restrictions, weekday, slot_start):
        raise ServiceRestrictionError(
            f"Service {service.value!r} not allowed at {slot_start} on {weekday}: "
            f"this barber only accepts haircuts at that slot."
        )
