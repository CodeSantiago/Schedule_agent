# Exploration: Tenant Dashboard Content

## Current State

The project is a multi-tenant barber booking platform (greenfield rebuild). Stack: FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL. Clean/Hexagonal architecture with `packages/domain`, `packages/application`, `packages/infrastructure`. Frontend is Astro SSR in `apps/admin-astro`. The tenant dashboard currently shows only a simple welcome message after login — no CRUD functionality exists yet.

## Database Models (All Exist)

The full schema is deployed across 5 migrations (`0001_initial` → `0005_tenant_auth`):

### `Barber` (`barbers`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → tenants.id | CASCADE on delete |
| `name` | String(120) | |
| `restrictions` | String(64) | nullable, e.g. "SOLO_CORTE" |
| `is_active` | Bool | default true |
| `created_at`, `updated_at` | DateTime(tz) | |

Relationships: `schedules`, `absences`, `extra_hours`, `appointments`

### `Service` (`services`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → tenants.id | CASCADE |
| `name` | String(120) | |
| `code` | String(8) | C/B/CB/OTHER, default OTHER |
| `duration_minutes` | Integer | |
| `price_cents` | Integer | minor units |
| `description` | Text | nullable |
| `is_active` | Bool | default true |
| `created_at`, `updated_at` | DateTime(tz) | |

### `BarberSchedule` (`barber_schedules`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `barber_id` | UUID FK → barbers.id | CASCADE |
| `weekday` | Enum(mon..sun) | |
| `start_time` | Time | |
| `end_time` | Time | |
| Unique: `(barber_id, weekday, start_time)` |

### `BarberAbsence` (`barber_absences`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `barber_id` | UUID FK → barbers.id | CASCADE |
| `absence_date` | Date | indexed |
| `start_time` | Time | nullable (NULL = whole day) |
| `end_time` | Time | nullable |
| `reason` | String(120) | nullable |

### `BarberExtraHour` (`barber_extra_hours`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `barber_id` | UUID FK → barbers.id | CASCADE |
| `extra_date` | Date | indexed |
| `start_time` | Time | |
| `end_time` | Time | |
| `reason` | String(120) | nullable |

### `Appointment` (`appointments`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → tenants.id | CASCADE |
| `barber_id` | UUID FK → barbers.id | CASCADE |
| `service_id` | UUID FK → services.id | RESTRICT |
| `appointment_date` | Date | indexed |
| `start_time` | DateTime(tz) | |
| `end_time` | DateTime(tz) | |
| `status` | Enum(pending/confirmed/cancelled/completed/no_show) | default pending |
| `customer_name` | String(120) | |
| `customer_phone` | String(32) | indexed |
| `notes` | String(500) | nullable |
| Unique: `(tenant_id, barber_id, appointment_date, start_time)` |

### `TenantUser` (`tenant_users`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → tenants.id | CASCADE |
| `email` | String(255) | globally unique |
| `password_hash` | String(255) | PBKDF2 |
| `name` | String(120) | display name |
| `is_active` | String(8) | "true"/"false" (SQLite compat) |

## Existing API Endpoints

All endpoints are under FastAPI at `apps/api/src/routes/`. They are **not** auth-protected at the route level (no `get_tenant_principal` dependency) — they rely on the `TenantPath` pattern which only extracts the tenant_id from the URL. Auth dependencies exist in `deps.py` (`get_tenant_principal`, `require_tenant`) but are **not used** by any CRUD route.

### Barbers (`/tenants/{tenant_id}/barbers`)
- `GET ""` — list all barbers for tenant
- `POST ""` — create barber (name, restrictions, is_active)
- `GET "/{barber_id}"` — get single barber

**Missing**: PUT (update), DELETE (deactivate/remove)

### Services (`/tenants/{tenant_id}/services`)
- `GET ""` — list all services for tenant
- `POST ""` — create service (name, code, duration_minutes, price_cents, description, is_active)
- `GET "/{service_id}"` — get single service

**Missing**: PUT (update), DELETE

### Schedules (`/tenants/{tenant_id}/barbers/{barber_id}/schedules`)
- `GET ""` — list schedules for a barber
- `POST ""` — create schedule entry (weekday, start_time, end_time)

**Missing**: DELETE per schedule entry, PUT (update), no update endpoint

### Absences (`/tenants/{tenant_id}/barbers/{barber_id}/absences`)
- `GET ""` — list absences (with optional date_from/date_to)
- `POST ""` — create absence
- `DELETE "/{absence_id}"` — delete absence

**Missing**: PUT (update)

### Extra Hours (`/tenants/{tenant_id}/barbers/{barber_id}/extra-hours`)
- `GET ""` — list extra hours (with optional date_from/date_to)
- `POST ""` — create extra hour
- `DELETE "/{extra_hour_id}"` — delete extra hour

**Missing**: PUT (update)

### Appointments (`/tenants/{tenant_id}/appointments`)
- `GET ""` — list appointments (requires barber_id, optional date_from/date_to)
- `POST ""` — create appointment (full booking service with domain rules)
- `DELETE "/{appointment_id}"` — cancel appointment (with CB partner support)
- `POST "/{appointment_id}/reschedule"` — reschedule appointment

**Missing**: PUT (update customer info), ability to list all appointments for a tenant without barber_id filter

### Availability & Overview (read-only)
- `GET "/tenants/{tenant_id}/availability"` — available slots per barber/service/date
- `GET "/tenants/{tenant_id}/agenda"` — day agenda (all barbers or one)
- `GET "/tenants/{tenant_id}/overview"` — dashboard KPI data (counts, day list, upcoming)

### Tenant Auth (`/tenants/auth`)
- `POST "/login"` — authenticate tenant user, returns bearer token + tenant_id
- `GET "/me"` — returns current principal info (token, scope, tenant_id)

### General Tenant (`/tenants`)
- `GET "/{tenant_id}"` — get tenant info (name, slug, status, timezone)

## Existing UI Pages and Components

### Tenant-specific pages:
- `/tenant/login.astro` — login form, POST authenticates and sets session cookies, redirect to dashboard
- `/tenant/dashboard.astro` — **current minimal dashboard**: checks auth, loads tenant info via `/tenants/{id}`, shows welcome message

### Layouts:
- `TenantBase.astro` — used by tenant pages, has `<Topbar>` with email, main `<slot />`
- `Base.astro` — used by superadmin pages, same structure

### Components:
- `Topbar.astro` — brand + signed-in user email + sign out button

### Shared lib:
- `session.ts` — `getTenantPrincipal()`, `tenantLogin()`, `setTenantSessionCookie()`, cookie management for `tenant_session`, `tenant_email`, `tenant_id`
- `api.ts` — `apiFetch()`, `apiJson()`, `ApiError` class, resolves base URL, forwards cookies

### CSS:
- `app.css` — full dark theme design system: cards, buttons, forms, tables, KPIs, status badges, provider configs, empty states. All pre-built styles exist for the dashboard pages.

## Application Services

### `BookingService` (booking_service.py)
- Tenant-scoped by construction
- Uses: BarberRepository, ServiceRepository, ScheduleRepository, AbsenceRepository, ExtraHourRepository, AppointmentRepository
- `book_slot(BookSlotCommand)` → plans and persists (handles CB as 2 rows)
- Has `list_barbers()`, `list_active_barbers()`, `list_services()` helpers
- Domain rules: slot grid, past-time, SOLO_CORTE, barber availability, double-booking

### `AppointmentManageService` (manage_service.py)
- `cancel(appointment_id)` — cancels appointment + CB partner
- `reschedule(appointment_id, new_start_at)` — moves appointment, reuses domain booking rules

### `OverviewService` (overview_service.py)
- `build(target_date)` → counts (booked/cancelled/completed/pending/confirmed), day's appointments with names, upcoming 7-day bucket counts

## Repositories (all tenant-scoped via `TenantScopedRepository`)

| Repository | Model | Extra Methods |
|-----------|-------|---------------|
| `BarberRepository` | Barber | `get_by_id()`, `list_active()` |
| `ServiceRepository` | Service | `list_active()` |
| `ScheduleRepository` | BarberSchedule | `list_for_barber()`, `list_for_barber_and_weekday()` — joins through barber for tenant scope |
| `AbsenceRepository` | BarberAbsence | `list_for_barber()`, `list_for_barber_on_date()` — joins through barber |
| `ExtraHourRepository` | BarberExtraHour | `list_for_barber()`, `list_for_barber_on_date()` — joins through barber |
| `AppointmentRepository` | Appointment | `get_for_barber_on()`, `get_for_barber_in_range()`, `is_slot_taken()`, `list_for_tenant_on()`, `list_for_tenant_in_range()`, `find_cb_partner()`, `find_cb_primary()`, `count_status_for_day()`, `set_status()` |

Base `TenantScopedRepository` provides: `list()`, `get_by_id()`, `add()`, `delete()`.

## Architectural Patterns

### API Layer
- FastAPI with APIRouter prefix pattern: `/tenants/{tenant_id}/{resource}`
- Dependencies in `deps.py`: repos are injected per-route via `Depends(get_barber_repo)` etc.
- `TenantPath` = `Annotated[UUID, Path(...)]` annotation for tenant_id
- DB session via `get_db()` generator, yielded per request
- Repos constructed with `(session, tenant_id)` — tenant-scoped by construction
- Routes call `repo.session.commit()` and `repo.session.refresh()` directly
- Pydantic schemas in `schemas.py` with `model_config = ConfigDict(from_attributes=True)`

### **IMPORTANT**: API routes are NOT auth-protected!
- Auth deps (`get_tenant_principal`, `require_tenant`) exist in `deps.py` but are **not wired** to any CRUD route
- Only `/tenants/auth/login` and `/tenants/auth/me` use auth
- This means the API will accept requests with no token or with a superadmin token — the dashboard pages use cookie-based auth, but there's no server-side enforcement at the API level

### Frontend (Astro SSR)
- Server-rendered pages with form POST for mutations (no JS framework for CRUD)
- Auth: cookie-based (`tenant_session` httpOnly cookie with bearer token)
- `getTenantPrincipal()` checks `/tenants/auth/me` to validate + refresh principal
- API calls via `apiFetch()` / `apiJson()` which forward the cookie's bearer token
- Forms use `method="post"` with hidden `_action` field for action routing
- All state is server-side (Astro frontmatter reads cookies, calls API, renders HTML)
- Pattern: check auth → redirect if null → load data → render

## What's Missing Per Area

### 1. Barbers (barberos) — CRUD
**Backend gaps:**
- API: Missing PUT (update) and DELETE endpoints for barbers
- Repository: `update()` method not needed if using session.merge, but no route exists
- The `BarberRepository` only has `list_active()` on top of base

**Frontend gaps (ALL):**
- **List page**: No page exists to list barbers with edit/delete actions
- **Create form**: No page with form to create a barber
- **Edit form**: No page to edit barber name, restrictions, is_active
- **Delete**: No delete confirmation/action for barbers

### 2. Services (servicios) — CRUD
**Backend gaps:**
- API: Missing PUT (update) and DELETE endpoints for services
- Repository: `ServiceRepository` only has `list_active()` on top of base

**Frontend gaps (ALL):**
- **List page**: No page to list services with name, code, duration, price
- **Create form**: No page with form to create a service
- **Edit form**: No page to edit service fields
- **Delete**: No delete confirmation/action for services

### 3. Schedules (horarios) — Availability per barber/day
**Backend gaps:**
- API: Missing DELETE for schedule entries (only list and create exist)
- API: Missing PUT for schedule entries
- The Schedule route uses `/tenants/{tenant_id}/barbers/{barber_id}/schedules/`

**Frontend gaps (ALL):**
- **Per-barber schedule view**: No page to see a barber's weekly schedule
- **Add schedule entry**: No form to add weekly time ranges per weekday
- **Delete schedule entry**: No UI to remove schedule entries
- **Absences management**: No UI for creating/deleting absences (API exists)
- **Extra hours management**: No UI for creating/deleting extra hours (API exists)
- The schedule editing needs to be integrated into the barber detail/edit flow

### 4. Appointments (turnos) — List, create, edit, cancel
**Backend gaps:**
- API: Missing PUT for editing appointment details (customer name, phone, notes)
- API: `GET /appointments` requires `barber_id` query param — no "all appointments across barbers" endpoint (use `/agenda` for that)
- Listing appointments by status filter is not directly supported

**Frontend gaps (ALL):**
- **Day agenda view**: No page to see today's appointments (API `/overview` exists with all data)
- **Create appointment form**: No page with form to manually create a booking (API exists)
- **Appointment detail/edit**: No page to view/edit appointment details
- **Cancel appointment**: No UI for cancelling (API exists)
- **Status management**: No UI to change status (pending→confirmed→completed)
- **Appointment list with filters**: No page to browse appointments by date range, barber, status

## Recommendations for Implementation

### Approach: Progressive Enhancement per Entity

Given that the backend APIs already exist for MOST operations (except PUT/UPDATE and a few DELETEs), and the frontend has a complete design system ready, the most efficient approach is:

**Phase 1: Backend — Complete missing API endpoints (1 task)**
1. Add UPDATE (PUT) endpoints for barbers, services, schedules, absences, extra_hours
2. Add DELETE endpoint for schedules (barbers/services have cascade-safe deletes via rest of code)
3. Add PATCH for appointment status changes and PUT for appointment details edit
4. Wire auth protection (`require_tenant`) to all tenant CRUD routes
5. Add Pydantic schemas for update payloads

**Phase 2: Frontend — Barbers & Services pages (2 tasks)**
1. List page + create form + edit form for barbers
2. List page + create form + edit form for services

**Phase 3: Frontend — Schedules & Absences pages (1 task)**
1. Per-barber schedule view with CRUD
2. Absences list + create + delete per barber
3. Extra hours list + create + delete per barber

**Phase 4: Frontend — Appointments/Agenda page (2 tasks)**
1. Day agenda view with today's appointments (uses existing `/overview` endpoint)
2. Appointment detail: edit, cancel, status changes
3. Manual booking form (creates appointment)
4. Browse/filter appointments by date, barber, status

### Frontend Patterns to Follow
- Each entity gets its own Astro page under `/tenant/{entity}/` (e.g. `/tenant/barbers/`, `/tenant/barbers/new`, `/tenant/barbers/{id}/edit`)
- Use `TenantBase` layout for all tenant pages
- Use `getTenantPrincipal()` for auth guard
- Form POST with hidden `_action` for mutations
- Use the existing CSS classes (`.card`, `.form`, `.btn`, `table.grid`, `.kpi`, `.status`, `.flash`, `.form-error`, `.empty-state`)
- Load data in frontmatter with `apiJson()`, handle `ApiError` for error states
- Redirect back to list on successful create/edit/delete

### Auth Considerations
The tenant CRUD API routes should use the existing `require_tenant` / `get_tenant_principal` dependencies. Currently none of the CRUD routes are auth-protected. This is a **risk** — unauthenticated access to tenant data is possible. Recommend adding auth protection as part of this change.

## Risks

1. **No auth on API routes**: Currently ANYONE who knows a tenant_id UUID can call the CRUD endpoints. The auth deps exist but are unused. This must be addressed.
2. **Schedule/absence repo tenant scoping**: `ScheduleRepository`, `AbsenceRepository`, `ExtraHourRepository` don't have `tenant_id` on their tables — they join through `barbers.tenant_id`. This works but means the base `delete()` on `TenantScopedRepository` won't work out of the box (they already override `delete()` and `add()`). Any new bulk operations need the same pattern.
3. **No PUT schemas exist**: The `schemas.py` file has `Create` and `Out` models but no `Update` models. Need to add them (all fields optional for PATCH semantics).
4. **No search/filter endpoints**: Appointments can only be queried by `(barber_id, date_range)` or all-tenant by `(date)`. No status filter, no customer name search. The dashboard may need this.
5. **Overview endpoint is today-centric**: The `/overview` endpoint works for a "today" dashboard but doesn't support arbitrary date ranges for appointment browsing.
6. **No cascade delete on barbers**: Deleting a barber CASCADES to schedules, absences, extra_hours, but appointments use `ondelete=RESTRICT` on `service_id` (the FK on appointments → services). Services have no `ondelete` cascade for appointments either. Deleting a barber or service with existing appointments may fail.
7. **Frontend is server-rendered only**: All mutations require full page POST → redirect. No client-side interactivity (no Alpine.js, no htmx, no React). This makes the UX simple but less dynamic. If the team wants inline editing or real-time updates, they'd need a different approach.

## Ready for Proposal
Yes — all the necessary information is available to write a proposal for the "Tenant Dashboard Content" change. The proposal should cover:
- Scope: which 4 entities, what CRUD ops per entity
- Auth: add `require_tenant` to all tenant CRUD endpoints
- Backend: missing PUT/DELETE endpoints
- Frontend: page structure following existing patterns
- Out of scope: anything not explicitly listed (e.g. real-time updates, search, pagination)
