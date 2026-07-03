# Exploration: Tenant Operations & Config Foundation

## Current State

The platform is a greenfield multi-tenant barber booking system. The **tenant-dashboard-content** change is nearly complete (17/17 tasks implemented, verified, not yet archived). That change delivered full CRUD for barbers, services, schedules, absences, extra-hours, appointments, plus a dashboard overview and auth on all tenant routes.

### What exists today relevant to the approved items:

**Identity & Sessions:**
- Auth: bearer tokens with scope (`superadmin` / `tenant`), PBKDF2 hashing, 12h session cookies
- `TenantUser` model: email, password_hash, name, is_active — **no role/permissions column**
- `Base.astro` and `TenantBase.astro` are **identical** — no visual differentiation between admin and tenant dashboards
- Topbar shows email + sign out for both — no context indicator (admin vs tenant)

**Business Configuration:**
- `TenantSetting` table: one JSONB `config` column per tenant — completely unstructured
- Greeting is hardcoded in `packages/application/intake/__init__.py` as a module constant
- Message flow is deterministic (`classify_intent`) with optional LLM fallback via OpenRouter
- Barber names, prices, services have full CRUD (from the completed change) but no **tenant self-service** UI yet — only the API exists
- No tenant-side settings page exists; tenant users only see the dashboard and appointments

**Provider Configuration:**
- `ProviderConfig` table: per-tenant, per-kind (whatsapp, llm, calendar, **sheets**, sms), JSONB credentials + settings
- Kapso transport and config helpers exist
- **Sheets kind is declared but no adapter or sync logic exists**

**Operational Controls:**
- Tenant lifecycle: `active` / `suspended` / `trial` / `churned` — this is global status, not operational
- **No per-tenant bot-enable toggle** (the bot runs for every active tenant regardless)
- **No tenant-wide holiday/closure** system — only per-barber `BarberAbsence`
- **No booking intake close** — the bot accepts new conversations 24/7 if the tenant is active
- `Barber.is_active` exists in the model but has no simple toggle in the tenant UI (only via the barber edit page)

**Observability:**
- `outgoing_messages` table serves as bot audit log — no timing/ms data, no admin change history
- **No audit trail for config changes** — who changed what and when is not tracked
- **No health check endpoints** per integration
- **No per-tenant application logging** with timings

**Data Ownership:**
- Tenant is the data root — every table has `tenant_id` with CASCADE deletes
- No explicit data-ownership policy documented in code
- Google Sheets connection exists only as a provider `kind="sheets"` enum value — no adapter

### Existing Specs (7 domain specs, all implemented via tenant-dashboard-content):

| Spec | Status |
|------|--------|
| tenant-crud-barbers | ✅ Implemented |
| tenant-crud-services | ✅ Implemented |
| tenant-crud-schedules | ✅ Implemented |
| tenant-crud-absences | ✅ Implemented |
| tenant-crud-extra-hours | ✅ Implemented |
| tenant-appointments | ✅ Implemented |
| tenant-dashboard-overview | ✅ Implemented |

---

## Affected Areas

| Area | Files | Why |
|------|-------|-----|
| **DB Models** | `packages/infrastructure/db/models/tenants.py`, `packages/infrastructure/db/models/auth.py`, `packages/infrastructure/db/models/scheduling.py` + new models | Need new tables: `tenant_closures`, `tenant_roles` (or a role column), `audit_log`, `feature_flags`. `TenantSetting.config` needs structure. |
| **Auth/RBAC** | `packages/application/auth/__init__.py`, `packages/infrastructure/db/models/tenant_user.py`, `apps/api/src/deps.py` | Role/permission column on TenantUser, new auth dependencies for role gating |
| **API Routes** | `apps/api/src/routes/tenants.py`, `apps/api/src/routes/superadmin.py`, new route files | Operational endpoints (bot toggle, closures), tenant self-service settings, sheets sync, audit |
| **Application Services** | `packages/application/intake/__init__.py`, `packages/application/superadmin/__init__.py`, new services | Intake reads bot config instead of hardcoded greeting; new operational service layer |
| **Scheduling/Domain** | `packages/domain/scheduling/` | Tenant-wide closure rules need to be integrated into booking availability logic |
| **Frontend Layouts** | `apps/admin-astro/src/layouts/Base.astro`, `apps/admin-astro/src/layouts/TenantBase.astro`, `apps/admin-astro/src/components/Topbar.astro` | Visual differentiation, nav structure for operational controls |
| **Frontend Pages** | New + existing pages under `apps/admin-astro/src/pages/tenant/` and `apps/admin-astro/src/pages/admin/` | Operational dashboard, tenant settings/self-service, audit viewer, feature flag UI |
| **Session/Cookies** | `apps/admin-astro/src/lib/session.ts` | Session duration change from 12h |
| **Provider Config** | `packages/application/providers/`, `packages/infrastructure/repositories/providers.py` | Sheets adapter, webhook signature for sheets, sync logic |
| **Migrations** | `packages/infrastructure/db/migrations/` | New migration(s) for each new table/column |
| **Tests** | `tests/` | Coverage for all new routes and domain logic |

---

## Grouped Implementation Epics

### Epic A: Operational Controls (items 5, 11, partially 1)

The emergency-stop layer: controls to disable a misbehaving bot, close booking for holidays, and temporarily disable barbers without deleting data.

**Items covered:**
- 5: Bot enable/disable, reset/restart bot flow, temporarily disable a barber, close booking intake for a date
- 11: Explicit holiday/closure/override controls (tenant-wide, not per-barber)
- 1 (partial): Longer session duration (12h → configurable)

**New models needed:**
- `TenantClosure` — date, reason, tenant_id (simple table, tenant-wide closure)
- Bot config fields on `TenantSetting.config` — `bot_enabled`, `intake_open`

**Dependencies:** None — operates on existing infrastructure.

---

### Epic B: Business Configuration & Self-Service (items 1, 2, 9, 15, 16)

The config layer: what the bot says, how it behaves, and the UI to control it.

**Items covered:**
- 1: Visual differentiation of admin vs tenant dashboards (layouts, nav, branding)
- 2: Bot/business customization — greeting, message flow/reply behavior, barber schedules/names/prices/services as tenant self-service
- 9: Draft/publish mode for sensitive config
- 15: Feature flags per tenant
- 16: Onboarding templates for new tenants

**New models needed:**
- `TenantBotConfig` (or structured fields in `TenantSetting.config`) — greeting, menu options, flow steps
- `TenantFeatureFlag` — key/value per tenant
- Draft/publish versioning on `TenantSetting` or `ProviderConfig`
- Onboarding template — a snapshot of default config for new tenants

**Dependencies:** Epic A (bot toggle is a prerequisite for bot config — you need a kill switch before you customize)

---

### Epic C: Data Integration & Governance (items 3, 4, 10, 12)

The rules and infrastructure for external data.

**Items covered:**
- 3: Clarify central DB responsibility, tenant data ownership, define Sheets purpose
- 4: Sheets/Excel connect and sync per tenant
- 10: Safe import with preview
- 12: Source-of-truth rules between DB and external sheets

**New infrastructure:**
- Sheets adapter (infrastructure layer, connector pattern)
- Sync service with conflict resolution
- Import preview endpoint (parse + validate without persisting)
- Documented data-ownership policy

**Dependencies:** Epics A + B (operational controls protect the sync; config foundation defines what gets synced)

---

### Epic D: Access Control & Audit (items 6, 7)

The governance layer: who can do what and a record of what happened.

**Items covered:**
- 6: Tenant roles/permissions
- 7: Audit history

**New models needed:**
- Role column on `TenantUser` (admin / operator / viewer)
- `AuditLog` table — actor_id, action, target_type, target_id, details, timestamp

**Dependencies:** Epics A + B (audit only matters once config changes exist to track; roles gate the operations)

---

### Epic E: Observability & Resilience (items 13, 14, 17)

The monitoring and recovery layer.

**Items covered:**
- 13: Per-tenant logs with timings (ms)
- 14: Backup/restore per tenant
- 17: Health checks per integration

**New models needed:**
- `TenantLog` — structured log rows with ms timings, per tenant
- Backup manifest (could be file-system based)

**Dependencies:** Epics A–D (health checks need integrations to check; logs need operations to log; backup needs a clear data boundary)

---

## Dependency Graph

```
Epic A: Operational Controls ───────────────────┐
                                                ├──> Epic B: Business Config ──> Epic C: Integrations
Epic D: Access Control & Audit <────────────────┘       │
                                                        └──> Epic E: Observability
```

**Strict ordering:**
- Epic D depends on Epic A (roles gate operational controls)
- Epic B depends on Epic A (bot toggle before bot config)
- Epic C depends on Epic B (config defines shape of synced data)
- Epic E depends on Epics A + B + C (you monitor what you have)

---

## Recommended First Slice

### Slice 1: Operational Base — Bot Toggle + Holiday Closures + Config Backend

**Goal:** Give operators the ability to stop a malfunctioning bot and close dates for holidays, while laying the backend foundation for config-driven bot behavior. No visual polish — minimal UI additions to existing settings pages.

**Scope:**

| Area | What | Why |
|------|------|-----|
| **Bot toggle** | `bot_enabled` field in `TenantSetting.config`; webhook handler checks it before processing; `intake_open` flag for pausing new conversations | Emergency stop without needing a developer |
| **Holiday closures** | New `tenant_closures` table (tenant_id, date, reason, created_at); booking availability logic excludes closed dates; API: CRUD for closures | Table-stakes for any booking business |
| **Config backend** | Structured `greeting_template`, `menu_options`, `flow_steps` fields in `TenantSetting.config` (or a new `TenantBotConfig` table); intake service reads from config instead of hardcoded constants | Unlocks every customization downstream |
| **Session duration** | 12h → 24h config change in `session.ts` | Simple, zero-risk change |
| **Minimal UI** | Bot toggle + holiday list/date picker in existing tenant settings page (add a section to the settings page) | Operators can actually use the controls |

**Slice 1 does NOT include:**
- Visual differentiation of dashboards
- Tenant self-service UI for barbers/services/schedules
- Draft/publish mode
- Feature flags
- Onboarding templates
- Google Sheets sync
- Roles/permissions
- Audit history
- Per-tenant logs with timings
- Backup/restore
- Health checks

**Product leverage:** High. Operators gain a kill switch and holiday management — two things a production booking bot cannot launch without. The config backend is invisible but enables every subsequent customization slice. Total new code surface: small (~3 new models, ~5 API endpoints, 1 small UI section).

**Effort estimate:** ~400-600 lines (backend + models + minimal UI)

---

## Major Risks

1. **TenantSetting JSONB vs structured tables:** The current pattern uses a single JSONB `config` blob. As config grows, querying/validating individual fields becomes painful. Risk of the JSONB blob turning into an unmaintainable dump. **Mitigation:** Add a `TenantBotConfig` table (or dedicated columns) early rather than overloading `TenantSetting.config`.

2. **Session duration change is deceptively simple:** Extending from 12h to 24h or making it configurable is easy, but there's no refresh-token mechanism — if a token is stolen, it's valid for the full duration. **Mitigation:** Add `max_age` to the `api_tokens` table per-token, and/or implement token rotation for long-lived sessions. For Slice 1, just change the constant and document the trade-off.

3. **Holiday closure integration with booking logic:** The booking domain (`BookingService`, `AvailabilityService`) needs to check closures. Current availability logic only checks per-barber schedules/absences/extra-hours. Adding tenant-wide closures requires modifying the domain layer, which has no existing test for this case. **Mitigation:** Write closure-filter logic in the domain layer with its own unit test before wiring into the API.

4. **Ordering of migration vs existing data:** Adding `bot_enabled` defaults to `true` is safe for existing tenants. Adding structured fields to `TenantSetting.config` requires careful migration — existing JSONB blobs may not have the new keys. **Mitigation:** Application code treats missing keys as their default value (Python `.get("key", default)` pattern).

5. **No role system yet — who can toggle the bot?** Both superadmin and tenant user can access operational controls. Without roles, any tenant user with valid credentials can disable their own bot. **Mitigation:** For Slice 1, gate bot toggle behind `require_tenant` (authenticated tenant user) + a simple check that the user's scope matches. Accept the gap until Epic D delivers proper roles.

6. **Pre-existing test failures:** 5 tests fail in `test_part3_routes.py` and `test_seams.py` (webhook + kapso transport). These are pre-existing and unrelated, but they make the test suite noisy. **Mitigation:** Document as pre-existing, do not block on them.

---

## Ready for Proposal

**Yes** — exploration is sufficient to write a proposal. The recommended first slice (Operational Base) is small, well-understood, and independently deliverable. Epics A–E are clearly scoped with documented dependencies.
