# Tenant Dashboard Overview Specification

## Purpose

The tenant dashboard home page displays operational KPIs and today's appointment agenda in a single view. The overview endpoint already exists at `GET /tenants/{tenant_id}/overview?date=YYYY-MM-DD` and returns counts + appointments + upcoming data. This spec covers wiring `require_tenant` to the endpoint and building the frontend view.

## Requirements

### Requirement: Wire auth to overview endpoint

The overview endpoint MUST require a valid tenant-scoped bearer token matching the path tenant.

#### Scenario: Overview returns 401 without auth

- GIVEN no valid bearer token
- WHEN a GET request is sent to `/tenants/{tenant_id}/overview`
- THEN the response MUST return 401 Unauthorized

#### Scenario: Overview returns 403 for wrong tenant

- GIVEN a bearer token for tenant A
- WHEN a GET request is sent to `/tenants/{tenant_id}/overview` where tenant_id is tenant B
- THEN the response MUST return 403 Forbidden

### Requirement: Return KPI counts

The system MUST return counts for the target date, scoped to the tenant.

#### Scenario: Happy path — counts reflect day's data

- GIVEN the tenant has 12 appointments today: 5 pending, 3 confirmed, 2 completed, 2 cancelled
- WHEN a GET request is sent to `/tenants/{tenant_id}/overview?date=2026-07-15`
- THEN the response MUST contain `counts` with `booked_today: 10` (non-cancelled), `cancelled_today: 2`, `pending_today: 5`, `confirmed_today: 3`, `completed_today: 2`
- AND `active_barbers` MUST reflect the tenant's active barber count
- AND `active_services` MUST reflect the tenant's active service count

#### Scenario: Empty day returns zeros

- GIVEN a date with no appointments
- WHEN a GET request is sent
- THEN the response MUST contain `counts` with all counts at 0

### Requirement: Return today's appointment list

The system MUST return non-cancelled appointments for the target date, enriched with barber and service names.

#### Scenario: Happy path — appointments are enriched

- GIVEN 3 active appointments today for barber "Carlos" with service "Corte"
- WHEN a GET request is sent
- THEN the response MUST contain `appointments` array with 3 items
- AND each item MUST contain `barber_name`, `service_name`, `customer_name`, `start_time`, `end_time`, `status`, `is_cb_continuation`

#### Scenario: CB continuation flagged

- GIVEN a CB appointment's continuation row
- WHEN a GET request is sent
- THEN `is_cb_continuation` MUST be `true` for the continuation row

### Requirement: Return upcoming day counts

The system MUST return appointment counts for the next 7 days (excluding today), keyed by ISO date.

#### Scenario: Happy path — upcoming days

- GIVEN the next 7 days have: 3 bookings on day 1, 5 on day 3, 0 on others
- WHEN a GET request is sent
- THEN the response MUST contain `upcoming` with 7 entries mapping ISO dates to counts
- AND `upcoming_days_with_bookings` MUST be `2`

## Frontend View

The dashboard page at `/tenant/dashboard/` SHALL display:

### KPI Card Row

A row of stat cards showing:
| Card | Source |
|------|--------|
| Booked today | `counts.booked_today` |
| Pending | `counts.pending_today` |
| Confirmed | `counts.confirmed_today` |
| Completed today | `counts.completed_today` |
| Cancelled today | `counts.cancelled_today` |
| Active barbers | `counts.active_barbers` |
| Active services | `counts.active_services` |
| Upcoming days | `counts.upcoming_days_with_bookings` |

### Today's Agenda

A list below the KPI cards showing each appointment with:
- Time range (start_time → end_time, formatted for readability)
- Barber name
- Customer name and phone
- Service name
- Status badge (color-coded)
- CB continuation indicator (if applicable)

### Data Loading

- The dashboard page SHALL call `GET /tenants/{tenant_id}/overview` with the current server date
- The page SHALL use `apiJson` with `{ request: Astro.request }` for server-side fetch
- Errors SHALL be caught and displayed; missing data SHALL NOT break the page

## Frontend Page

| Route | Method | Action |
|-------|--------|--------|
| `/tenant/dashboard` | GET | Server-render KPIs + agenda from overview API |
