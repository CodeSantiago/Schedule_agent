# Proposal: Tenant Dashboard Content

## Intent

Tenant dashboard shows only a welcome message. Tenants have no UI to manage barbers, services, schedules, or appointments. Deliver full CRUD across all 4 entities plus missing backend endpoints.

## Scope

**In**: Backend PUT endpoints for all entities + DELETE schedules + PATCH appointment status + auth wiring. Frontend: barbers/services/schedules/absences/extra-hours CRUD pages, dashboard overview (KPIs + today agenda), appointment day agenda + manual booking + cancel/status. Fix `api.ts` `tenant_session` cookie forwarding.

**Out**: Real-time updates, pagination, customer search, calendar widget, bulk ops, notifications, role-based access, superadmin changes.

## Decisions

| Decision | Choice |
|----------|--------|
| Barber/Service delete | Soft-delete via `is_active` toggle |
| Cancelled appointments | Stay in DB with `cancelled` status (not hard-deleted) |
| Schedule model | Recurrent weekly patterns (e.g. Mon-Fri 9-18) with per-day overrides for exceptions |
| Appointment calendar | Date-navigable agenda — pick a day, see/ manage its appointments |

## Capabilities

**New**: `tenant-crud-barbers`, `tenant-crud-services`, `tenant-crud-schedules`, `tenant-crud-absences`, `tenant-crud-extra-hours`, `tenant-appointments`, `tenant-dashboard-overview`.

**Modified**: None (no existing specs in `openspec/specs/`).

## Approach

4-phase delivery. **Phase 1**: Add `*Update` schemas to `schemas.py`. Add PUT endpoints per route file, `DELETE /schedules/{id}`, `PATCH /appointments/{id}/status`. Wire `require_tenant` dep to every CRUD route. Fix `api.ts` to forward `tenant_session` cookie (currently only forwards `barber_session`).

**Phase 2**: `/tenant/barbers/` and `/tenant/services/` pages with list (table.grid), create/edit forms (POST + hidden `_action`). Deactivate via is_active toggle.

**Phase 3**: Per-barber detail page with weekly schedule grid, absences, extra-hours CRUD.

**Phase 4**: Dashboard KPIs + today agenda (from existing OverviewService). `/tenant/appointments/` with date+barber filter, cancel, status PATCH, manual booking form.

## Affected Areas

| Area | Impact |
|------|--------|
| `apps/api/src/schemas.py` | +`*Update` models |
| `routes/barbers.py` | +PUT, auth |
| `routes/services.py` | +PUT, auth |
| `routes/schedules.py` | +PUT+DELETE, auth |
| `routes/absences.py` | +PUT, auth |
| `routes/extra_hours.py` | +PUT, auth |
| `routes/appointments.py` | +PUT+PATCH, auth |
| `routes/overview.py` | +auth dep |
| `lib/api.ts` | fix tenant_session cookie |
| `pages/tenant/dashboard.astro` | KPIs + agenda |
| `pages/tenant/barbers/` | New CRUD pages |
| `pages/tenant/services/` | New CRUD pages |
| `pages/tenant/barbers/[id]/` | Schedule/absences |
| `pages/tenant/appointments/` | Agenda + booking |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Auth addition breaks existing flow | Medium | Only client is Astro dashboard; deploy cookie fix in same commit |
| FK constraint on delete barber/service | High | Soft-delete (is_active=false) for barbers/services; DELETEs on schedules/absences are FK-safe |
| Missing repo `update()` method | Low | Direct field assignment on existing ORM object works — no repo change needed |

## Rollback Plan

Revert the deployment commit. No DB migrations to undo (all fields exist).

## Dependencies

- Backend must deploy before frontend (API contract)
- No migrations needed

## Success Criteria

- [ ] All backend tests pass
- [ ] Tenant CRUD barbers (list/create/update/deactivate) via UI
- [ ] Tenant CRUD services (list/create/update/toggle) via UI
- [ ] Tenant views/manages weekly schedule per barber
- [ ] Tenant manages absences & extra hours per barber
- [ ] Dashboard shows KPIs + today's agenda
- [ ] Tenant creates appointments, changes status, cancels
- [ ] All CRUD routes return 401/403 without valid tenant token
