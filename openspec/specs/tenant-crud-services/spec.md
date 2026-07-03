# Tenant CRUD Services Specification

## Purpose

Tenant users manage their service catalog: list, create, edit, and toggle service availability. Services define what the barbershop offers (e.g. Corte, Barba, CB). Every operation is scoped to the authenticated tenant.

## Requirements

### Requirement: List services

The system MUST return all services for the authenticated tenant. Inactive services SHALL be included.

#### Scenario: Happy path — list returns all services

- GIVEN the tenant has 4 services (3 active, 1 inactive)
- WHEN a GET request is sent to `/tenants/{tenant_id}/services`
- THEN the response MUST return 200 with a JSON array of 4 service objects
- AND each object MUST contain `id`, `name`, `code`, `duration_minutes`, `price_cents`, `description`, and `is_active`

#### Scenario: Auth failure returns 401

- GIVEN no valid tenant bearer token
- WHEN a GET request is sent
- THEN the response MUST return 401 Unauthorized

### Requirement: Create service

The system MUST create a new service for the authenticated tenant.

#### Scenario: Happy path — create minimal service

- GIVEN payload `{"name": "Corte", "duration_minutes": 30}`
- WHEN a POST request is sent to `/tenants/{tenant_id}/services`
- THEN the response MUST return 201 Created
- AND `code` MUST default to `"OTHER"`, `price_cents` to `0`, `is_active` to `true`

#### Scenario: Duration below minimum returns 422

- GIVEN a payload with `duration_minutes: 5`
- WHEN a POST request is sent
- THEN the response MUST return 422 Unprocessable Entity

#### Scenario: Duration above maximum returns 422

- GIVEN a payload with `duration_minutes: 500`
- WHEN a POST request is sent
- THEN the response MUST return 422 Unprocessable Entity

### Requirement: Update service

The system MUST update an existing service for the authenticated tenant.

#### Scenario: Happy path — update price and duration

- GIVEN an existing service with `price_cents: 0`
- WHEN a PUT request is sent to `/tenants/{tenant_id}/services/{service_id}` with `{"price_cents": 1500, "duration_minutes": 45}`
- THEN the response MUST return 200 with updated fields

#### Scenario: Toggle service active/inactive

- GIVEN an existing active service
- WHEN a PUT request is sent with `{"is_active": false}`
- THEN the response MUST return 200 with `is_active: false`
- AND the service MUST still appear in the list with `is_active: false`

#### Scenario: Update non-existent service returns 404

- GIVEN a `service_id` that does not exist for this tenant
- WHEN a PUT request is sent
- THEN the response MUST return 404 Not Found

### Requirement: Get single service

The system MUST return a single service by ID, scoped to the tenant.

#### Scenario: Happy path — get by id

- GIVEN an existing service
- WHEN a GET request is sent to `/tenants/{tenant_id}/services/{service_id}`
- THEN the response MUST return 200 with the service object

## Backend Schema

### ServiceUpdate (new)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| name | string(1-120) | No | |
| code | string(1-8) | No | |
| duration_minutes | int (15-480) | No | |
| price_cents | int (>=0) | No | |
| description | string or null | No | |
| is_active | boolean | No | |

## Frontend Pages

| Route | Method | Action |
|-------|--------|--------|
| `/tenant/services/` | GET | List all services in a table |
| `/tenant/services/` | POST | Create service (via hidden `_action=create`) |
| `/tenant/services/{id}/edit` | GET | Show edit form |
| `/tenant/services/{id}/edit` | POST | Update service (via hidden `_action=update`) |
| `/tenant/services/{id}/toggle` | POST | Toggle `is_active` |
