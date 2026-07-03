# Future Improvements

This document captures follow-up work that should happen after the current MVP/dashboard migration stabilizes.

## High Priority

### 1. Timezone correctness and host-independence

Current timezone handling is not robust enough for production multi-tenant use.

#### Current problem

- Tenant rows already store `timezone`.
- Appointments use `DateTime(timezone=True)` in the DB model.
- But several code paths still use naive `datetime.now()` / `date.today()` and naive `datetime.combine(...)`.
- That means behavior can drift depending on the host timezone (for example Railway or any US-hosted environment).

#### What must be fixed

- Treat tenant timezone as the source of truth for all booking rules.
- Introduce a shared timezone utility/service using `zoneinfo.ZoneInfo`.
- Compute `now` and `today` in the tenant timezone, not the server timezone.
- Convert tenant-local wall-clock input to timezone-aware datetimes.
- Persist appointment timestamps in UTC.
- Convert back from UTC to tenant timezone when rendering or calculating day-based views.
- Review all past-time checks, availability checks, overview/day grouping, cancel/reschedule logic, and webhook intake timestamps.

#### Files/areas to review first

- `packages/application/scheduling/booking_service.py`
- `packages/application/scheduling/manage_service.py`
- `packages/application/scheduling/overview_service.py`
- `apps/api/src/routes/availability.py`
- `apps/api/src/routes/overview.py`
- any future Kapso/WhatsApp inbound timestamp handling

---

### 2. Enforce archived-tenant behavior

Soft delete currently maps to `status = "churned"`, but archived tenants are not fully blocked from all tenant-scoped operations yet.

#### Follow-up

- Decide the exact policy for archived tenants.
- Prevent archived tenants from receiving new operational writes if required.
- Define whether webhooks for archived tenants should be ignored, rejected, or logged-only.

---

### 3. Dashboard test coverage for Astro

The old Jinja web tests were removed during migration.

#### Follow-up

- Add Astro-focused test coverage.
- Prefer Playwright or a small browser-level smoke suite.
- Cover login, tenant list, settings, provider config CRUD, and archive flow.

## Medium Priority

### 4. Provider-specific config UX

Provider config forms are improved, but still incomplete.

#### Follow-up

- Expand typed forms beyond `whatsapp` and `llm`.
- Add clearer provider-specific field labels and validation.
- Consider hiding raw JSON completely for common providers.

---

### 5. CSRF protection for Astro admin

Current admin flow relies on same-origin + cookie behavior.

#### Follow-up

- Add CSRF token generation and verification for state-changing admin forms.

---

### 6. Real provider transport

Current transport seam is ready, but the real provider integration is not implemented.

#### Follow-up

- Implement Kapso transport behind the existing transport interface.
- Add webhook signature verification and provider-specific error handling.
- Add end-to-end test scenarios against sandbox/staging credentials.

---

### 7. Real LLM fallback

Current intake remains deterministic-first only.

#### Follow-up

- Add an LLM-backed classifier behind the existing intent seam.
- Keep deterministic-first behavior and use the LLM only as fallback.
- Add observability for misclassification / fallback rate.

## Product / UX Follow-ups

### 8. Client operational panel redesign

The long-term product direction is no longer "web booking first".

#### Follow-up

- Build the real client operational panel around WhatsApp-first intake.
- Weekly calendar view.
- Day/hour/barber schedule visibility.
- Fast overrides for absences, partial-day blocks, and manual adjustments.

---

### 9. Reduce unnecessary scrolling across admin pages

Some screens were already compacted, but this should be treated as an explicit UX standard.

#### Follow-up

- Review all main admin screens for laptop-sized viewports.
- Keep critical actions above the fold where possible.
- Only allow scrolling when data volume genuinely requires it.

## Guiding principle

The next infrastructure-hardening priority should be:

1. **timezone correctness**
2. archived-tenant enforcement
3. Astro dashboard test coverage
4. real provider transport
5. LLM fallback

Timezone is the most important technical correctness gap because it can silently produce wrong booking behavior depending on hosting environment.
