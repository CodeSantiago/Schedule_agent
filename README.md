# Barber Agent

Multi-tenant barber booking platform. **Greenfield rebuild** — this
codebase is the long-lived product; the original single-tenant bot is
preserved under `solo-tenant-bot/` and is **legacy reference only**.

> Do not import from `solo-tenant-bot/` into the new code. It exists so
> we can keep consulting its business logic while the new system
> replaces it, module by module.

## What's in this slice

This is **Part 4** of the greenfield rebuild — the final integrated
slice. Part 3 left a working operational core + superadmin auth +
provider-config CRUD + a thin WhatsApp-like webhook intake; Part 4
adds the missing lifecycle endpoints, an admin dashboard, and
integration-prep seams for the real Kapso transport + LLM intent
classifier.

**Product correction (current):** turns are always requested by
WhatsApp. The previous Jinja/FastAPI web UI (booking, agenda, manual
booking, public client booking) has been removed — it was the wrong
frontend architecture. The admin dashboard is now a separate Astro
application that talks to the FastAPI JSON API. Booking, agenda, and
the public client page are no longer product surface.

What Part 4 adds on top of Part 3:

- **Appointment cancel + reschedule.** New `DELETE /tenants/{id}/appointments/{aid}`
  and `POST /tenants/{id}/appointments/{aid}/reschedule` endpoints.
  CB pair behaviour is preserved: cancelling a CB primary also
  cancels the continuation row, and rescheduling a CB primary moves
  the continuation along. The rules reuse `plan_booking` so the
  new slot is gated by the same domain rules as a fresh booking
  (slot grid, past-time, SOLO_CORTE, barber availability,
  double-booking).
- **Operational overview endpoint.** `GET /tenants/{id}/overview`
  returns the KPI counts the dashboard's cards need (booked today,
  pending, confirmed, completed, cancelled, active barbers, active
  services, busy days in the next 7) plus the day's appointment list
  with the CB continuation row already marked.
- **Astro admin dashboard (separate app under `apps/admin-astro/`).**
  Server-rendered pages for login, tenant list, tenant settings,
  provider-config CRUD (typed forms for `whatsapp`/`llm`, raw JSON
  for the rest), and the new-tenant form. No booking/agenda/manual
  booking surface. Talks to the FastAPI JSON API over
  `Authorization: Bearer` (the same token returned by
  `POST /superadmin/auth/login`), stored in an httpOnly cookie.
- **Integration-prep seams (no real implementations).**
  - `MessageTransport` Protocol + `EchoTransport` no-op default in
    `packages/application/messaging/transport.py`. The webhook
    handler routes the reply through the transport resolved from
    the tenant's active `provider_configs` row for
    `kind="whatsapp"`. A real Kapso / Twilio adapter drops in behind
    the same Protocol.
  - `IntentClassifier` Protocol + `DeterministicIntentClassifier`
    default in `packages/application/intake/seam.py`. The webhook
    handler keeps using the deterministic `classify_intent`; a
    future LLM-backed classifier plugs in behind the Protocol
    without changing the handler.
- **Env / config placeholders** for the real integrations in
  `.env.example`: `KAPSO_API_KEY`, `KAPSO_BASE_URL`, `LLM_PROVIDER`,
  `LLM_API_KEY`, `LLM_MODEL`, `SESSION_SECRET`, `PUBLIC_BASE_URL`.
  The application still boots with these unset; the transport
  factory and the intent classifier fall back to the no-op defaults.

Tests cover: cancel + reschedule (CB pair behaviour, past-time
guard, double-booking rejection, missing-id 404), the overview
service (empty day, day with C, day with CB, cancelled-row
counting), the LLM seam (default matches the deterministic
classifier, satisfies the Protocol), the transport seam (echo
success, factory falls back when no active config, factory falls
back for unknown `provider_name`), and the JSON API surface
(login, list tenants, overview, agenda, cancel, provider-config
CRUD).

## Layout

```
barber_agent/
├── apps/
│   ├── api/
│   │   └── src/
│   │       ├── main.py            # FastAPI entry (JSON API only)
│   │       ├── schemas.py         # Pydantic request/response models
│   │       ├── deps.py            # FastAPI dependencies (repos, services)
│   │       └── routes/            # One router per concern
│   │           ├── tenants.py
│   │           ├── barbers.py
│   │           ├── services.py
│   │           ├── schedules.py
│   │           ├── absences.py
│   │           ├── extra_hours.py
│   │           ├── availability.py
│   │           ├── appointments.py   # POST + DELETE + POST reschedule
│   │           ├── agenda.py
│   │           ├── overview.py       # KPI cards + day list
│   │           ├── provider_configs.py
│   │           ├── superadmin.py
│   │           └── webhooks.py       # routes reply through transport seam
│   └── admin-astro/               # NEW: Astro admin dashboard
│       ├── src/
│       │   ├── pages/             # login, logout, admin tenants, settings, provider-configs
│       │   ├── layouts/Base.astro
│       │   ├── components/Topbar.astro
│       │   ├── lib/api.ts         # JSON API client
│       │   ├── lib/session.ts     # cookie + bearer plumbing
│       │   └── styles/app.css     # design tokens + components
│       ├── astro.config.mjs
│       ├── package.json
│       └── README.md
├── packages/
│   ├── domain/
│   │   ├── auth/                  # PasswordHasher + TokenIssuer
│   │   └── scheduling/            # Pure-Python business logic
│   │       ├── errors.py
│   │       └── ...
│   ├── application/
│   │   ├── auth/                  # AuthService (used by Astro via JSON API)
│   │   ├── superadmin/
│   │   ├── providers/
│   │   ├── intake/                # + seam.py (LLM Protocol)
│   │   ├── messaging/             # transport Protocol + EchoTransport
│   │   └── scheduling/
│   │       ├── booking_service.py
│   │       ├── manage_service.py  # cancel + reschedule
│   │       └── overview_service.py
│   └── infrastructure/
│       ├── db/                    # SQLAlchemy models + Alembic
│       └── repositories/
│           └── appointments.py    # + CB partner lookup + status counts
├── tests/                         # backend tests (no Jinja dashboard tests)
├── solo-tenant-bot/               # LEGACY — reference only, do not import
├── alembic.ini
├── pyproject.toml
├── .env.example
└── README.md
```

## Quickstart

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                       # then edit DATABASE_URL
alembic upgrade head                       # applies the migrations
unset DATABASE_URL                         # so the test DB (sqlite) is used
python -m pytest tests/ -v
uvicorn apps.api.src.main:app --reload     # start the JSON API on :8000

# Admin dashboard (separate process)
cd apps/admin-astro
npm install
npm run dev                                # starts on :4321
```

When both servers are up:

- `http://localhost:8000/health`            — liveness + DB ping
- `http://localhost:8000/docs`              — OpenAPI for the JSON API
- `http://localhost:4321/login`             — admin sign-in
- `http://localhost:4321/admin`             — tenant list (after login)
- `http://localhost:4321/admin/tenants/{id}/settings` — settings + provider-configs
- `POST http://localhost:8000/webhooks/whatsapp/{tenant_id}` — provider webhook

## How the admin auth flows

1. The dashboard login form posts `email` + `password` to
   `POST /superadmin/auth/login` (on the FastAPI server).
2. The Astro app stores the returned bearer token in an httpOnly
   cookie (`barber_session`) and the email in a sibling non-httpOnly
   cookie (`barber_email`) for the topbar label.
3. Every admin page resolves the principal from the cookie, then
   forwards the cookie to the FastAPI server when calling the JSON
   API on its behalf.
4. `POST /logout` clears both cookies.

The session secret is `SESSION_SECRET` (env var on the Astro server
— controls `Secure` cookie flag in production). The Astro server
proxies `/api` to the FastAPI process via the Vite dev server in dev
and via `API_BASE_URL` in production.

## Integration-prep seams

### Messaging transport

`packages/application/messaging/transport.py`:

- `MessageTransport` Protocol: `send(to_phone, body, session_id, metadata) -> SendResult`.
- `EchoTransport`: no-op default; returns `delivered=True` with a
  synthetic `provider_message_id`. This is what runs in dev / CI.
- `TransportFactory.for_tenant(tenant_id)`: reads the active
  `provider_configs` row for `kind="whatsapp"`, dispatches by
  `provider_name`. Today every name falls back to `EchoTransport`;
  a future slice adds `KapsoTransport`, `TwilioTransport`, etc.

The webhook handler resolves the transport per request and stamps
the result on the `outgoing_messages` row (`provider_message_id`,
`status="sent" | "failed"`).

### LLM intent

`packages/application/intake/seam.py`:

- `IntentClassifier` Protocol: `classify(text, current_state) -> Intent`.
- `DeterministicIntentClassifier`: default, wraps the existing
  `classify_intent`.

The webhook keeps calling the deterministic function; an LLM
classifier is a one-file change that adds a new implementation and
swaps the dependency. No handler change.

## Legacy business rules preserved

- **CB (Corte y Barba) consumes 2 consecutive 30-min slots.** The
  second slot is persisted as a separate `Appointment` row tagged
  `(CB cont.)`. Cancel cascades to the partner row; reschedule
  moves the partner along.
- **SOLO_CORTE** — barbers restricted to `C` at specific
  (weekday, time) pairs. Enforced in the domain layer, applied
  during reschedule as well as booking.
- **Same-day past-time rejection** — booking attempts at a slot
  that has already started today are rejected with `PastTimeError`.
  Same guard applied to reschedule. Past-day appointments can still
  be cancelled (operators mark no-shows and late cancels after the
  fact).

## API surface (Part 4)

JSON API (existing + new in **bold**):

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/tenants` | Create a tenant. |
| `GET`  | `/tenants/{id}` | Read a tenant. |
| `GET`/`POST` | `/tenants/{id}/barbers` | List / create barbers. |
| `GET`  | `/tenants/{id}/barbers/{barber_id}` | Read a barber. |
| `GET`/`POST` | `/tenants/{id}/services` | List / create services. |
| `GET`/`POST` | `/tenants/{id}/barbers/{barber_id}/schedules` | Weekly schedule entries. |
| `GET`/`POST`/`DELETE` | `/tenants/{id}/barbers/{barber_id}/absences` | Date-specific absences. |
| `GET`/`POST`/`DELETE` | `/tenants/{id}/barbers/{barber_id}/extra-hours` | Date-specific extra hours. |
| `GET`  | `/tenants/{id}/availability?barber_id=&service_id=&date=` | List bookable starting slots. |
| `GET`/`POST` | `/tenants/{id}/appointments` | List / book appointments. CB returns 2 rows. |
| `DELETE` | `/tenants/{id}/appointments/{aid}` | **NEW: cancel (and CB partner)** |
| `POST` | `/tenants/{id}/appointments/{aid}/reschedule` | **NEW: reschedule (and CB partner)** |
| `GET`  | `/tenants/{id}/agenda?date=&barber_id=` | Day agenda per tenant. |
| `GET`  | `/tenants/{id}/overview?date=` | **NEW: KPI cards + day list** |
| `GET`  | `/health` | Liveness + DB ping. |
| `POST` | `/superadmin/auth/login` | Email+password login, returns bearer token. |
| `GET`/`POST` | `/superadmin/tenants` | List / create tenants. |
| `GET`  | `/superadmin/tenants/{id}` | Read a tenant. |
| `PATCH` | `/superadmin/tenants/{id}/status` | Update tenant lifecycle status. |
| `GET`/`POST` | `/tenants/{id}/provider-configs` | List / create per-tenant provider wiring rows. |
| `GET`  | `/tenants/{id}/provider-configs/{cid}` | Read a single provider config. |
| `PATCH` | `/tenants/{id}/provider-configs/{cid}` | Update a provider config. |
| `POST` | `/tenants/{id}/provider-configs/{cid}/activate` | Activate a provider config (deactivates siblings). |
| `DELETE` | `/tenants/{id}/provider-configs/{cid}` | Delete a provider config. |
| `POST` | `/webhooks/whatsapp/{tenant_id}` | Inbound WhatsApp-like webhook (deterministic intake). |

The admin dashboard lives in a separate Astro application
(`apps/admin-astro/`) that calls the JSON API as a backend. It does
not expose any HTTP surface of its own besides its own pages.

## What's NOT in this slice (out of scope, by design)

- No real Kapso / Twilio / WhatsApp Cloud transport — the
  `EchoTransport` covers the dev path. The Protocol is wired so a
  real adapter is a single-file drop-in.
- No real LLM intent classifier — `DeterministicIntentClassifier`
  is the default. The Protocol is wired so an LLM implementation
  is a single-file drop-in.
- No webhook signature verification / IP allowlist — provider-side
  auth ships alongside the real Kapso adapter in a future slice.
- No booking / agenda / manual-booking / public-booking web surface
  — turns are always requested by WhatsApp.
- No Postgres-container integration tests. The test suite runs
  against an in-memory sqlite engine with the JSONB / ENUM shims
  installed in `tests/conftest.py`; the migration path is exercised
  in production only.

## `solo-tenant-bot/` — legacy

The folder is intentionally untouched. It contains the original
single-tenant bot (`api.py`, `sheets.py`, the technical audit). We
keep it around to consult behaviour while rebuilding. It must not
be imported, packaged, or run from the new code.
