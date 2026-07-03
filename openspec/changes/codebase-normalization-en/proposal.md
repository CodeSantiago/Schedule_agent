# Proposal: Core Domain Identifier Normalization (Slice 1)

## Intent

The domain layer uses Spanish identifiers throughout the Python codebase — `ServiceCode.CORTE`, `enforce_solo_corte`, Spanish inline comments — while the broader codebase convention is English. This creates cognitive friction for contributors and inconsistent naming across the clean architecture boundary. Slice 1 renames pure Python identifiers with zero DB impact.

## Scope

### In Scope

- `ServiceCode` enum members: `CORTE` → `HAIRCUT`, `BARBA` → `BEARD`, `CORTE_Y_BARBA` → `HAIRCUT_AND_BEARD`; `is_cb` property → `is_haircut_and_beard`
- `_SHORT_CODES` dict values in `service_codes.py` (keys stay — they are accepted external input strings)
- `solo_corte` function family: `enforce_solo_corte` → `enforce_haircut_only`, `is_solo_corte_slot` → `is_haircut_only_slot`, `parse_solo_corte` → `parse_haircut_only`
- All imports, re-exports (`__init__.py`), call sites, and `__all__` entries that reference renamed identifiers
- Spanish technical comments and docstrings in `booking.py`, `restrictions.py`, `models.py`
- Route tag `["agenda"]` → `["schedule"]` in `agenda.py`
- Test function names, imports, and references matching renamed identifiers (not test DATA strings like `"SOLO_CORTE"` in mock CSV payloads)

### Out of Scope

- Session state enum values in the DB / LLM prompts (deferred to Slice 2)
- User-visible UX strings (bot replies, dashboard labels, frontend navigation)
- Google Sheets reader constant values — they match tenant sheet column headers
- Tenant-entered data: service names, customer fields, `restrictions` column content
- Migration file historical content (Alembic files are frozen audit records)
- Legacy `solo-tenant-bot/` code
- Any change requiring a DB migration or an LLM prompt behavioral update

## Capabilities

### New Capabilities

None — pure refactor, no new capability.

### Modified Capabilities

None — no spec-level behavioral change. All renames preserve existing behavior.

## Approach

Mechanical rename grouped by concept, each verified with tests after every group:

1. **ServiceCode**: rename enum members in `models.py`, update `is_cb` → `is_haircut_and_beard`, update `_SHORT_CODES` values in `service_codes.py`
2. **SOLO_CORTE**: rename 3 functions in `restrictions.py`, update all imports and call sites across domain, application, API, and tests
3. **Comments**: normalize 3 Spanish inline comments/docstrings to English
4. **Route tag**: `["agenda"]` → `["schedule"]` in `agenda.py`
5. **Run full test suite** to confirm no behavioral regression

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `packages/domain/scheduling/models.py` | Modified | ServiceCode members, is_cb, docstring |
| `packages/domain/scheduling/service_codes.py` | Modified | Dict values, module docstring |
| `packages/domain/scheduling/restrictions.py` | Modified | 3 functions, module docstring, inline comments |
| `packages/domain/scheduling/__init__.py` | Modified | Re-exports, __all__ |
| `packages/domain/scheduling/booking.py` | Modified | Import, call site, comment |
| `packages/domain/scheduling/availability.py` | Modified | Import, call site, docstring |
| `packages/domain/scheduling/errors.py` | Modified | Error class docstring |
| `packages/application/scheduling/booking_service.py` | Modified | Docstrings |
| `packages/application/scheduling/manage_service.py` | Modified | Docstrings |
| `packages/application/intake/__init__.py` | Modified | Inline comment |
| `packages/infrastructure/db/models/scheduling.py` | Modified | Inline comments |
| `apps/api/src/routes/agenda.py` | Modified | Route tag, module docstring |
| `apps/api/src/routes/appointments.py` | Modified | Inline comment |
| `apps/api/src/routes/availability.py` | Modified | Module docstring |
| `tests/scheduling/test_domain.py` | Modified | Imports, function calls, test names |
| `tests/application/test_booking_service.py` | Modified | Imports, function calls, test names |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Missed call site (unused or uncommon path) | Low | Full test suite + grep audit for any remaining old names |
| Test data strings wrongly renamed as identifiers | Low | Careful review of string literals vs Python identifiers |
| Behavioral drift from rename side-effects | Very Low | No logic changes — all changes are 1:1 renames |

## Rollback Plan

`git revert <commit-hash>`. Zero DB state is affected — rollback is instant and safe.

## Dependencies

None.

## Success Criteria

- [ ] `pytest` suite passes (all existing tests with renamed identifiers)
- [ ] Zero remaining `ServiceCode.CORTE`, `ServiceCode.BARBA`, `ServiceCode.CORTE_Y_BARBA` in source (non-test) Python files
- [ ] Zero remaining `enforce_solo_corte`, `is_solo_corte_slot`, `parse_solo_corte` in source Python files
- [ ] Route tag `["agenda"]` removed — `grep` returns no results for `tags=\["agenda"\]`
