# Tenant CRUD Barbers Specification

## Purpose

Tenant users manage their barbers: list all, create, edit fields, and deactivate (soft-delete). Every operation MUST be tenant-scoped.

## Requirements

### Requirement: List barbers

MUST return all barbers for the tenant (active and inactive).

#### Scenario: Happy path

- GIVEN the tenant has 3 barbers (2 active, 1 inactive)
- WHEN a GET request is sent to `/tenants/{tenant_id}/barbers`
- THEN response MUST be 200 with a JSON array of 3 barber objects containing `id`, `name`, `restrictions`, `is_active`

#### Scenario: Auth failure

- GIVEN no valid tenant bearer token
- WHEN a GET request is sent
- THEN response MUST be 401

#### Scenario: Wrong tenant

- GIVEN the bearer token belongs to a different tenant
- WHEN a GET request is sent
- THEN response MUST be 403

### Requirement: Create barber

MUST create a new barber for the tenant.

#### Scenario: Happy path

- GIVEN payload `{"name": "Carlos"}`
- WHEN a POST request is sent
- THEN response MUST be 201 with the new barber object, `is_active` defaulting to `true`

#### Scenario: Empty name

- GIVEN payload with `name: ""`
- WHEN a POST request is sent
- THEN response MUST be 422

### Requirement: Update barber

MUST update an existing barber for the tenant.

#### Scenario: Update name

- GIVEN an existing barber
- WHEN a PUT request is sent to `/tenants/{tenant_id}/barbers/{barber_id}` with `{"name": "Carlos Updated"}`
- THEN response MUST be 200 with updated name, unchanged fields preserved

#### Scenario: Soft-delete

- GIVEN an active barber
- WHEN a PUT request is sent with `{"is_active": false}`
- THEN response MUST be 200 with `is_active: false`, barber still visible in list

#### Scenario: Non-existent barber

- GIVEN a `barber_id` not belonging to this tenant
- WHEN a PUT request is sent
- THEN response MUST be 404

### Requirement: Get single barber

MUST return one barber by ID, tenant-scoped.

#### Scenario: Happy path

- GIVEN an existing barber
- WHEN a GET request is sent to `/tenants/{tenant_id}/barbers/{barber_id}`
- THEN response MUST be 200 with the barber object

#### Scenario: Not found

- GIVEN a `barber_id` that does not exist
- WHEN a GET request is sent
- THEN response MUST be 404

## Backend Schema

### BarberUpdate (new): all fields optional

| Field | Type | Notes |
|-------|------|-------|
| name | string(1-120) | |
| restrictions | string(max 64) or null | |
| is_active | boolean | |

## Frontend Pages

| Route | Method | Action |
|-------|--------|--------|
| `/tenant/barbers/` | GET | Table list |
| `/tenant/barbers/` | POST | Create (hidden `_action=create`) |
| `/tenant/barbers/{id}/edit` | GET | Edit form |
| `/tenant/barbers/{id}/edit` | POST | Update (hidden `_action=update`) |
| `/tenant/barbers/{id}/toggle` | POST | Toggle `is_active` |
