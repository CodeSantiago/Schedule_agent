"""Schedule import preview + apply logic.

Parses pasted TSV/CSV text into structured rows, resolves barber names
to existing barbers for the tenant, classifies each row against existing
schedules, and applies creates/updates in bulk.

Matching rule: **barber_name + weekday + start_time** identifies a schedule
slot (the DB unique constraint is ``(barber_id, weekday, start_time)``).
For updates, only ``end_time`` is mutable — the identity fields stay fixed.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import time
from typing import Any
from uuid import UUID, uuid4

from packages.domain.scheduling.models import TimeGrid, WEEKDAY_CODES
from packages.infrastructure.db.models.scheduling import Barber, BarberSchedule
from packages.infrastructure.repositories import ScheduleRepository

EXPECTED_HEADER = frozenset({
    "barber_name",
    "weekday",
    "start_time",
    "end_time",
})

REQUIRED_FIELDS = frozenset({"barber_name", "weekday", "start_time", "end_time"})


@dataclass(frozen=True)
class ParsedScheduleRow:
    """One parsed row with raw string values before validation."""

    barber_name: str
    weekday: str
    start_time: str
    end_time: str


def parse_pasted_content(content: str, delimiter: str = "\t") -> list[ParsedScheduleRow]:
    """Parse TSV/CSV text into rows.

    Expects a header row as the first line. Skips blank lines.
    Returns the parsed rows (header excluded) for validation.
    """
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    rows: list[ParsedScheduleRow] = []

    for row_dict in reader:
        barber_name = (row_dict.get("barber_name") or "").strip()
        weekday = (row_dict.get("weekday") or "").strip()
        start_time = (row_dict.get("start_time") or "").strip()
        end_time = (row_dict.get("end_time") or "").strip()

        # Skip fully blank lines
        if not barber_name:
            continue

        rows.append(ParsedScheduleRow(
            barber_name=barber_name,
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
        ))

    return rows


def validate_row(
    row: ParsedScheduleRow,
    barber_by_name: dict[str, Barber],
) -> list[str]:
    """Validate a parsed row. Returns a list of error messages (empty = valid)."""
    errors: list[str] = []

    # barber_name
    if not row.barber_name:
        errors.append("barber_name is required")
    elif len(row.barber_name) > 120:
        errors.append("barber_name must be 120 characters or fewer")
    elif row.barber_name not in barber_by_name:
        errors.append(f"Barber '{row.barber_name}' not found for this tenant")

    # weekday
    if not row.weekday:
        errors.append("weekday is required")
    elif row.weekday.lower() not in WEEKDAY_CODES:
        codes_csv = ",".join(WEEKDAY_CODES)
        errors.append(f"Invalid weekday '{row.weekday}'. Must be one of: {codes_csv}")

    # start_time
    start: time | None = None
    if not row.start_time:
        errors.append("start_time is required")
    else:
        try:
            start = time.fromisoformat(row.start_time)
            if not TimeGrid.is_aligned_to_grid(start):
                errors.append(
                    f"start_time {row.start_time} is not on the 30-minute grid "
                    "(use hh:00 or hh:30)"
                )
        except ValueError:
            errors.append(f"start_time '{row.start_time}' is not a valid time (use HH:MM)")

    # end_time
    end: time | None = None
    if not row.end_time:
        errors.append("end_time is required")
    else:
        try:
            end = time.fromisoformat(row.end_time)
            if not TimeGrid.is_aligned_to_grid(end):
                errors.append(
                    f"end_time {row.end_time} is not on the 30-minute grid "
                    "(use hh:00 or hh:30)"
                )
        except ValueError:
            errors.append(f"end_time '{row.end_time}' is not a valid time (use HH:MM)")

    # Cross-field: end > start (only if both parseable)
    if not errors and start is not None and end is not None and end <= start:
        errors.append(
            f"end_time {row.end_time} must be after start_time {row.start_time}"
        )

    return errors


def normalize_row(row: ParsedScheduleRow) -> dict[str, Any]:
    """Convert a ParsedScheduleRow to a dict for BarberSchedule creation/update."""
    return {
        "barber_name": row.barber_name,
        "weekday": row.weekday.lower(),
        "start_time": time.fromisoformat(row.start_time),
        "end_time": time.fromisoformat(row.end_time),
    }


def classify_rows(
    parsed_rows: list[ParsedScheduleRow],
    existing_barbers: list[Barber],
    existing_schedules: list[BarberSchedule],
) -> tuple[
    list[tuple[int, ParsedScheduleRow, dict[str, Any]]],                 # create
    list[tuple[int, ParsedScheduleRow, dict[str, Any], BarberSchedule]],  # update
    list[tuple[int, ParsedScheduleRow, str]],                             # unchanged
    list[tuple[int, ParsedScheduleRow, str]],                             # invalid
]:
    """Classify parsed rows against existing barbers and schedules.

    Matching is by **barber_name + weekday + start_time** (the DB unique
    constraint). For updates, only ``end_time`` is compared — identity
    fields stay fixed.

    Returns (create, update, unchanged, invalid) tuples.
    Each entry includes the (row_index, original_row, normalized_dict_or_reason).
    """
    barber_by_name: dict[str, Barber] = {}
    for b in existing_barbers:
        if b.name:
            barber_by_name[b.name] = b

    # Build lookup: (barber_id, weekday, start_time) -> BarberSchedule
    schedule_by_key: dict[tuple[UUID | object, str, time], BarberSchedule] = {}
    for s in existing_schedules:
        schedule_by_key[(s.barber_id, s.weekday, s.start_time)] = s

    create: list[tuple[int, ParsedScheduleRow, dict[str, Any]]] = []
    update: list[tuple[int, ParsedScheduleRow, dict[str, Any], BarberSchedule]] = []
    unchanged: list[tuple[int, ParsedScheduleRow, str]] = []
    invalid: list[tuple[int, ParsedScheduleRow, str]] = []

    for idx, row in enumerate(parsed_rows):
        errors = validate_row(row, barber_by_name)
        if errors:
            invalid.append((idx, row, "; ".join(errors)))
            continue

        normalized = normalize_row(row)
        barber = barber_by_name[row.barber_name]
        normalized["barber_id"] = barber.id

        key = (barber.id, normalized["weekday"], normalized["start_time"])
        existing = schedule_by_key.get(key)

        if existing is None:
            create.append((idx, row, normalized))
        else:
            # Compare end_time to determine update vs unchanged
            if existing.end_time != normalized["end_time"]:
                update.append((
                    idx, row, normalized, existing
                ))
            else:
                unchanged.append((
                    idx,
                    row,
                    f"Schedule for barber '{row.barber_name}' on {row.weekday} "
                    f"at {row.start_time} already matches",
                ))

    return create, update, unchanged, invalid


def apply_import(
    tenant_id: UUID,
    create_rows: list[dict[str, Any]],
    update_rows: list[tuple[dict[str, Any], BarberSchedule]],
    schedule_repo: ScheduleRepository,
) -> ScheduleImportResult:
    """Apply import rows: create new schedule entries and update existing ones.

    Returns a result summary.
    """
    created_count = 0
    updated_count = 0
    errors: list[str] = []

    for row_data in create_rows:
        try:
            schedule = BarberSchedule(
                id=uuid4(),
                barber_id=row_data["barber_id"],
                weekday=row_data["weekday"],
                start_time=row_data["start_time"],
                end_time=row_data["end_time"],
            )
            schedule_repo.add(schedule)
            created_count += 1
        except Exception as exc:
            barber_name = row_data.get("barber_name", "?")
            errors.append(
                f"Failed to create schedule for barber '{barber_name}' "
                f"on {row_data.get('weekday', '?')} at {row_data.get('start_time', '?')}: {exc}"
            )

    for row_data, existing in update_rows:
        try:
            existing.end_time = row_data["end_time"]
            updated_count += 1
        except Exception as exc:
            barber_name = row_data.get("barber_name", "?")
            errors.append(
                f"Failed to update schedule for barber '{barber_name}' "
                f"on {row_data.get('weekday', '?')} at {row_data.get('start_time', '?')}: {exc}"
            )

    return ScheduleImportResult(
        created=created_count,
        updated=updated_count,
        errors=errors,
    )


@dataclass
class ScheduleImportResult:
    created: int
    updated: int
    errors: list[str]
