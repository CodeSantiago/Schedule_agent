# Tenant CRUD Schedules Specification

## Purpose

Tenant users define weekly recurrent availability for each barber: which weekdays they work, and during what hours. Date-specific overrides (absences, extra hours) are separate capabilities. Schedule entries live on `barber_schedules` and represent a recurrent weekly time slot. Multiple entries per barber/weekday are allowed.

## Requirements

### Requirement: List schedules for a barber

The system MUST return all weekly schedule entries for a given barber scoped to the tenant.

#### Scenario: Happy path — list all schedule rows

- GIVEN barber "Carlos" has 5 schedule rows (Mon-Fri 9-18)
- WHEN a GET request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/schedules`
- THEN the response MUST return 200 with an array of 5 schedule objects
- AND each object MUST contain `id`, `weekday`, `start_time`, `end_time`

#### Scenario: Non-existent barber returns 404

- GIVEN a `barber_id` that does not exist for this tenant
- WHEN a GET request is sent
- THEN the response MUST return 404 Not Found

### Requirement: Create schedule entry

The system MUST create a weekly schedule row for a barber.

#### Scenario: Happy path — add a Monday slot

- GIVEN barber "Carlos" exists
- WHEN a POST request is sent with `{"weekday": "mon", "start_time": "09:00", "end_time": "13:00"}`
- THEN the response MUST return 201 Created
- AND the weekday MUST be stored lowercase as `"mon"`

#### Scenario: Invalid weekday returns 422

- GIVEN a payload with `weekday: "xxx"`
- WHEN a POST request is sent
- THEN the response MUST return 422 Unprocessable Entity

#### Scenario: Start after end returns domain error

- GIVEN a payload with `start_time: "14:00"`, `end_time: "09:00"`
- WHEN a POST request is sent
- THEN the response MUST return 422 Unprocessable Entity

### Requirement: Update schedule entry

The system MUST update an existing schedule row.

#### Scenario: Happy path — change hours

- GIVEN an existing schedule row for Monday 9-13
- WHEN a PUT request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/schedules/{schedule_id}` with `{"start_time": "09:00", "end_time": "14:00"}`
- THEN the response MUST return 200 with the updated entry

### Requirement: Delete schedule entry

The system MUST delete a schedule row.

#### Scenario: Happy path — delete a slot

- GIVEN an existing schedule row
- WHEN a DELETE request is sent to `/tenants/{tenant_id}/barbers/{barber_id}/schedules/{schedule_id}`
- THEN the response MUST return 204 No Content
- AND the row MUST no longer appear in the list

#### Scenario: Delete non-existent returns 404

- GIVEN a `schedule_id` that does not exist
- WHEN a DELETE request is sent
- THEN the response MUST return 404 Not Found

## Backend Schema

### ScheduleUpdate (new)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| weekday | string(3) | No | One of: mon/tue/wed/thu/fri/sat/sun |
| start_time | time | No | |
| end_time | time | No | |

### New endpoints (backend)

| Method | Path | Status |
|--------|------|--------|
| PUT | `/tenants/{tenant_id}/barbers/{barber_id}/schedules/{schedule_id}` | 200 |
| DELETE | `/tenants/{tenant_id}/barbers/{barber_id}/schedules/{schedule_id}` | 204 |

## Frontend Pages

| Route | Method | Action |
|-------|--------|--------|
| `/tenant/barbers/{id}/` | GET | Show barber detail page with weekly schedule grid |
| Add row form on barber detail | POST | Create schedule (hidden `_action=create-schedule`) |
| Edit row form on barber detail | POST | Update schedule (hidden `_action=update-schedule`) |
| Delete button on barber detail | POST | Delete schedule (hidden `_action=delete-schedule`) |

The schedule grid SHALL display a row per weekday (Mon-Sun) with time inputs for start/end. Empty weekday rows SHALL show empty time fields. The UI MUST allow adding multiple time ranges per weekday.
