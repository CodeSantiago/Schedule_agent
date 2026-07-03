"""Pydantic request/response models for the API.

Kept separate from the SQLAlchemy models so the API surface can evolve
without forcing schema migrations, and so request validation is one
place to look.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# --- Tenant ----------------------------------------------------------------


class TenantCreate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    slug: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)] = "UTC"


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    status: str
    timezone: str


# --- Barber ----------------------------------------------------------------


class BarberCreate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    restrictions: Annotated[str, StringConstraints(max_length=64)] | None = None
    is_active: bool = True


class BarberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    restrictions: str | None
    is_active: bool


# --- Service ---------------------------------------------------------------


class ServiceCreate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    code: Annotated[str, StringConstraints(min_length=1, max_length=8)] = "OTHER"
    duration_minutes: Annotated[int, Field(ge=15, le=480)]
    price_cents: Annotated[int, Field(ge=0)] = 0
    description: str | None = None
    is_active: bool = True


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    duration_minutes: int
    price_cents: int
    is_active: bool


# --- Schedule --------------------------------------------------------------


class ScheduleCreate(BaseModel):
    weekday: Annotated[str, StringConstraints(min_length=3, max_length=3)]
    start_time: time
    end_time: time


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    weekday: str
    start_time: time
    end_time: time


# --- Absence / extra hours -------------------------------------------------


class AbsenceCreate(BaseModel):
    absence_date: date
    start_time: time | None = None
    end_time: time | None = None
    reason: Annotated[str, StringConstraints(max_length=120)] | None = None


class AbsenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    absence_date: date
    start_time: time | None
    end_time: time | None
    reason: str | None


class ExtraHourCreate(BaseModel):
    extra_date: date
    start_time: time
    end_time: time
    reason: Annotated[str, StringConstraints(max_length=120)] | None = None


class ExtraHourOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    extra_date: date
    start_time: time
    end_time: time
    reason: str | None


# --- Appointment / availability -------------------------------------------


class AppointmentCreate(BaseModel):
    barber_id: UUID
    service_id: UUID
    start_at: datetime
    customer_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    customer_phone: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    # Configurable identity fields (optional, driven by tenant identity_mode)
    customer_last_name: Annotated[str, StringConstraints(max_length=120)] | None = None
    customer_dni: Annotated[str, StringConstraints(max_length=32)] | None = None
    notes: Annotated[str, StringConstraints(max_length=500)] | None = None


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    barber_id: UUID
    service_id: UUID
    appointment_date: date
    start_time: datetime
    end_time: datetime
    status: str
    customer_name: str
    customer_phone: str
    customer_last_name: str | None = None
    customer_dni: str | None = None
    notes: str | None


class BookingResult(BaseModel):
    """Result of a successful booking. `continuation` is set for CB."""

    appointment: AppointmentOut
    continuation: AppointmentOut | None = None


class RescheduleRequest(BaseModel):
    """Body of POST /appointments/{id}/reschedule.

    `new_start_at` is a tenant-local wall-clock datetime; the API does
    not do timezone conversion — the dashboard submits the same value
    it would submit for a fresh booking.
    """

    new_start_at: datetime


class CancelOut(BaseModel):
    """Response shape for a cancel.

    `continuation` is the cancelled CB partner (when the target was a
    CB primary). `None` for single-slot services.
    """

    appointment: AppointmentOut
    continuation: AppointmentOut | None = None


class RescheduleOut(BaseModel):
    """Response shape for a reschedule.

    Mirrors `BookingResult` so the dashboard can treat the two
    operations uniformly.
    """

    appointment: AppointmentOut
    continuation: AppointmentOut | None = None


# --- Operational overview (dashboard) ------------------------------------


class OverviewCountsOut(BaseModel):
    booked_today: int
    cancelled_today: int
    completed_today: int
    pending_today: int
    confirmed_today: int
    active_barbers: int
    active_services: int
    upcoming_days_with_bookings: int


class OverviewDayAppointmentOut(BaseModel):
    id: UUID
    barber_name: str
    service_name: str
    customer_name: str
    customer_phone: str
    start_time: str
    end_time: str
    status: str
    is_cb_continuation: bool


class OverviewOut(BaseModel):
    tenant_id: UUID
    date: str
    counts: OverviewCountsOut
    appointments: list[OverviewDayAppointmentOut]
    upcoming: dict[str, int]


class AvailabilitySlot(BaseModel):
    """One bookable starting slot."""

    date_: date = Field(alias="date")
    start_time: time
    end_time: time  # exclusive end of the FIRST slot only (CB continuation is implicit)

    model_config = ConfigDict(populate_by_name=True)


class AvailabilityOut(BaseModel):
    barber_id: UUID
    service_id: UUID
    date: date
    slots: list[AvailabilitySlot]


# --- CRUD Update schemas (all-fields-optional) ----------------------------


class BarberUpdate(BaseModel):
    """All fields optional — only provided fields are applied."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    restrictions: Annotated[str, StringConstraints(max_length=64)] | None = None
    is_active: bool | None = None


class ServiceUpdate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    code: Annotated[str, StringConstraints(min_length=1, max_length=8)] | None = None
    duration_minutes: Annotated[int, Field(ge=15, le=480)] | None = None
    price_cents: Annotated[int, Field(ge=0)] | None = None
    description: str | None = None
    is_active: bool | None = None


class ScheduleUpdate(BaseModel):
    weekday: Annotated[str, StringConstraints(min_length=3, max_length=3)] | None = None
    start_time: time | None = None
    end_time: time | None = None


class AbsenceUpdate(BaseModel):
    absence_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    reason: Annotated[str, StringConstraints(max_length=120)] | None = None


class ExtraHourUpdate(BaseModel):
    extra_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    reason: Annotated[str, StringConstraints(max_length=120)] | None = None


class StatusUpdateRequest(BaseModel):
    """Valid status values match the appointment_status DB enum."""

    status: Annotated[
        str,
        StringConstraints(pattern=r"^(pending|confirmed|cancelled|completed|no_show)$"),
    ]


# --- Auth / superadmin ----------------------------------------------------


class LoginRequest(BaseModel):
    email: Annotated[str, StringConstraints(min_length=3, max_length=255)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    label: Annotated[str, StringConstraints(max_length=120)] | None = None


class LoginResponse(BaseModel):
    token: str
    token_prefix: str
    principal_id: UUID
    email: str
    scope: str
    tenant_id: UUID | None = None
    role: str | None = None


class SuperadminTenantCreate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    slug: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)] = "UTC"
    status: Annotated[str, StringConstraints(min_length=1, max_length=32)] = "trial"
    initial_settings: dict | None = None


class SuperadminTenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    status: str
    timezone: str


class SuperadminTenantStatusUpdate(BaseModel):
    status: Annotated[str, StringConstraints(min_length=1, max_length=32)]


# --- Provider configs (per tenant) ----------------------------------------


class ProviderConfigCreate(BaseModel):
    kind: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    provider_name: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    credentials: dict | None = None
    settings: dict | None = None
    is_active: bool = True


class ProviderConfigUpdate(BaseModel):
    label: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    provider_name: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    credentials: dict | None = None
    settings: dict | None = None
    is_active: bool | None = None


class ProviderConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    label: str
    provider_name: str
    credentials: dict
    settings: dict
    is_active: bool


# --- Tenant operational settings (bot toggle + closed dates) ------------


class TenantOperationalSettingsOut(BaseModel):
    """Current operational settings for a tenant.

    `bot_enabled` controls whether the inbound bot processes messages.
    `closed_dates` lists dates (YYYY-MM-DD) where booking intake is blocked.
    """

    bot_enabled: bool
    closed_dates: list[str]


class TenantOperationalSettingsUpdate(BaseModel):
    """Partial update for operational settings. Only provided fields change."""

    bot_enabled: bool | None = None
    closed_dates: list[str] | None = None


# --- Tenant bot + business config -----------------------------------------


class TenantBotConfigOut(BaseModel):
    """Read-only view of the tenant's bot and business configuration.

    Every field has a sensible default so the frontend never renders
    empty/none gaps.
    """

    greeting_text: str
    behavior_notes: str
    display_name: str
    contact_phone: str
    booking_notes: str
    location: str       # business address
    hours: str          # business hours description (free text)


class TenantBotConfigUpdate(BaseModel):
    """Partial update for bot/business config. Only provided fields change."""

    greeting_text: str | None = None
    behavior_notes: str | None = None
    display_name: str | None = None
    contact_phone: str | None = None
    booking_notes: str | None = None
    location: str | None = None       # business address
    hours: str | None = None          # business hours description


# --- Webhook --------------------------------------------------------------


# --- Audit / operational log ----------------------------------------------


class TenantAuditLogOut(BaseModel):
    """A single audit/log entry, read-only for the API surface."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    event_type: str
    level: str
    message: str
    actor_scope: str | None = None
    actor_id: str | None = None
    changed_fields: dict | None = None
    details: dict = {}
    duration_ms: int | None = None
    created_at: datetime


class TenantAuditLogListOut(BaseModel):
    """Paginated list of log entries."""

    entries: list[TenantAuditLogOut]
    total: int


# --- Data / integration settings ----------------------------------------


class TenantDataSettingsOut(BaseModel):
    """Data/integration policy for a tenant.

    `source_of_truth` controls where the platform reads/writes business data.
    `sync_mode` controls how data flows between the source and other stores.
    `sheets_connected` reflects whether an active sheets provider config exists.
    Every field is always present with a sensible default.
    """

    source_of_truth: str
    sync_mode: str
    sheets_connected: bool


class TenantDataSettingsUpdate(BaseModel):
    """Partial update for data/integration settings. Only provided fields change."""

    source_of_truth: str | None = None
    sync_mode: str | None = None


# --- Customer identity settings -------------------------------------------


CUSTOMER_IDENTITY_MODES = frozenset({
    "full_name",
    "first_name_last_name",
    "dni",
    "full_name_dni",
    "first_name_last_name_dni",
})


class TenantIdentitySettingsOut(BaseModel):
    """Customer identity capture mode for booking."""

    mode: str = "full_name"


class TenantIdentitySettingsUpdate(BaseModel):
    """Update customer identity capture mode."""

    mode: str = "full_name"


# --- Tenant self-service Sheets config -----------------------------------


SHEETS_CREDENTIAL_KEYS = frozenset({"api_key"})
SHEETS_SETTINGS_KEYS = frozenset({"spreadsheet_id", "sheet_name", "range"})


class TenantSheetsConfigOut(BaseModel):
    """Typed view of a tenant's sheets provider config.

    The ``credentials`` and ``settings`` dicts follow a minimal explicit
    schema so the tenant-facing UI has stable field names to bind to:

    **credentials**
        ``api_key`` — Google Sheets API key (optional for public sheets).

    **settings**
        ``spreadsheet_id`` — the Google Sheet ID from the sheet URL.
        ``sheet_name`` — sheet tab name, defaults to ``"Sheet1"``.
        ``range`` — cell range, e.g. ``"A1:Z1000"``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    label: str
    provider_name: str
    credentials: dict = {}
    settings: dict = {}
    is_active: bool


class TenantSheetsConfigCreate(BaseModel):
    """Create/update a sheets provider config from the tenant UI."""

    label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    provider_name: Annotated[
        str, StringConstraints(min_length=1, max_length=64)
    ] = "google_sheets"
    credentials: dict | None = None
    settings: dict | None = None
    is_active: bool = True


class TenantSheetsConfigUpdate(BaseModel):
    """Partial update for a sheets provider config.

    All fields optional — only provided fields are applied.
    """

    label: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    provider_name: Annotated[
        str, StringConstraints(min_length=1, max_length=64)
    ] | None = None
    credentials: dict | None = None
    settings: dict | None = None
    is_active: bool | None = None


# --- Services import (preview + apply) ------------------------------------


class ServiceImportRow(BaseModel):
    """A parsed row from the pasted sheet content."""

    code: Annotated[str, StringConstraints(min_length=1, max_length=8)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    duration_minutes: Annotated[int, Field(ge=15, le=480)]
    price_cents: Annotated[int, Field(ge=0)] = 0
    description: Annotated[str, StringConstraints(max_length=500)] | None = None


class ServiceImportRowPreview(BaseModel):
    """One row in the preview response, classified."""

    classification: str  # "create" | "update" | "unchanged" | "invalid"
    reason: str
    row_index: int  # 0-based original line number (excluding header)
    row: ServiceImportRow


class ServiceImportPreviewRequest(BaseModel):
    """Body for the preview endpoint.

    Accepts pasted TSV/CSV text. Header row is required.
    """

    content: Annotated[str, StringConstraints(min_length=1)]
    delimiter: Annotated[
        str, StringConstraints(pattern=r"^[\t,;|]$")
    ] = "\t"


class ServiceImportPreviewResponse(BaseModel):
    """Categorized preview of all parsed rows."""

    total: int
    create: list[ServiceImportRowPreview]
    update: list[ServiceImportRowPreview]
    unchanged: list[ServiceImportRowPreview]
    invalid: list[ServiceImportRowPreview]


class ServiceImportApplyRequest(BaseModel):
    """Body for the apply endpoint.

    Carries the rows to create and/or update. The caller typically
    sends the ``create`` + ``update`` lists from a preview response.
    """

    create: list[ServiceImportRow]
    update: list[ServiceImportRow]


class ServiceImportApplyResult(BaseModel):
    """Result of applying an import."""

    created: int
    updated: int
    errors: list[str]


# --- Barber import (preview + apply) -------------------------------------


class BarberImportRow(BaseModel):
    """A parsed row from the pasted sheet content for barbers."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    restrictions: Annotated[str, StringConstraints(max_length=64)] | None = None
    is_active: bool = True


class BarberImportRowPreview(BaseModel):
    """One row in the barber preview response, classified."""

    classification: str  # "create" | "update" | "unchanged" | "invalid"
    reason: str
    row_index: int  # 0-based original line number (excluding header)
    row: BarberImportRow


class BarberImportPreviewRequest(BaseModel):
    """Body for the barber preview endpoint."""

    content: Annotated[str, StringConstraints(min_length=1)]
    delimiter: Annotated[
        str, StringConstraints(pattern=r"^[\t,;|]$")
    ] = "\t"


class BarberImportPreviewResponse(BaseModel):
    """Categorized preview of all parsed barber rows."""

    total: int
    create: list[BarberImportRowPreview]
    update: list[BarberImportRowPreview]
    unchanged: list[BarberImportRowPreview]
    invalid: list[BarberImportRowPreview]


class BarberImportApplyRequest(BaseModel):
    """Body for the barber apply endpoint."""

    create: list[BarberImportRow]
    update: list[BarberImportRow]


class BarberImportApplyResult(BaseModel):
    """Result of applying a barber import."""

    created: int
    updated: int
    errors: list[str]


# --- Schedule import (preview + apply) ------------------------------------


class ScheduleImportRow(BaseModel):
    """A parsed row from the pasted sheet content for schedules."""

    barber_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    weekday: Annotated[str, StringConstraints(min_length=3, max_length=3)]
    start_time: time
    end_time: time


class ScheduleImportRowPreview(BaseModel):
    """One row in the schedule preview response, classified."""

    classification: str  # "create" | "update" | "unchanged" | "invalid"
    reason: str
    row_index: int  # 0-based original line number (excluding header)
    row: ScheduleImportRow


class ScheduleImportPreviewRequest(BaseModel):
    """Body for the schedule preview endpoint."""

    content: Annotated[str, StringConstraints(min_length=1)]
    delimiter: Annotated[
        str, StringConstraints(pattern=r"^[\t,;|]$")
    ] = "\t"


class ScheduleImportPreviewResponse(BaseModel):
    """Categorized preview of all parsed schedule rows."""

    total: int
    create: list[ScheduleImportRowPreview]
    update: list[ScheduleImportRowPreview]
    unchanged: list[ScheduleImportRowPreview]
    invalid: list[ScheduleImportRowPreview]


class ScheduleImportApplyRequest(BaseModel):
    """Body for the schedule apply endpoint."""

    create: list[ScheduleImportRow]
    update: list[ScheduleImportRow]


class ScheduleImportApplyResult(BaseModel):
    """Result of applying a schedule import."""

    created: int
    updated: int
    errors: list[str]


# --- Webhook --------------------------------------------------------------


class WebhookInboundPayload(BaseModel):
    """The minimal shape a provider webhook has to deliver.

    The full provider payload is preserved in
    `incoming_messages.raw_payload` for replay; this model only carries
    the fields the intake service needs to do its work.
    """

    provider_message_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    from_phone: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    body: Annotated[str, StringConstraints(max_length=4096)] = ""
    channel: Annotated[str, StringConstraints(max_length=32)] = "whatsapp"
    # Optional: provider-side metadata kept for replay / debugging.
    raw_payload: dict | None = None


class WebhookInboundResult(BaseModel):
    accepted: bool
    duplicate: bool
    state: str
    reply: str
