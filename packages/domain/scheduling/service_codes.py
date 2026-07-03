"""Shared parsing of the `services.code` column to a `ServiceCode`.

The booking application service and the availability route both need to
translate the short string stored on a `Service` row (e.g. `"C"`, `"CB"`,
or the long `"CORTE_Y_BARBA"`) into the canonical `ServiceCode` enum that
the domain layer uses. Keeping the mapping in one place avoids the
silent semantic divergence the Part 2 verification flagged, where a
tenant using the long form got `OTHER` from availability but the right
code from booking — which made haircut-only filtering inconsistent
between the two endpoints.

The function is intentionally tolerant: whitespace and case differences
are accepted, and unknown values fall back to `ServiceCode.OTHER` so a
new service that the platform does not yet recognise does not break the
booking flow.
"""

from __future__ import annotations

from packages.domain.scheduling.models import ServiceCode


# Short codes → canonical ServiceCode. Both the legacy short form
# ("C" / "B" / "CB") and the long human-readable form
# ("CORTE" / "BARBA" / "CORTE_Y_BARBA") are accepted so tenants can use
# whichever they prefer without breaking availability vs. booking.
_SHORT_CODES: dict[str, ServiceCode] = {
    "C": ServiceCode.HAIRCUT,
    "CORTE": ServiceCode.HAIRCUT,
    "B": ServiceCode.BEARD,
    "BARBA": ServiceCode.BEARD,
    "CB": ServiceCode.HAIRCUT_AND_BEARD,
    "CORTE_Y_BARBA": ServiceCode.HAIRCUT_AND_BEARD,
    "CORTYBARBA": ServiceCode.HAIRCUT_AND_BEARD,
}


def parse_service_code(raw: str | None) -> ServiceCode:
    """Map a `services.code` string to a `ServiceCode` enum value.

    Accepts the legacy short codes (`"C"`, `"B"`, `"CB"`) and the long
    human-readable forms (`"CORTE"`, `"BARBA"`, `"CORTE_Y_BARBA"`).
    Whitespace and case are normalised. Unknown values fall back to
    `ServiceCode.OTHER` so the booking flow keeps working while a tenant
    operator reclassifies them.
    """
    if not raw:
        return ServiceCode.OTHER
    normalised = raw.strip().upper().replace(" ", "_")
    return _SHORT_CODES.get(normalised, ServiceCode.OTHER)
