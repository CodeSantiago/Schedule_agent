# Tenant CRUD Absences Specification

## Purpose

Tenant users manage per-barber date-specific unavailability. Absences override the weekly schedule: a full-day absence (no time range) makes the barber unavailable all day; a partial-day absence blocks a specific time window. This is used for vacations, sick days, or personal time off.

## Requirements

### Requirement: List absences for a barber

The system MUST return all absences for a given barber, optionally filtered by date range, scoped to the tenant.

#### Scenario: Happy path — list all absences

- GIVEN barber "Carlos" has 2 absences in the next month
- WHEN a GET request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/absences`
- THEN the response MUST return 200 with an array of absence objects
- AND each object MUST contain `id`, `absence_date`, `start_time`, `end_time`, `reason`

#### Scenario: Filter by date range

- GIVEN absences exist on Jan 5 and Jan 20
- WHEN a GET request is sent with `?date_from=2026-01-01&date_to=2026-01-10`
- THEN the response MUST return only the Jan 5 absence

#### Scenario: Non-existent barber returns 404

- GIVEN a `barber_id` that does not exist
- WHEN a GET request is sent
- THEN the response MUST return 404 Not Found

### Requirement: Create absence

The system MUST create a new absence for a barber.

#### Scenario: Happy path — full-day absence

- GIVEN barber "Carlos" exists
- WHEN a POST request is sent with `{"absence_date": "2026-07-15", "reason": "Vacation"}`
- AND `start_time` and `end_time` are NOT provided
- THEN the response MUST return 201 Created
- AND `start_time` and `end_time` MUST be `null`
- AND this means Carlos is unavailable the entire day

#### Scenario: Partial-day absence

- GIVEN barber "Carlos" exists
- WHEN a POST request is sent with `{"absence_date": "2026-07-15", "start_time": "14:00", "end_time": "16:00", "reason": "Doctor appointment"}`
- THEN the response MUST return 201 Created with the time range stored

### Requirement: Update absence

The system MUST update an existing absence.

#### Scenario: Happy path — change reason or times

- GIVEN an existing absence for July 15, 14-16
- WHEN a PUT request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/absences/{absence_id}` with `{"end_time": "17:00", "reason": "Doctor visit"}`
- THEN the response MUST return 200 with the updated fields

### Requirement: Delete absence

The system MUST delete an absence entry.

#### Scenario: Happy path — delete absence

- GIVEN an existing absence
- WHEN a DELETE request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/absences/{absence_id}`
- THEN the response MUST return 204 No Content

#### Scenario: Delete non-existent returns 404

- GIVEN an `absence_id` that does not exist
- WHEN a DELETE request is sent
- THEN the response MUST return 404 Not Found

## Backend Schema

### AbsenceUpdate (new)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| absence_date | date | No | |
| start_time | time or null | No | Set to null for full-day |
| end_time | time or null | No | Set to null for full-day |
| reason | string(max 120) or null | No | |

### New endpoint

| Method | Path | Status |
|--------|------|--------|
| PUT | `/tenants/{tenant_id}/barbers/{barber_id}/absences/{absence_id}` | 200 |

## Frontend Pages

Users manage absences on the per-barber detail page at `/tenant/barbers/{id}/`. An "Absences" section SHALL list all absences in a table with create/edit/delete actions via forms with hidden `_action` field.
