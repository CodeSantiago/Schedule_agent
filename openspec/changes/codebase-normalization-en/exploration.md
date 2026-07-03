## Exploration: Codebase Normalization — English Technical Artifacts

### Current State

The codebase is a multi-tenant barber booking platform (FastAPI + SQLAlchemy 2.0 + Astro SSR)
that started as a greenfield rebuild of a legacy Spanish-language bot. The original domain
concepts (service types, booking states, restrictions) were modeled in Spanish and those
names persist throughout the non-user-visible layers: domain enum values, DB session states,
function/method names, constants, and internal docstrings.

User-facing UX (bot replies, dashboard labels, error messages) is in Spanish by design
— that target audience is Argentine barbershops. The task is to **aggressively normalize
everything else** to English while keeping UX untouched.

---

### 1. Categorization of Spanish Technical Artifacts Found

#### 1A. Domain Enum Values (Core Business Identifiers)

**Location**: `packages/domain/scheduling/models.py`

```python
class ServiceCode(str, Enum):
    CORTE = "C"              # → should be HAIRCUT
    BARBA = "B"              # → should be BEARD
    CORTE_Y_BARBA = "CB"    # → should be HAIRCUT_AND_BEARD
    OTHER = "OTHER"
```

**Impact**: The Python enum member names are `CORTE`, `BARBA`, `CORTE_Y_BARBA`. These
appear in every file that imports `ServiceCode` (~15+ files across domain, application,
API layers). The DB column `services.code` stores the *short values* `"C"`, `"B"`, `"CB"`,
not the enum member names — so renaming the Python enum members does NOT require a DB
migration. The `parse_service_code()` function in `service_codes.py` also maps Spanish
long-form strings (`"CORTE"`, `"BARBA"`, `"CORTE_Y_BARBA"`, `"CORTYBARBA"`) that
come from tenant data. Those input strings are **external data** (tenants type them),
so they must remain as accepted parse inputs.

**Scope**: In-scope for rename. The enum member names are pure Python identifiers.

---

#### 1B. SOLO_CORTE Restriction — Function Names & Constants

**Location**: `packages/domain/scheduling/restrictions.py`

```python
def enforce_solo_corte(service, restrictions, weekday, slot_start): ...
def is_solo_corte_slot(restrictions, weekday, slot_start): ...
def parse_solo_corte(restrictions): ...
```

**Impact**: These functions are called from `booking.py`, `availability.py`, and
`restrictions.py` itself. `SOLO_CORTE` is a domain restriction name ("only haircut
at certain slots") that uses Spanish in its identifier. The actual restriction string
stored in the DB (`barbers.restrictions` column) is *not* Spanish — it uses compact
English codes like `"mon:11:30,19:30"`. So the DB is safe. Only Python identifiers
and their call sites need updating.

**Scope**: In-scope for rename.

---

#### 1C. Conversation Session States — DB Enum Values (High Risk)

**Location**:
- `packages/infrastructure/db/models/messaging.py` — `SESSION_STATE_VALUES` tuple (18 entries)
- `packages/infrastructure/db/migrations/versions/0001_initial.py` — creates the enum
- `packages/infrastructure/db/migrations/versions/0009_location_and_states.py` — adds new states
- `packages/infrastructure/db/migrations/versions/0010_fix_sqlite_schema_drift.py` — updates SQLite constraint
- `packages/infrastructure/llm/openrouter.py` — `_VALID_STATES` frozenset
- `packages/infrastructure/llm/prompts.py` — state names in LLM system prompt
- `packages/application/intake/__init__.py` — state references in `classify_intent`

Spanish state values:
```
"inicio", "esperando_menu", "esperando_servicio", "esperando_dia",
"esperando_barbero", "esperando_horario", "esperando_nombre",
"confirmacion_turno", "turno_confirmado", "esperando_cancelacion",
"turno_cancelado", "esperando_reagendar", "seleccion_turno_cancelar",
"seleccion_turno_reagendar", "seleccion_nuevo_horario", "turno_reagendado",
"idle", "closed"
```

**Impact: HIGH** — These are actual Postgres enum values stored in
`conversation_sessions.state`. Renaming them requires:
1. An Alembic migration that does `ALTER TYPE session_state ADD VALUE` for new names
   and a data migration to update existing rows
2. Coordinated code changes across 5+ files
3. LLM prompt updates (the LLM is instructed to output these state names)
4. Existing DB rows with old values must be handled during deploy

**Scope**: Should be done, but as a SEPARATE SLICE with a DB migration.

---

#### 1D. LLM Prompts — Spanish System Instructions

**Location**: `packages/infrastructure/llm/prompts.py`

The entire system prompt and conversation prompt are in Spanish:
```python
parts = [f"Sos un asistente de WhatsApp para {tenant_name}, una barbería."]
# More Spanish text throughout...
```

**Impact**: This is the bot's **user-facing voice**. The LLM communicates with
customers in Spanish by design. Per the change contract, this **MUST stay Spanish**.

The prompt references Spanish state names (`"esperando_menu"`, `"turno_confirmado"`, etc.)
in the state transition table. If we rename the session states, this prompt must also
be updated to tell the LLM to output the new English state names. But the *reply text*
the LLM generates for customers stays Spanish.

**Scope**: OUT of scope for identifiers (it IS the UX). But the embedded state names
must be updated IF the session states are renamed.

---

#### 1E. Intake Service — Reply Strings (User-Visible UX)

**Location**: `packages/application/intake/__init__.py`

All reply strings in `classify_intent()` and `IntakeService` are in Spanish:
```python
"Perfecto. Decime el servicio..."
"Faltan datos para agendar el turno..."
"Ese horario ya está reservado..."
```

**Scope**: OUT of scope — these are the bot's customer-facing replies.

---

#### 1F. Frontend Dashboard — Labels & Navigation (User-Visible UX)

**Location**: `apps/admin-astro/src/pages/tenant/dashboard.astro`

Spanish labels:
```javascript
const labels = { pending: "Pendiente", confirmed: "Confirmado", ... };
// <a class="btn" href="...">Configuración</a>
// <a class="btn" href="...">Barberos</a>
// KPI: "Reservados hoy", "Pendientes", etc.
```

**Scope**: OUT of scope — these are the dashboard UX for Spanish-speaking staff.

---

#### 1G. Google Sheets Reader — Spanish Column Headers (External Contract)

**Location**: `packages/application/providers/sheets_reader.py`

```python
WEEKDAYS = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES")
BARBERO_HEADER = "BARBERO"
BOT_KEYWORD = "BOT"
ACTIVE_VALUES = frozenset({"activo", "active", "on", "true", "si", "yes"})
```

**Impact**: These constants match external Google Sheets column headers that real
tenants use. Renaming the Python constants is cosmetic and low-risk. Changing the
actual values (e.g., `WEEKDAYS[0]` from `"LUNES"` to `"MONDAY"`) would break sheet
reads for all existing tenants — **unless the sheet headers are also updated**.

**Scope**: The Python constant NAMES are in-scope for rename. The VALUES they hold
are external contracts and should stay as-is unless a sheet migration plan exists.

---

#### 1H. Internal Comments & Docstrings (Spanish)

Found in several files, especially:
- `packages/domain/scheduling/booking.py` — `# legacy rule: "mismo día, hora pasada → rechazar"`
- `packages/domain/scheduling/restrictions.py` — `# "A las X con Y solo se aceptan cortes"`
- `packages/domain/scheduling/models.py` — comment referencing `CB (Corte + Barba)`
- Migration files — docstrings in English (fine)

**Scope**: In-scope for rename. Low risk, purely cosmetic.

---

#### 1I. Routes / API Contracts — URL Prefixes & Tags

**Location**: Route files in `apps/api/src/routes/`

Route tags like `["agenda"]` are Spanish. The model field names like `customer_dni`,
`customer_last_name`, `appointment_date` are already in English. Route prefixes use
English (`/tenants/`, `/appointments/`, etc.).

The `/agenda` prefix is Spanish. Renaming it to `/agenda` → `/schedule` or keeping
it would be a breaking API change if external clients call it. Since this is an
internal API consumed by the Astro frontend only, this is viable but requires
coordinated frontend updates.

**Scope**: Route tags in-scope. Route URL paths: discuss with team (API contract).

---

#### 1J. Solo-Tenant-Bot — Legacy Code

**Location**: `solo-tenant-bot/api.py` and `solo-tenant-bot/sheets.py`

Heavy Spanish throughout the entire codebase:
```python
SERVICIOS = {"C": "Corte", "B": "Barba", "CB": "Corte y Barba"}
BARBEROS, DIAS_VALIDOS, HORARIOS  # imported from sheets.py
SOLO_CORTE = { ... }
```

**Scope**: This is legacy code (not running in production AFAICT). Out of scope for
the initial change — or a separate "legacy" slice.

---

#### 1K. Test Files

**Location**: `tests/`

Test files reference Spanish domain concepts (e.g., `test_cb_long_form_classified_as_cb`)
but the test code itself is English. No Spanish identifiers in fixture names or
test function names. Test data includes Spanish values that match the domain.

**Scope**: Low risk. Update test references to new names when renaming domain concepts.

---

### 2. Spanish Artifacts That MUST Stay (User-Visible UX)

| Artifact | Location | Reason |
|----------|----------|--------|
| LLM system prompt text | `prompts.py` | Bot speaks to customers in Spanish |
| LLM reply field values | `prompts.py` | Bot-generated replies are customer-facing |
| Intake service replies | `intake/__init__.py` | Bot responses to customers |
| Frontend dashboard labels | `dashboard.astro` + all `.astro` pages | Staff UI in Spanish |
| Frontend navigation text | All `.astro` pages | Staff navigation in Spanish |
| Bot greeting text | `intake/__init__.py` GREETING | Customer greeting |
| `customer_dni` field | `schemas.py`, `appointments.py` | DNI is Spanish concept; renaming breaks API |
| Service names in DB | `services.name` column | Tenant-entered data, not code |
| `customer_name`, `customer_phone`... | All DB models | User data, not code |
| Spanish in `.env` / `.env.example` | Config files | Site-specific configuration |

---

### 3. Highest-Risk Areas for Aggressive Renaming

| Area | Risk Level | Why |
|------|-----------|-----|
| **Session state enum** (DB) | **CRITICAL** | Postgres enum + existing DB rows + LLM prompt contract + migration coordination |
| **LLM state names** (prompts) | **HIGH** | LLM must be told to output new state names; old sessions have old states |
| **Service codes** (DB values) | **MEDIUM** | `"C"`, `"B"`, `"CB"` are stored in DB; renaming requires data migration |
| **Sheets reader constants** | **MEDIUM** | Values match real tenant Google Sheets column headers |
| **Migration files** | **MEDIUM** | Historical migration files reference enum values; must stay consistent |
| **`/agenda` route path** | **LOW** | Only consumed by Astro frontend; coordinated update works |
| **Enum member names** (Python) | **LOW** | Pure code change; no DB impact |
| **Function names** | **LOW** | Mechanical rename across call sites |
| **Comments/docstrings** | **LOW** | Cosmetic, no runtime impact |

---

### 4. Recommended Change Boundary

**First slice (recommended) — "Core Domain Identifiers"**

Rename only the **pure Python identifiers** that have NO persisted data impact:

1. `ServiceCode.CORTE` → `ServiceCode.HAIRCUT`
2. `ServiceCode.BARBA` → `ServiceCode.BEARD`
3. `ServiceCode.CORTE_Y_BARBA` → `ServiceCode.HAIRCUT_AND_BEARD`
4. `enforce_solo_corte()` → `enforce_haircut_only()`
5. `is_solo_corte_slot()` → `is_haircut_only_slot()`
6. `parse_solo_corte()` → `parse_haircut_only()`
7. All `SOLO_CORTE` references in comments, docstrings, variable names
8. All Spanish internal comments/docstrings across the codebase
9. Update `parse_service_code()`: rename long-form map keys internally (not the accepted input values)
10. Route tag `["agenda"]` → `["schedule"]`

**Why this slice**: It is large enough to be valuable (cleans up the core domain
vocabulary), but still reviewable (~200-300 changed lines spread across ~15 files).
No DB migration needed. No LLM prompt behavioral change needed.

**Second slice (follow-up) — "Session States Migration"**

1. Rename all 16 Spanish session state values to English in the Python constant
2. Create Alembic migration for the Postgres enum (`ALTER TYPE ... ADD VALUE`)
3. Create data migration for existing rows
4. Update `_VALID_STATES` in `openrouter.py`
5. Update LLM prompt state table to reference English state names
6. Update SQLite shim constraint in migration 0010

**Why separate**: Requires DB migration, LLM prompt retraining verification,
and careful deploy sequencing. Much higher risk.

**Third slice (optional) — "Cleanup"**

1. Google Sheets reader constant names
2. Legacy `solo-tenant-bot/` code (if worth touching)
3. Any remaining docstring Spanish

---

### 5. One Change or Chained Slices?

**Definitely split into 2-3 chained PRs**.

The first slice is pure code (no migration) and can ship independently. The second
slice depends on the first (the state names and prompt changes are coupled) but
carries enough risk (DB migration + LLM contract) that it deserves its own review.

| Slice | Files | DB Migration | Lines | Risk |
|-------|-------|-------------|-------|------|
| 1. Core Domain Identifiers | ~15 | No | ~200-350 | Low |
| 2. Session States Migration | ~8 | Yes | ~250-400 | High |
| 3. Cleanup (sheets, legacy) | ~5 | No | ~50-150 | Low |

The first slice is a good candidate for a single PR. The second slice may need
further chaining depending on how the migration logic is structured.

---

### Recommendations

| Aspect | Decision |
|--------|----------|
| **Change boundary** | Slice 1 first: rename `ServiceCode` enum members, `SOLO_CORTE` functions, all Spanish comments/docstrings, route tag |
| **Chained PRs** | **Yes** — at least 2 (Slice 1 → Session States), possibly 3 |
| **DB migration needed** | Slice 1: No. Slice 2: Yes (session states) |
| **LLM prompt changes** | Slice 1: No. Slice 2: Yes (state names in prompt table) |
| **Highest risk** | Session state enum rename — touches DB, LLM, migration files |
| **Ready for proposal** | **Yes** — proceed with `sdd-propose` for Slice 1 |

### Ready for Proposal

Yes. Slice 1 is well-bounded, reviewable, and risk-contained. The orchestrator should
launch `sdd-propose` for a change named `codebase-normalization-en`.
