# Tenant Appointments Specification

## Purpose

Tenant users view and manage appointments day by day: date-navigable agenda, manual booking, cancel, and status changes. Existing backend: GET list, POST book, DELETE cancel, POST reschedule. New: PATCH status, `require_tenant` on all routes.

## Requirements

### Requirement: List day agenda

MUST return appointments for a date and barber, scoped to the tenant. Cancelled appointments SHALL be excluded by default.

#### Scenario: Happy path

- GIVEN 8 appointments on 2026-07-15 across 3 barbers
- WHEN GET `/tenants/{tenant_id}/appointments?barber_id={id}&date_from=2026-07-15&date_to=2026-07-15`
- THEN response MUST be 200 with appointment objects containing `id`, `barber_id`, `service_id`, `appointment_date`, `start_time`, `end_time`, `status`, `customer_name`, `customer_phone`, `notes`

#### Scenario: Empty day

- GIVEN a date with no appointments
- WHEN a GET request is sent
- THEN response MUST be 200 with empty array

### Requirement: Manual booking

MUST create an appointment via the dashboard (same endpoint as bot booking).

#### Scenario: Happy path — book slot

- GIVEN barber has availability at 10:00
- WHEN POST `/tenants/{tenant_id}/appointments` with `{"barber_id": "...", "service_id": "...", "start_at": "2026-07-15T10:00:00", "customer_name": "Juan Perez", "customer_phone": "+54111234567"}`
- THEN response MUST be 201 with the created appointment

#### Scenario: Slot taken

- GIVEN the slot is already booked
- WHEN a POST request is sent for the same slot
- THEN response MUST be 409 Conflict

#### Scenario: Past time

- GIVEN `start_at` is in the past
- WHEN a POST request is sent
- THEN response MUST be 422

#### Scenario: Service restriction

- GIVEN barber has "SOLO_CORTE" and service is not Corte
- WHEN a POST request is sent
- THEN response MUST be 422

### Requirement: Cancel appointment

MUST cancel an appointment (and CB continuation partner).

#### Scenario: Happy path

- GIVEN an existing pending appointment
- WHEN DELETE `/tenants/{tenant_id}/appointments/{appointment_id}`
- THEN response MUST be 200 with appointment status `"cancelled"`

#### Scenario: Already cancelled

- GIVEN an appointment already `cancelled`
- WHEN a DELETE request is sent
- THEN response MUST be 409 Conflict

#### Scenario: Past appointment

- GIVEN an appointment that has passed
- WHEN a DELETE request is sent
- THEN response MUST be 422

### Requirement: Change status via PATCH

MUST update appointment status. Valid statuses: pending, confirmed, completed, no_show.

#### Scenario: Confirm appointment

- GIVEN a pending appointment
- WHEN PATCH `/tenants/{tenant_id}/appointments/{appointment_id}/status` with `{"status": "confirmed"}`
- THEN response MUST be 200 with `status: "confirmed"`

#### Scenario: Mark completed

- GIVEN a confirmed appointment
- WHEN a PATCH request is sent with `{"status": "completed"}`
- THEN response MUST be 200

#### Scenario: Mark no-show

- GIVEN a confirmed appointment
- WHEN a PATCH request is sent with `{"status": "no_show"}`
- THEN response MUST be 200

#### Scenario: Invalid status

- GIVEN payload with `{"status": "invalid"}`
- WHEN a PATCH request is sent
- THEN response MUST be 422

#### Scenario: Not found

- GIVEN a non-existent `appointment_id`
- WHEN a PATCH request is sent
- THEN response MUST be 404

### Requirement: Auth on all routes

All appointment endpoints MUST require a valid tenant bearer token matching the path tenant.

#### Scenario: All routes return 401 without auth

- GIVEN no bearer token
- WHEN any GET, POST, DELETE, or PATCH request is sent
- THEN response MUST be 401

## Backend Schema

### StatusUpdateRequest (new)

| Field | Type | Notes |
|-------|------|-------|
| status | string | One of: pending, confirmed, completed, no_show, cancelled |

### New endpoint

| Method | Path | Status |
|--------|------|--------|
| PATCH | `/tenants/{tenant_id}/appointments/{appointment_id}/status` | 200 |

## Frontend Pages

| Route | Method | Action |
|-------|--------|--------|
| `/tenant/appointments/` | GET | Day agenda with date picker + appointment list |
| `/tenant/appointments/` | POST | Book, cancel, or status change (hidden `_action`) |

The day agenda SHALL show: date input (defaults to today), appointments chronologically with time, barber name, customer, service, status badge, and action buttons (cancel, confirm, complete, no-show).
