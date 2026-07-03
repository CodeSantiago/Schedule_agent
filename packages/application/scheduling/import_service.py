"""Services import preview + apply logic.

Parses pasted TSV/CSV text into structured rows, classifies them against
existing services for a tenant, and applies creates/updates in bulk.

This is deliberately narrow — it only handles services (the first import
subset). No generic import engine, no background jobs, no real Sheets fetch.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from packages.infrastructure.db.models.scheduling import Service
from packages.infrastructure.repositories import ServiceRepository

EXPECTED_HEADER = frozenset({
    "code",
    "name",
    "duration_minutes",
    "price_cents",
    "description",
})

REQUIRED_FIELDS = frozenset({"code", "name", "duration_minutes"})


@dataclass(frozen=True)
class ParsedRow:
    """One parsed row with raw values before validation."""

    code: str
    name: str
    duration_minutes: str
    price_cents: str
    description: str


@dataclass(frozen=True)
class Classification:
    """Result of classifying one row against existing services."""

    classification: str  # "create" | "update" | "unchanged" | "invalid"
    reason: str


def parse_pasted_content(content: str, delimiter: str = "\t") -> list[ParsedRow]:
    """Parse TSV/CSV text into rows.

    Expects a header row as the first line. Skips blank lines.
    Returns the parsed rows (header excluded) for validation.
    """
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    rows: list[ParsedRow] = []

    for lineno, row_dict in enumerate(reader, start=2):  # 1-based, header=1
        code = (row_dict.get("code") or "").strip()
        name = (row_dict.get("name") or "").strip()
        duration = (row_dict.get("duration_minutes") or "").strip()
        price = (row_dict.get("price_cents") or "").strip()
        desc = (row_dict.get("description") or "").strip()

        # Skip fully blank lines
        if not code and not name:
            continue

        rows.append(ParsedRow(
            code=code,
            name=name,
            duration_minutes=duration,
            price_cents=price,
            description=desc,
        ))

    return rows


def validate_row(row: ParsedRow) -> list[str]:
    """Validate a parsed row. Returns a list of error messages (empty = valid)."""
    errors: list[str] = []

    if not row.code:
        errors.append("code is required")
    elif len(row.code) > 8:
        errors.append("code must be 8 characters or fewer")

    if not row.name:
        errors.append("name is required")
    elif len(row.name) > 120:
        errors.append("name must be 120 characters or fewer")

    if not row.duration_minutes:
        errors.append("duration_minutes is required")
    else:
        try:
            d = int(row.duration_minutes)
            if d < 15 or d > 480:
                errors.append("duration_minutes must be between 15 and 480")
        except ValueError:
            errors.append("duration_minutes must be a valid integer")

    if row.price_cents:
        try:
            p = int(row.price_cents)
            if p < 0:
                errors.append("price_cents must be a non-negative integer")
        except ValueError:
            errors.append("price_cents must be a valid integer")

    if row.description and len(row.description) > 500:
        errors.append("description must be 500 characters or fewer")

    return errors


def normalize_row(row: ParsedRow) -> dict[str, Any]:
    """Convert a ParsedRow to a dict for Service creation/update."""
    return {
        "code": row.code,
        "name": row.name,
        "duration_minutes": int(row.duration_minutes),
        "price_cents": int(row.price_cents) if row.price_cents else 0,
        "description": row.description or None,
    }


def classify_rows(
    parsed_rows: list[ParsedRow],
    existing_services: list[Service],
) -> tuple[
    list[tuple[int, ParsedRow, dict[str, Any]]],   # create
    list[tuple[int, ParsedRow, dict[str, Any], Service]],  # update
    list[tuple[int, ParsedRow, str]],               # unchanged
    list[tuple[int, ParsedRow, str]],               # invalid
]:
    """Classify parsed rows against existing services.

    Returns (create, update, unchanged, invalid) tuples.
    Each entry includes the (row_index, original_row, normalized_dict_or_reason).
    """
    existing_by_code: dict[str, Service] = {}
    for svc in existing_services:
        if svc.code:
            existing_by_code[svc.code] = svc

    create: list[tuple[int, ParsedRow, dict[str, Any]]] = []
    update: list[tuple[int, ParsedRow, dict[str, Any], Service]] = []
    unchanged: list[tuple[int, ParsedRow, str]] = []
    invalid: list[tuple[int, ParsedRow, str]] = []

    for idx, row in enumerate(parsed_rows):
        errors = validate_row(row)
        if errors:
            invalid.append((idx, row, "; ".join(errors)))
            continue

        normalized = normalize_row(row)
        existing = existing_by_code.get(row.code)

        if existing is None:
            create.append((idx, row, normalized))
        else:
            # Compare fields to determine update vs unchanged
            has_diff = (
                existing.name != normalized["name"]
                or existing.duration_minutes != normalized["duration_minutes"]
                or existing.price_cents != normalized["price_cents"]
                or (existing.description or "") != (normalized["description"] or "")
            )
            if has_diff:
                update.append((idx, row, normalized, existing))
            else:
                unchanged.append((idx, row, f"Service '{row.code}' already matches"))

    return create, update, unchanged, invalid


def apply_import(
    tenant_id: UUID,
    create_rows: list[dict[str, Any]],
    update_rows: list[tuple[dict[str, Any], Service]],
    service_repo: ServiceRepository,
) -> ServiceImportResult:
    """Apply import rows: create new services and update existing ones.

    Returns a result summary.
    """
    created_count = 0
    updated_count = 0
    errors: list[str] = []

    for row_data in create_rows:
        try:
            service = Service(
                id=uuid4(),
                tenant_id=tenant_id,
                name=row_data["name"],
                code=row_data["code"],
                duration_minutes=row_data["duration_minutes"],
                price_cents=row_data["price_cents"],
                description=row_data.get("description"),
                is_active=True,
            )
            service_repo.add(service)
            created_count += 1
        except Exception as exc:
            errors.append(f"Failed to create '{row_data.get('code', '?')}': {exc}")

    for row_data, existing in update_rows:
        try:
            existing.name = row_data["name"]
            existing.code = row_data["code"]
            existing.duration_minutes = row_data["duration_minutes"]
            existing.price_cents = row_data["price_cents"]
            existing.description = row_data.get("description")
            # Keep is_active as-is (don't toggle via import)
            updated_count += 1
        except Exception as exc:
            errors.append(f"Failed to update '{row_data.get('code', '?')}': {exc}")

    return ServiceImportResult(
        created=created_count,
        updated=updated_count,
        errors=errors,
    )


@dataclass
class ServiceImportResult:
    created: int
    updated: int
    errors: list[str]
