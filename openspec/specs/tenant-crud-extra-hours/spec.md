# Tenant CRUD Extra Hours Specification

## Purpose

Tenant users manage per-barber date-specific extra availability outside the weekly schedule. Extra hours add working time on top of the base schedule (e.g. a Saturday shift). Multiple extra-hour entries per barber/date are allowed.

## Requirements

### Requirement: List extra hours for a barber

The system MUST return all extra hours for a given barber, optionally filtered by date range, scoped to the tenant.

#### Scenario: Happy path — list all extra hours

- GIVEN barber "Carlos" has 3 extra-hour entries next month
- WHEN a GET request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/extra-hours`
- THEN the response MUST return 200 with an array of extra-hour objects
- AND each object MUST contain `id`, `extra_date`, `start_time`, `end_time`, `reason`

#### Scenario: Filter by date range

- GIVEN extra hours exist on Jan 15 and Jan 25
- WHEN a GET request is sent with `?date_from=2026-01-01&date_to=2026-01-20`
- THEN the response MUST return only the Jan 15 entry

#### Scenario: Non-existent barber returns 404

- GIVEN a `barber_id` that does not exist
- WHEN a GET request is sent
- THEN the response MUST return 404 Not Found

### Requirement: Create extra hour

The system MUST create an extra-hour entry for a barber.

#### Scenario: Happy path — add Saturday shift

- GIVEN barber "Carlos" exists
- WHEN a POST request is sent with `{"extra_date": "2026-07-18", "start_time": "10:00", "end_time": "14:00", "reason": "Saturday coverage"}`
- THEN the response MUST return 201 Created with the stored entry

#### Scenario: Start after end returns 422

- GIVEN a payload with `start_time: "14:00"`, `end_time: "10:00"`
- WHEN a POST request is sent
- THEN the response MUST return 422 Unprocessable Entity

### Requirement: Update extra hour

The system MUST update an existing extra-hour entry.

#### Scenario: Happy path — change hours

- GIVEN an existing extra-hour entry for July 18, 10-14
- WHEN a PUT request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/extra-hours/{extra_hour_id}` with `{"start_time": "09:00", "end_time": "15:00"}`
- THEN the response MUST return 200 with the updated entry

#### Scenario: Update non-existent returns 404

- GIVEN an `extra_hour_id` that does not exist
- WHEN a PUT request is sent
- THEN the response MUST return 404 Not Found

### Requirement: Delete extra hour

The system MUST delete an extra-hour entry.

#### Scenario: Happy path — delete extra hour

- GIVEN an existing extra-hour entry
- WHEN a DELETE request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/extra-hours/{extra_hour_id}`
- THEN the response MUST return 204 No Content

#### Scenario: Delete non-existent returns 404

- GIVEN an `extra_hour_id` that does not exist
- WHEN a DELETE request is sent
- THEN the response MUST return 404 Not Found

## Backend Schema

### ExtraHourUpdate (new)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| extra_date | date | No | |
| start_time | time | No | |
| end_time | time | No | |
| reason | string(max 120) or null | No | |

### New endpoint

| Method | Path | Status |
|--------|------|--------|
| PUT | `/tenants/{tenant_id}/barbers/{barber_id}/extra-hours/{extra_hour_id}` | 200 |

## Frontend Pages

Users manage extra hours on the per-barber detail page at `/tenant/barbers/{id}/`. An "Extra Hours" section SHALL list all entries in a table with create/edit/delete actions via forms with hidden `_action` field.
