"""Application-layer tests for `ProviderConfigService`.

Pins the per-tenant CRUD + the "at most one active config per kind"
invariant. Uses the in-memory sqlite engine from the root conftest.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.application.providers import (
    ProviderConfigService,
    UnknownProviderKindError,
)


@pytest.fixture()
def provider_svc(session, tenant_id) -> ProviderConfigService:
    return ProviderConfigService(session, tenant_id)


class TestProviderConfigService:
    def test_create_persists_a_row(self, provider_svc) -> None:
        row = provider_svc.create(
            kind="whatsapp",
            label="Kapso prod",
            provider_name="kapso",
            credentials={"api_key": "secret"},
            settings={"webhook_url": "https://x"},
            is_active=True,
        )
        assert row.id is not None
        assert row.kind == "whatsapp"
        assert row.label == "Kapso prod"
        assert row.is_active is True

    def test_unknown_kind_raises(self, provider_svc) -> None:
        with pytest.raises(UnknownProviderKindError):
            provider_svc.create(
                kind="telegram",
                label="x",
                provider_name="x",
            )

    def test_empty_label_rejected(self, provider_svc) -> None:
        with pytest.raises(ValueError):
            provider_svc.create(
                kind="whatsapp",
                label="   ",
                provider_name="kapso",
            )

    def test_activate_deactivates_siblings(self, provider_svc, tenant_id) -> None:
        a = provider_svc.create(
            kind="whatsapp", label="A", provider_name="kapso", is_active=True
        )
        b = provider_svc.create(
            kind="whatsapp", label="B", provider_name="twilio", is_active=False
        )
        # Activate B — A must auto-deactivate.
        provider_svc.activate(b.id)
        provider_svc.session.commit()
        active = provider_svc.get_active_for_kind("whatsapp")
        assert active is not None
        assert active.id == b.id
        # Re-read A to confirm the bit flipped.
        assert provider_svc.get(a.id).is_active is False  # type: ignore[union-attr]

    def test_update_can_flip_label(self, provider_svc) -> None:
        row = provider_svc.create(
            kind="llm", label="groq-old", provider_name="groq", is_active=True
        )
        provider_svc.update(row.id, label="groq-new")
        provider_svc.session.commit()
        assert provider_svc.get(row.id).label == "groq-new"  # type: ignore[union-attr]

    def test_update_activating_via_patch_deactivates_sibling(
        self, provider_svc
    ) -> None:
        """Regression: PATCH ... is_active=true must deactivate any sibling
        BEFORE activating the target, so the partial unique index
        `uq_provider_active_per_kind` is never violated inside a single
        statement batch.
        """
        a = provider_svc.create(
            kind="whatsapp", label="A", provider_name="kapso", is_active=True
        )
        b = provider_svc.create(
            kind="whatsapp", label="B", provider_name="twilio", is_active=False
        )
        # PATCH-equivalent: flip the inactive row ON. The service must
        # flip A off, flush, then flip B on — not the other way around.
        provider_svc.update(b.id, is_active=True)
        provider_svc.session.commit()

        # Exactly one active row per kind, and it's the one we just
        # activated.
        assert provider_svc.get(a.id).is_active is False  # type: ignore[union-attr]
        assert provider_svc.get(b.id).is_active is True  # type: ignore[union-attr]
        active = provider_svc.get_active_for_kind("whatsapp")
        assert active is not None
        assert active.id == b.id

    def test_deactivate(self, provider_svc) -> None:
        row = provider_svc.create(
            kind="calendar", label="google", provider_name="google_calendar", is_active=True
        )
        assert provider_svc.deactivate(row.id) is True
        provider_svc.session.commit()
        assert provider_svc.get(row.id).is_active is False  # type: ignore[union-attr]
        # Idempotent: deactivating again is a no-op.
        assert provider_svc.deactivate(row.id) is False

    def test_delete(self, provider_svc) -> None:
        row = provider_svc.create(
            kind="sms", label="twilio-sms", provider_name="twilio"
        )
        assert provider_svc.delete(row.id) is True
        provider_svc.session.commit()
        assert provider_svc.get(row.id) is None
        # Deleting again is a no-op.
        assert provider_svc.delete(row.id) is False
