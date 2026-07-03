"""Tenant-scoped import preview + apply endpoints.

Provides a safe first-pass import flow for tenant data without real
Google Sheets API fetch. The tenant pastes TSV/CSV text, gets a
preview classifying rows into create/update/unchanged/invalid, and
then optionally applies the valid rows.

Current subsets: **Services**, **Barbers**, and **Schedules**.
"""

from __future__ import annotations

from datetime import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.api.src.deps import (
    get_barber_repo,
    get_schedule_repo,
    get_service_repo,
    get_session,
    get_tenant_principal,
    require_tenant,
)
from apps.api.src.schemas import (
    BarberImportApplyRequest,
    BarberImportApplyResult,
    BarberImportPreviewRequest,
    BarberImportPreviewResponse,
    BarberImportRow,
    BarberImportRowPreview,
    ScheduleImportApplyRequest,
    ScheduleImportApplyResult,
    ScheduleImportPreviewRequest,
    ScheduleImportPreviewResponse,
    ScheduleImportRow,
    ScheduleImportRowPreview,
    ServiceImportApplyRequest,
    ServiceImportApplyResult,
    ServiceImportPreviewRequest,
    ServiceImportPreviewResponse,
    ServiceImportRow,
    ServiceImportRowPreview,
)
from packages.application.scheduling.import_service import (
    ServiceImportResult,
    apply_import,
    classify_rows,
    parse_pasted_content,
)
from packages.application.scheduling.schedule_import_service import (
    ParsedScheduleRow,
    ScheduleImportResult,
    apply_import as schedule_apply_import,
    classify_rows as schedule_classify_rows,
    normalize_row as schedule_normalize_row,
    parse_pasted_content as schedule_parse_pasted_content,
    validate_row as schedule_validate_row,
)
from packages.infrastructure.repositories import (
    BarberRepository,
    ScheduleRepository,
    ServiceRepository,
    TenantAuditLogRepository,
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/import",
    tags=["import"],
)


def _resolve_actor_id(principal) -> str | None:
    return str(getattr(principal, "user_id", "") or "")


def _build_preview_row(
    idx: int, row_data: dict, classification: str, reason: str
) -> ServiceImportRowPreview:
    return ServiceImportRowPreview(
        row_index=idx,
        classification=classification,
        reason=reason,
        row=ServiceImportRow(
            code=row_data["code"],
            name=row_data["name"],
            duration_minutes=row_data["duration_minutes"],
            price_cents=row_data.get("price_cents", 0),
            description=row_data.get("description"),
        ),
    )


@router.post(
    "/services/preview",
    response_model=ServiceImportPreviewResponse,
)
def preview_services_import(
    payload: ServiceImportPreviewRequest,
    tenant_id: UUID = Depends(require_tenant),
    repo: ServiceRepository = Depends(get_service_repo),
    _principal=Depends(get_tenant_principal),
) -> ServiceImportPreviewResponse:
    """Preview a pasted sheet for services.

    Parses the TSV/CSV content, classifies each row against existing
    services for this tenant, and returns categorized preview lists.
    No data is modified.
    """
    try:
        parsed_rows = parse_pasted_content(payload.content, payload.delimiter)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse content: {exc}",
        ) from exc

    if not parsed_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No rows found in the pasted content. Ensure the header row is present.",
        )

    existing = repo.list()
    create, update, unchanged, invalid = classify_rows(parsed_rows, existing)

    return ServiceImportPreviewResponse(
        total=len(parsed_rows),
        create=[_build_preview_row(idx, data, "create", "New service") for idx, _, data in create],
        update=[
            _build_preview_row(idx, data, "update", f"Service '{row.code}' has changes")
            for idx, row, data, _ in update
        ],
        unchanged=[
            ServiceImportRowPreview(
                row_index=idx,
                classification="unchanged",
                reason=reason,
                row=ServiceImportRow(
                    code=row.code,
                    name=row.name,
                    duration_minutes=int(row.duration_minutes) if row.duration_minutes else 30,
                    price_cents=int(row.price_cents) if row.price_cents else 0,
                    description=row.description or None,
                ),
            )
            for idx, row, reason in unchanged
        ],
        invalid=[
            ServiceImportRowPreview(
                row_index=idx,
                classification="invalid",
                reason=reason,
                row=ServiceImportRow(
                    code=row.code or "???",
                    name=row.name or "(missing name)",
                    duration_minutes=int(row.duration_minutes) if row.duration_minutes and row.duration_minutes.isdigit() else 30,
                    price_cents=int(row.price_cents) if row.price_cents and row.price_cents.isdigit() else 0,
                    description=row.description or None,
                ),
            )
            for idx, row, reason in invalid
        ],
    )


@router.post(
    "/services/apply",
    response_model=ServiceImportApplyResult,
)
def apply_services_import(
    payload: ServiceImportApplyRequest,
    tenant_id: UUID = Depends(require_tenant),
    repo: ServiceRepository = Depends(get_service_repo),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> ServiceImportApplyResult:
    """Apply previously previewed import rows.

    Creates new services and updates existing ones. Invalid rows from
    the preview must be excluded by the caller — the API only accepts
    create and update lists.
    """
    if not payload.create and not payload.update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to apply — provide at least one create or update row",
        )

    actor_id = _resolve_actor_id(principal)

    # Validate each row before applying
    from packages.application.scheduling.import_service import (
        ParsedRow,
        validate_row,
        normalize_row,
    )

    create_data: list[dict] = []
    for row in payload.create:
        parsed = ParsedRow(
            code=row.code,
            name=row.name,
            duration_minutes=str(row.duration_minutes),
            price_cents=str(row.price_cents),
            description=row.description or "",
        )
        errors = validate_row(parsed)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid create row for code '{row.code}': {'; '.join(errors)}",
            )
        create_data.append(normalize_row(parsed))

    # Fetch existing services by code for update matching
    existing_services = repo.list()
    existing_by_code: dict[str, "object"] = {}
    for svc in existing_services:
        if svc.code:
            existing_by_code[svc.code] = svc

    update_data: list[tuple[dict, "object"]] = []
    for row in payload.update:
        parsed = ParsedRow(
            code=row.code,
            name=row.name,
            duration_minutes=str(row.duration_minutes),
            price_cents=str(row.price_cents),
            description=row.description or "",
        )
        errors = validate_row(parsed)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid update row for code '{row.code}': {'; '.join(errors)}",
            )
        existing = existing_by_code.get(row.code)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update service '{row.code}': not found for this tenant",
            )
        update_data.append((normalize_row(parsed), existing))

    result = apply_import(tenant_id, create_data, update_data, repo)
    session.commit()

    # Audit log
    audit = TenantAuditLogRepository(session, tenant_id)
    details = {"created": result.created, "updated": result.updated, "errors": result.errors}
    audit.log(
        event_type="services_import_applied",
        level="warning" if result.errors else "info",
        message=f"Services import: {result.created} created, {result.updated} updated, {len(result.errors)} errors",
        actor_scope="tenant",
        actor_id=actor_id,
        details=details,
    )
    session.commit()

    return ServiceImportApplyResult(
        created=result.created,
        updated=result.updated,
        errors=result.errors,
    )


# ── Barber import ─────────────────────────────────────────────────────────


def _build_barber_preview_row(
    idx: int, row_data: dict, classification: str, reason: str
) -> BarberImportRowPreview:
    return BarberImportRowPreview(
        row_index=idx,
        classification=classification,
        reason=reason,
        row=BarberImportRow(
            name=row_data["name"],
            restrictions=row_data.get("restrictions"),
            is_active=row_data.get("is_active", True),
        ),
    )


@router.post(
    "/barbers/preview",
    response_model=BarberImportPreviewResponse,
)
def preview_barbers_import(
    payload: BarberImportPreviewRequest,
    tenant_id: UUID = Depends(require_tenant),
    repo: BarberRepository = Depends(get_barber_repo),
    _principal=Depends(get_tenant_principal),
) -> BarberImportPreviewResponse:
    """Preview a pasted sheet for barbers.

    Parses the TSV/CSV content, classifies each row against existing
    barbers for this tenant, and returns categorized preview lists.
    No data is modified.
    """
    from packages.application.scheduling.barber_import_service import (
        ParsedBarberRow,
        classify_rows,
        normalize_row,
        parse_pasted_content,
        validate_row,
    )

    try:
        parsed_rows = parse_pasted_content(payload.content, payload.delimiter)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse content: {exc}",
        ) from exc

    if not parsed_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No rows found in the pasted content. Ensure the header row is present.",
        )

    existing = repo.list()
    create, update, unchanged, invalid = classify_rows(parsed_rows, existing)

    return BarberImportPreviewResponse(
        total=len(parsed_rows),
        create=[
            _build_barber_preview_row(idx, data, "create", "New barber")
            for idx, _, data in create
        ],
        update=[
            _build_barber_preview_row(
                idx, data, "update", f"Barber '{row.name}' has changes"
            )
            for idx, row, data, _ in update
        ],
        unchanged=[
            BarberImportRowPreview(
                row_index=idx,
                classification="unchanged",
                reason=reason,
                row=BarberImportRow(
                    name=row.name,
                    restrictions=row.restrictions or None,
                    is_active=True,
                ),
            )
            for idx, row, reason in unchanged
        ],
        invalid=[
            BarberImportRowPreview(
                row_index=idx,
                classification="invalid",
                reason=reason,
                row=BarberImportRow(
                    name=(row.name or "(missing name)")[:120],
                    restrictions=row.restrictions[:64] if row.restrictions else None,
                    is_active=True,
                ),
            )
            for idx, row, reason in invalid
        ],
    )


@router.post(
    "/barbers/apply",
    response_model=BarberImportApplyResult,
)
def apply_barbers_import(
    payload: BarberImportApplyRequest,
    tenant_id: UUID = Depends(require_tenant),
    repo: BarberRepository = Depends(get_barber_repo),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> BarberImportApplyResult:
    """Apply previously previewed barber import rows.

    Creates new barbers and updates existing ones. Invalid rows from
    the preview must be excluded by the caller — the API only accepts
    create and update lists.
    """
    from packages.application.scheduling.barber_import_service import (
        ParsedBarberRow,
        apply_import,
        normalize_row,
        validate_row,
    )

    if not payload.create and not payload.update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to apply — provide at least one create or update row",
        )

    actor_id = _resolve_actor_id(principal)

    # Validate each row before applying
    create_data: list[dict] = []
    for row in payload.create:
        parsed = ParsedBarberRow(
            name=row.name,
            restrictions=row.restrictions or "",
            is_active="true" if row.is_active else "false",
        )
        errors = validate_row(parsed)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid create row for '{row.name}': {'; '.join(errors)}",
            )
        create_data.append(normalize_row(parsed))

    # Fetch existing barbers by name for update matching
    existing_barbers = repo.list()
    existing_by_name: dict[str, "object"] = {}
    for b in existing_barbers:
        if b.name:
            existing_by_name[b.name] = b

    update_data: list[tuple[dict, "object"]] = []
    for row in payload.update:
        parsed = ParsedBarberRow(
            name=row.name,
            restrictions=row.restrictions or "",
            is_active="true" if row.is_active else "false",
        )
        errors = validate_row(parsed)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid update row for '{row.name}': {'; '.join(errors)}",
            )
        existing = existing_by_name.get(row.name)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update barber '{row.name}': not found for this tenant",
            )
        update_data.append((normalize_row(parsed), existing))

    result = apply_import(tenant_id, create_data, update_data, repo)
    session.commit()

    # Audit log
    audit = TenantAuditLogRepository(session, tenant_id)
    details = {"created": result.created, "updated": result.updated, "errors": result.errors}
    audit.log(
        event_type="barbers_import_applied",
        level="warning" if result.errors else "info",
        message=f"Barbers import: {result.created} created, {result.updated} updated, {len(result.errors)} errors",
        actor_scope="tenant",
        actor_id=actor_id,
        details=details,
    )
    session.commit()

    return BarberImportApplyResult(
        created=result.created,
        updated=result.updated,
        errors=result.errors,
    )


# ── Schedule import ──────────────────────────────────────────────────────


def _safe_time(value: str | None, default: time = time(10, 0)) -> time:
    """Safely parse a time string; return ``default`` on failure."""
    if not value:
        return default
    try:
        return time.fromisoformat(value)
    except ValueError:
        return default


def _build_schedule_preview_row(
    idx: int, row_data: dict, classification: str, reason: str
) -> ScheduleImportRowPreview:
    return ScheduleImportRowPreview(
        row_index=idx,
        classification=classification,
        reason=reason,
        row=ScheduleImportRow(
            barber_name=row_data["barber_name"],
            weekday=row_data["weekday"],
            start_time=row_data["start_time"],
            end_time=row_data["end_time"],
        ),
    )


@router.post(
    "/schedules/preview",
    response_model=ScheduleImportPreviewResponse,
)
def preview_schedules_import(
    payload: ScheduleImportPreviewRequest,
    tenant_id: UUID = Depends(require_tenant),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    schedule_repo: ScheduleRepository = Depends(get_schedule_repo),
    _principal=Depends(get_tenant_principal),
) -> ScheduleImportPreviewResponse:
    """Preview a pasted sheet for schedules.

    Parses the TSV/CSV content, resolves barber names to existing barbers,
    classifies each row against existing schedules for this tenant, and
    returns categorized preview lists. No data is modified.
    """
    try:
        parsed_rows = schedule_parse_pasted_content(payload.content, payload.delimiter)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse content: {exc}",
        ) from exc

    if not parsed_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No rows found in the pasted content. Ensure the header row is present.",
        )

    existing_barbers = barber_repo.list()
    existing_schedules = schedule_repo.list()
    create, update, unchanged, invalid = schedule_classify_rows(
        parsed_rows, existing_barbers, existing_schedules
    )

    return ScheduleImportPreviewResponse(
        total=len(parsed_rows),
        create=[
            _build_schedule_preview_row(idx, data, "create", "New schedule entry")
            for idx, _, data in create
        ],
        update=[
            _build_schedule_preview_row(
                idx, data, "update", f"Schedule has changes (end_time differs)"
            )
            for idx, _, data, _ in update
        ],
        unchanged=[
            ScheduleImportRowPreview(
                row_index=idx,
                classification="unchanged",
                reason=reason,
                row=ScheduleImportRow(
                    barber_name=row.barber_name[:120],
                    weekday=row.weekday.lower()[:3],
                    start_time=_safe_time(row.start_time),
                    end_time=_safe_time(row.end_time),
                ),
            )
            for idx, row, reason in unchanged
        ],
        invalid=[
            ScheduleImportRowPreview(
                row_index=idx,
                classification="invalid",
                reason=reason,
                row=ScheduleImportRow(
                    barber_name=(row.barber_name or "(missing name)")[:120],
                    weekday=row.weekday.lower()[:3] if row.weekday else "mon",
                    start_time=_safe_time(row.start_time),
                    end_time=_safe_time(row.end_time),
                ),
            )
            for idx, row, reason in invalid
        ],
    )


@router.post(
    "/schedules/apply",
    response_model=ScheduleImportApplyResult,
)
def apply_schedules_import(
    payload: ScheduleImportApplyRequest,
    tenant_id: UUID = Depends(require_tenant),
    barber_repo: BarberRepository = Depends(get_barber_repo),
    schedule_repo: ScheduleRepository = Depends(get_schedule_repo),
    session: Session = Depends(get_session),
    principal=Depends(get_tenant_principal),
) -> ScheduleImportApplyResult:
    """Apply previously previewed schedule import rows.

    Creates new schedule entries and updates existing ones. Invalid rows
    from the preview must be excluded by the caller — the API only accepts
    create and update lists.
    """
    if not payload.create and not payload.update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to apply — provide at least one create or update row",
        )

    actor_id = _resolve_actor_id(principal)

    # Build barber_by_name lookup for validation + resolving
    existing_barbers = barber_repo.list()
    barber_by_name: dict[str, "object"] = {}
    for b in existing_barbers:
        if b.name:
            barber_by_name[b.name] = b

    # Validate and process create rows
    create_data: list[dict] = []
    for row in payload.create:
        parsed = ParsedScheduleRow(
            barber_name=row.barber_name,
            weekday=row.weekday,
            start_time=row.start_time.isoformat(),
            end_time=row.end_time.isoformat(),
        )
        errors = schedule_validate_row(parsed, barber_by_name)  # type: ignore[arg-type]
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid create row for barber '{row.barber_name}': {'; '.join(errors)}",
            )
        normalized = schedule_normalize_row(parsed)
        normalized["barber_id"] = barber_by_name[row.barber_name].id  # type: ignore[attr]
        create_data.append(normalized)

    # Build existing schedule lookup for update matching
    existing_schedules = schedule_repo.list()
    schedule_by_key: dict[tuple, "object"] = {}
    for s in existing_schedules:
        schedule_by_key[(s.barber_id, s.weekday, s.start_time)] = s

    update_data: list[tuple[dict, "object"]] = []
    for row in payload.update:
        parsed = ParsedScheduleRow(
            barber_name=row.barber_name,
            weekday=row.weekday,
            start_time=row.start_time.isoformat(),
            end_time=row.end_time.isoformat(),
        )
        errors = schedule_validate_row(parsed, barber_by_name)  # type: ignore[arg-type]
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid update row for barber '{row.barber_name}': {'; '.join(errors)}",
            )
        normalized = schedule_normalize_row(parsed)
        barber = barber_by_name.get(row.barber_name)  # type: ignore[arg-type]
        if barber is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update schedule for barber '{row.barber_name}': barber not found",
            )
        key = (barber.id, normalized["weekday"], normalized["start_time"])
        existing = schedule_by_key.get(key)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot update schedule for barber '{row.barber_name}' "
                    f"on {row.weekday} at {row.start_time}: entry not found"
                ),
            )
        normalized["barber_id"] = barber.id
        update_data.append((normalized, existing))

    result = schedule_apply_import(tenant_id, create_data, update_data, schedule_repo)
    session.commit()

    # Audit log
    audit = TenantAuditLogRepository(session, tenant_id)
    details = {"created": result.created, "updated": result.updated, "errors": result.errors}
    audit.log(
        event_type="schedules_import_applied",
        level="warning" if result.errors else "info",
        message=f"Schedules import: {result.created} created, {result.updated} updated, {len(result.errors)} errors",
        actor_scope="tenant",
        actor_id=actor_id,
        details=details,
    )
    session.commit()

    return ScheduleImportApplyResult(
        created=result.created,
        updated=result.updated,
        errors=result.errors,
    )
