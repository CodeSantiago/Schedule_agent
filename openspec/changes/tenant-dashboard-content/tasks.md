# Tasks: Tenant Dashboard Content

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,400–1,700 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (backend) → PR 2 (barbers FE) → PR 3 (services FE) → PR 4 (schedule FE) → PR 5 (dashboard+appts FE) |
| Delivery strategy | ask-on-risk |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Backend: Update schemas + routes + auth + api.ts + tests | PR 1 | Base = main; standalone backend slice |
| 2 | Frontend: barbers list/create/edit/toggle pages | PR 2 | Depends on PR 1; base = main (or stacked) |
| 3 | Frontend: services list/create/edit/toggle pages | PR 3 | Depends on PR 1; base = main (or stacked) |
| 4 | Frontend: barber detail with schedule grid/absences/extra-hours | PR 4 | Depends on PR 2 (nav from barbers list); base = main |
| 5 | Frontend: dashboard KPIs + agenda + appointments day view + booking | PR 5 | Depends on PR 1; base = main (or stacked) |

---

## Phase 1: Backend — Update schemas, PUT/DELETE/PATCH routes, auth, cookie fix

- [x] **T1**: Add `BarberUpdate`, `ServiceUpdate`, `ScheduleUpdate`, `AbsenceUpdate`, `ExtraHourUpdate`, `StatusUpdateRequest` to `apps/api/src/schemas.py` (all-fields-optional pattern matching `ProviderConfigUpdate`)
  - Files: `apps/api/src/schemas.py`
  - Deps: none | Effort: S
- [x] **T2**: Add `PUT /barbers/{id}` endpoint + wire `require_tenant` to existing GET/POST routes in `apps/api/src/routes/barbers.py`
  - Files: `apps/api/src/routes/barbers.py`
  - Deps: T1 | Effort: S
- [x] **T3**: Add `PUT /services/{id}` endpoint + wire `require_tenant` to existing routes in `apps/api/src/routes/services.py`
   - Files: `apps/api/src/routes/services.py`
   - Deps: T1 | Effort: S
- [x] **T4**: Add `PUT /schedules/{id}` + `DELETE /schedules/{id}` endpoints + wire `require_tenant` in `apps/api/src/routes/schedules.py` (repo already has `delete()`)
   - Files: `apps/api/src/routes/schedules.py`
   - Deps: T1 | Effort: S
- [x] **T5**: Add `PUT /absences/{id}` endpoint + wire `require_tenant` in `apps/api/src/routes/absences.py`
   - Files: `apps/api/src/routes/absences.py`
   - Deps: T1 | Effort: S
- [x] **T6**: Add `PUT /extra-hours/{id}` endpoint + wire `require_tenant` in `apps/api/src/routes/extra_hours.py`
   - Files: `apps/api/src/routes/extra_hours.py`
   - Deps: T1 | Effort: S
- [x] **T7**: Add `PATCH /appointments/{id}/status` endpoint (uses existing `AppointmentRepository.set_status()`) + wire `require_tenant` to all existing routes in `apps/api/src/routes/appointments.py`
   - Files: `apps/api/src/routes/appointments.py`
   - Deps: T1 | Effort: S
- [x] **T8**: Wire `require_tenant` to `GET /overview` in `apps/api/src/routes/overview.py`
   - Files: `apps/api/src/routes/overview.py`
   - Deps: T1 | Effort: XS
- [x] **T9**: Fix `apps/admin-astro/src/lib/api.ts` to also match `tenant_session` cookie alongside `barber_session`
   - Files: `apps/admin-astro/src/lib/api.ts`
   - Deps: none | Effort: XS
- [x] **T10**: Write backend tests for all new/modified routes (PUT each entity, DELETE schedule, PATCH status, auth 401/403 on all routes) using TestClient + `seeded` fixture per `test_part4_routes.py` pattern
  - Files: `tests/api/test_tenant_dashboard_routes.py`
  - Deps: T1–T8 | Effort: M

## Phase 2: Frontend — Barbers & Services CRUD pages

- [x] **T11**: Create `apps/admin-astro/src/pages/tenant/barbers/index.astro` — table list of barbers (name, restrictions, status toggle) + inline create form via `_action=create` + toggle via `_action=toggle`, uses `apiJson`/`apiFetch` with cookie forwarding
   - Files: `apps/admin-astro/src/pages/tenant/barbers/index.astro`
   - Deps: T9, T2 | Effort: M
- [x] **T12**: Create `apps/admin-astro/src/pages/tenant/barbers/[id]/edit.astro` — edit form for barber fields (name, restrictions, is_active), POST with `_action=update`, redirect back to list
   - Files: `apps/admin-astro/src/pages/tenant/barbers/[id]/edit.astro`
   - Deps: T11 | Effort: S
- [x] **T13**: Create `apps/admin-astro/src/pages/tenant/services/index.astro` — table list of services (name, code, duration, price, status toggle) + inline create form + toggle
   - Files: `apps/admin-astro/src/pages/tenant/services/index.astro`
   - Deps: T9, T3 | Effort: M
- [x] **T14**: Create `apps/admin-astro/src/pages/tenant/services/[id]/edit.astro` — edit form for service fields, POST with `_action=update`
   - Files: `apps/admin-astro/src/pages/tenant/services/[id]/edit.astro`
   - Deps: T13 | Effort: S

## Phase 3: Frontend — Per-barber schedules, absences, extra-hours

- [x] **T15**: Create `apps/admin-astro/src/pages/tenant/barbers/[id]/index.astro` — barber detail page with:
  - Weekly schedule grid (Mon–Sun rows with time inputs, create/update/delete via `_action`)
  - Absences table + create form (`_action=create-absence`, `delete-absence`)
  - Extra-hours table + create form (`_action=create-extra-hour`, `delete-extra-hour`)
  - Calls schedules/absences/extra-hours APIs (T4–T6)
  - Files: `apps/admin-astro/src/pages/tenant/barbers/[id]/index.astro`
  - Deps: T11, T4, T5, T6 | Effort: L

## Phase 4: Frontend — Dashboard overview + Appointments agenda

- [x] **T16**: Update `apps/admin-astro/src/pages/tenant/dashboard.astro` — replace welcome message with KPI card row (booked/pending/confirmed/completed/cancelled/active barbers/active services/upcoming days) + today's agenda list (time, barber, customer, service, status badge) from `GET /overview` API
   - Files: `apps/admin-astro/src/pages/tenant/dashboard.astro`
   - Deps: T8, T9 | Effort: M
- [x] **T17**: Create `apps/admin-astro/src/pages/tenant/appointments/index.astro` — day agenda view with:
  - Date picker (defaults to today) + barber filter
  - Chronological appointment list with time, barber, customer, service, status badge
  - Action buttons: confirm, complete, no-show (PATCH status), cancel (DELETE)
  - Manual booking form (POST appointment via `BookingService`)
  - Files: `apps/admin-astro/src/pages/tenant/appointments/index.astro`
  - Deps: T7, T9 | Effort: L

## Dependency Graph

```
T1 (schemas) ──┬── T2 (barbers PUT+auth) ── T11 (barbers list FE) ── T12 (barbers edit FE)
              ├── T3 (services PUT+auth) ── T13 (services list FE) ── T14 (services edit FE)
              ├── T4 (schedules PUT+DELETE+auth) ─┐
              ├── T5 (absences PUT+auth) ────────┤── T15 (barber detail FE)
              ├── T6 (extra-hours PUT+auth) ─────┘
              ├── T7 (appointments PATCH+auth) ── T17 (appointments FE)
              └── T8 (overview auth) ─────────── T16 (dashboard FE)

T9 (api.ts cookie) ──┬── (used by all FE pages via apiFetch)
                     │
T10 (backend tests) ── Last in Phase 1

Phases flow: Phase 1 → Phase 2 → Phase 3 → Phase 4
Phase 2 & Phase 4 can partially parallelize (both need Phase 1 but not each other)
```
