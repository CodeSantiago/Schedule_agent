"""Per-tenant LLM classifier factory.

Follows the same pattern as ``TransportFactory`` in
``packages.application.messaging.transport``: reads the active
``provider_configs`` row for ``kind="llm"`` and builds the matching
intent classifier.

Resolution order
----------------
1. Tenant has an active ``kind="llm"`` config with a non-empty
   ``credentials.api_key`` → build the configured classifier (e.g.
   ``OpenRouterIntentClassifier``) using the tenant's credentials and
   settings.
2. Tenant has an active config but no key → return the deterministic
   classifier (fail-closed per tenant).
3. No active config → fall back to global environment variables
   (``OPENROUTER_API_KEY``, …) and return an LLM classifier when
   those are set, or the deterministic classifier otherwise.  This
   preserves backward compatibility for tenants that have not created
   an LLM provider config yet.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


class LLMClassifierFactory:
    """Resolve the right ``IntentClassifier`` for a tenant.

    Usage::

        factory = LLMClassifierFactory(session_factory)
        classifier = factory.for_tenant(tenant_id)
        intent = classifier.classify("quiero turno", current_state="start")
    """

    def __init__(self, session_factory) -> None:  # type: ignore[no-untyped-def]
        self._session_factory = session_factory

    def for_tenant(self, tenant_id: UUID) -> Any:
        """Resolve an ``IntentClassifier`` for ``tenant_id``.

        Returns an object that satisfies the ``IntentClassifier``
        Protocol (see ``packages.application.intake.seam``).
        """
        from packages.infrastructure.repositories import ProviderConfigRepository

        with self._session_factory() as session:
            repo = ProviderConfigRepository(session, tenant_id)
            active = repo.get_active_for_kind("llm")
            if active is not None:
                return self._build(active)
        # No active LLM config → fall back to global env vars.
        from packages.infrastructure.llm.openrouter import (
            build_openrouter_classifier,
        )

        return build_openrouter_classifier()

    @staticmethod
    def _build(config: Any) -> Any:  # type: ignore[name-defined]
        """Build a classifier from a ``ProviderConfig`` row.

        Reads ``credentials.api_key`` and (optionally)
        ``settings.model`` and ``settings.base_url`` from the tenant's
        config.
        """
        creds = config.credentials or {}
        sets = config.settings or {}

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            # Active config exists but no key → deterministic.
            from packages.application.intake.seam import (
                DeterministicIntentClassifier,
            )

            return DeterministicIntentClassifier()

        model = (sets.get("model") or "").strip() or None
        base_url = (sets.get("base_url") or "").strip() or None

        from packages.infrastructure.llm.openrouter import (
            OpenRouterIntentClassifier,
        )

        return OpenRouterIntentClassifier(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
