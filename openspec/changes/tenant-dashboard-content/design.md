# Design: Tenant Dashboard Content

## Technical Approach

4-phase delivery extending existing CRUD patterns with new PUT/PATCH/DELETE endpoints, tenant auth wiring, and Astro SSR frontend pages. No new services or migrations — routes manipulate ORM objects directly (existing pattern from barbers.py/services.py), and all models carry the needed columns.

## Architecture Decisions

| Decision | Options | Choice & Rationale |
|----------|---------|--------------------|
| Update pattern | Service layer vs direct ORM | **Direct ORM** — existing barbers.py/services.py set fields + commit. No business rules in these updates, so a service layer adds indirection. |
| Auth wiring | Per-route decorator vs `require_tenant` Depends | **`require_tenant` Depends** — already exists in `deps.py`; injects at route level, zero decorator infrastructure. |
| Barber/service delete | Hard DELETE vs `is_active` toggle | **`is_active` toggle** via PUT — barbers/services have FK constraints (schedules, absences, appointments). Soft-delete avoids cascade complications. |
| Appointment status change | New route vs extend manage_service | **New PATCH route** using existing `AppointmentRepository.set_status()` — lightweight, no domain logic for simple status transitions. Cancel/reschedule stay on manage_service. |
| Frontend form pattern | Client JS vs Astro POST | **Astro POST with hidden `_action`** — matches existing login.astro pattern; no JS framework needed for CRUD forms. |

## Data Flow

```
# List barbers (tenant-scoped)
Astro SSR ──GET /tenants/{tid}/barbers──> FastAPI
  apiJson({request:Astro.request})         │
  cookie → api.ts matches tenant_session    require_tenant(principal.tenant_id == path_tid)
  Auth header ← Authorization: Bearer       BarberRepository.list()
                                            └─→ BarberOut[]
  <── JSON array ────────────────────────────┘

# Update barber (PUT)
form POST _action=update ──> Astro SSR
  apiFetch(PUT /tenants/{tid}/barbers/{id}, {body: JSON, request})
    FastAPI: require_tenant → repo.get_by_id() → set attrs → commit → BarberOut

# Set weekly schedule with overrides
Astro SSR ──GET /tenants/{tid}/barbers/{bid}/schedules──> ScheduleRepository.list_for_barber()
                                  ──GET .../absences──> AbsenceRepository.list_for_barber()
                                  ──GET .../extra-hours──> ExtraHourRepository.list_for_barber()
    Each CRUD action via POST _action=create-schedule | update-schedule | delete-schedule

# Book appointment manually
form POST _action=book ──> POST /tenants/{tid}/appointments ──> BookingService.book_slot()
  BookingService: plan_booking() → create row(s) → commit
  Returns BookingResult { appointment, continuation? }

# Cancel appointment
form POST _action=cancel ──> DELETE /tenants/{tid}/appointments/{id}
  AppointmentManageService.cancel(id) → status="cancelled" (+ CB partner)
```

## Component Design — Backend

**New schemas** in `schemas.py`: All-fields-optional `*Update` models (BarberUpdate, ServiceUpdate, ScheduleUpdate, AbsenceUpdate, ExtraHourUpdate) plus `StatusUpdateRequest(status: str)` with enum validator — matching existing `ProviderConfigUpdate` pattern.

**PUT route pattern** (all 6 entities): `get_by_id` → 404 if None → `setattr` for each non-None field → `session.commit()` → validate + return. No service layer — same approach as the CREATE routes.

**DELETE** (schedules, absences, extra-hours): `repo.delete(id)` → 204 on True, 404 on False. Already exists for absences/extra-hours; add to schedules.

**PATCH `/appointments/{id}/status`**: `repo.set_status(id, payload.status)` → 404 if None. Status transitions validated by DB enum + domain.

**Auth wiring**: Add `_tenant=Depends(require_tenant)` to every existing list/create/get route in barbers.py, services.py, schedules.py, absences.py, extra_hours.py, appointments.py, overview.py. New routes get it too. The auth check is on `tenant_id` path parameter vs principal's `tenant_id`.

## Component Design — Frontend

**Cookie fix** (`lib/api.ts`): Extend the `barber_session` cookie regex fallback to also try `tenant_session` cookie — currently it only forwards one.

**Page structure** under `src/pages/tenant/`:

| Route | File | Content |
|-------|------|---------|
| `/tenant/dashboard` | `dashboard.astro` | KPI cards + today agenda from overview API |
| `/tenant/barbers/` | `barbers/index.astro` | Table list + create form |
| `/tenant/barbers/{id}/edit` | `barbers/[id]/edit.astro` | Edit form |
| `/tenant/barbers/{id}/toggle` | POST to list page | `_action=toggle` |
| `/tenant/barbers/{id}/` | `barbers/[id]/index.astro` | Detail: schedule grid, absences, extra-hours |
| `/tenant/services/` | `services/index.astro` | Table list + create form |
| `/tenant/services/{id}/edit` | `services/[id]/edit.astro` | Edit form |
| `/tenant/services/{id}/toggle` | POST to list page | `_action=toggle` |
| `/tenant/appointments/` | `appointments/index.astro` | Day agenda + booking form |

**Form pattern**: POST with hidden `<input name="_action" value="create">`. Astro reads `formData().get("_action")` to dispatch. Same pattern throughout all CRUD pages.

**API calls**: `apiJson<T>(path, { request: Astro.request })` for server-side GET. `apiFetch(path, { method, body, request })` for writes.

**Component sharing**: `TenantBase.astro` layout already exists. No shared CRUD component — each page is standalone Astro (keep it simple, 4 pages only).

## Phase Breakdown

### Phase 1: Backend endpoints + auth

| File | Action | Description |
|------|--------|-------------|
| `apps/api/src/schemas.py` | Modify | Add BarberUpdate, ServiceUpdate, ScheduleUpdate, AbsenceUpdate, ExtraHourUpdate, StatusUpdateRequest |
| `apps/api/src/routes/barbers.py` | Modify | +PUT +require_tenant on existing routes |
| `apps/api/src/routes/services.py` | Modify | +PUT +require_tenant on existing routes |
| `apps/api/src/routes/schedules.py` | Modify | +PUT +DELETE +require_tenant on existing routes |
| `apps/api/src/routes/absences.py` | Modify | +PUT +require_tenant on existing routes |
| `apps/api/src/routes/extra_hours.py` | Modify | +PUT +require_tenant on existing routes |
| `apps/api/src/routes/appointments.py` | Modify | +PATCH status +require_tenant on existing routes |
| `apps/api/src/routes/overview.py` | Modify | +require_tenant on get_overview |
| `apps/admin-astro/src/lib/api.ts` | Modify | Also match tenant_session cookie in apiFetch |

### Phase 2: Barbers & services frontend

| File | Action | Description |
|------|--------|-------------|
| `pages/tenant/barbers/index.astro` | Create | List table + create form + toggle action |
| `pages/tenant/barbers/[id]/edit.astro` | Create | Edit form (name, restrictions, is_active) |
| `pages/tenant/services/index.astro` | Create | List table + create form + toggle action |
| `pages/tenant/services/[id]/edit.astro` | Create | Edit form (all service fields) |

### Phase 3: Schedules frontend

| File | Action | Description |
|------|--------|-------------|
| `pages/tenant/barbers/[id]/index.astro` | Create | Per-barber detail: schedule grid (Mon-Sun), absences table, extra-hours table |
| `pages/tenant/barbers/[id]/schedules/` | (inline on detail) | Create/update/delete schedule rows |

### Phase 4: Appointments + dashboard overview

| File | Action | Description |
|------|--------|-------------|
| `pages/tenant/appointments/index.astro` | Create | Date picker + barber filter + agenda list + booking form |
| `pages/tenant/dashboard.astro` | Modify | Add KPI cards + today agenda (replacing welcome message) |

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| API (backend) | PUT barber/service/schedule/absence/extra-hour — happy + 404 | TestClient, reuse `seeded` fixture pattern from test_part4_routes.py |
| API (backend) | DELETE schedule/absence/extra-hour — 204 + 404 | Same pattern |
| API (backend) | PATCH appointment status — all valid transitions + invalid + 404 | Same pattern |
| API (backend) | Auth — all new and modified routes return 401/403 without valid tenant token | `client.get(...)` without auth header |
| Frontend | N/A | Astro SSR has no test infra yet. Manual verification via browser. |

**Key test structure**: Each route file gets a `Test*` class per resource (e.g. `TestBarberUpdateRoute`) with happy-path, 404, and auth scenarios. No new fixtures needed — `seeded` already provides a tenant + barber + services + schedule.

## Open Questions

- None — all decisions map to existing codebase patterns.
