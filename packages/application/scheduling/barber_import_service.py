"""Barber import preview + apply logic.

Parses pasted TSV/CSV text into structured rows, classifies them against
existing barbers for a tenant, and applies creates/updates in bulk.

Mirrors the services import pattern (import_service.py). Matching is
by **name** — the only stable field in the barber model.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from packages.infrastructure.db.models.scheduling import Barber
from packages.infrastructure.repositories import BarberRepository

EXPECTED_HEADER = frozenset({
    "name",
    "restrictions",
    "is_active",
})

REQUIRED_FIELDS = frozenset({"name"})


@dataclass(frozen=True)
class ParsedBarberRow:
    """One parsed row with raw values before validation."""

    name: str
    restrictions: str
    is_active: str


def parse_pasted_content(content: str, delimiter: str = "\t") -> list[ParsedBarberRow]:
    """Parse TSV/CSV text into rows.

    Expects a header row as the first line. Skips blank lines.
    Returns the parsed rows (header excluded) for validation.
    """
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    rows: list[ParsedBarberRow] = []

    for row_dict in reader:
        name = (row_dict.get("name") or "").strip()
        restrictions = (row_dict.get("restrictions") or "").strip()
        is_active = (row_dict.get("is_active") or "").strip()

        # Skip fully blank lines
        if not name:
            continue

        rows.append(ParsedBarberRow(
            name=name,
            restrictions=restrictions,
            is_active=is_active,
        ))

    return rows


def validate_row(row: ParsedBarberRow) -> list[str]:
    """Validate a parsed row. Returns a list of error messages (empty = valid)."""
    errors: list[str] = []

    if not row.name:
        errors.append("name is required")
    elif len(row.name) > 120:
        errors.append("name must be 120 characters or fewer")

    if row.restrictions and len(row.restrictions) > 64:
        errors.append("restrictions must be 64 characters or fewer")

    if row.is_active:
        if row.is_active.lower() not in ("true", "false", "1", "0", "yes", "no", ""):
            errors.append("is_active must be true/false, 1/0, or yes/no")

    return errors


def normalize_row(row: ParsedBarberRow) -> dict[str, Any]:
    """Convert a ParsedBarberRow to a dict for Barber creation/update."""
    is_active = True
    if row.is_active:
        is_active = row.is_active.lower() in ("true", "1", "yes")

    return {
        "name": row.name,
        "restrictions": row.restrictions or None,
        "is_active": is_active,
    }


def classify_rows(
    parsed_rows: list[ParsedBarberRow],
    existing_barbers: list[Barber],
) -> tuple[
    list[tuple[int, ParsedBarberRow, dict[str, Any]]],          # create
    list[tuple[int, ParsedBarberRow, dict[str, Any], Barber]],  # update
    list[tuple[int, ParsedBarberRow, str]],                      # unchanged
    list[tuple[int, ParsedBarberRow, str]],                      # invalid
]:
    """Classify parsed rows against existing barbers.

    Matching is by **name** (the only stable field in the barber model).
    Returns (create, update, unchanged, invalid) tuples.
    Each entry includes the (row_index, original_row, normalized_dict_or_reason).
    """
    existing_by_name: dict[str, Barber] = {}
    for b in existing_barbers:
        if b.name:
            existing_by_name[b.name] = b

    create: list[tuple[int, ParsedBarberRow, dict[str, Any]]] = []
    update: list[tuple[int, ParsedBarberRow, dict[str, Any], Barber]] = []
    unchanged: list[tuple[int, ParsedBarberRow, str]] = []
    invalid: list[tuple[int, ParsedBarberRow, str]] = []

    for idx, row in enumerate(parsed_rows):
        errors = validate_row(row)
        if errors:
            invalid.append((idx, row, "; ".join(errors)))
            continue

        normalized = normalize_row(row)
        existing = existing_by_name.get(row.name)

        if existing is None:
            create.append((idx, row, normalized))
        else:
            # Compare fields to determine update vs unchanged
            has_diff = (
                existing.restrictions or ""
            ) != (normalized["restrictions"] or "") or (
                existing.is_active != normalized["is_active"]
            )
            if has_diff:
                update.append((idx, row, normalized, existing))
            else:
                unchanged.append(
                    (idx, row, f"Barber '{row.name}' already matches")
                )

    return create, update, unchanged, invalid


def apply_import(
    tenant_id: UUID,
    create_rows: list[dict[str, Any]],
    update_rows: list[tuple[dict[str, Any], Barber]],
    barber_repo: BarberRepository,
) -> BarberImportResult:
    """Apply import rows: create new barbers and update existing ones.

    Returns a result summary.
    """
    created_count = 0
    updated_count = 0
    errors: list[str] = []

    for row_data in create_rows:
        try:
            barber = Barber(
                id=uuid4(),
                tenant_id=tenant_id,
                name=row_data["name"],
                restrictions=row_data.get("restrictions"),
                is_active=row_data.get("is_active", True),
            )
            barber_repo.add(barber)
            created_count += 1
        except Exception as exc:
            errors.append(f"Failed to create '{row_data.get('name', '?')}': {exc}")

    for row_data, existing in update_rows:
        try:
            existing.name = row_data["name"]
            existing.restrictions = row_data.get("restrictions")
            existing.is_active = row_data.get("is_active", True)
            updated_count += 1
        except Exception as exc:
            errors.append(f"Failed to update '{row_data.get('name', '?')}': {exc}")

    return BarberImportResult(
        created=created_count,
        updated=updated_count,
        errors=errors,
    )


@dataclass
class BarberImportResult:
    created: int
    updated: int
    errors: list[str]
