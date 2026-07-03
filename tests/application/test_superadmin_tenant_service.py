"""Application-layer tests for `SuperadminTenantService`.

Cross-tenant operations: list, create, get-by-id, status update.
"""

from __future__ import annotations

import pytest

from packages.application.superadmin import SuperadminTenantService


@pytest.fixture()
def svc(session) -> SuperadminTenantService:
    return SuperadminTenantService(session)


class TestSuperadminTenantService:
    def test_create_persists_tenant_and_settings(
        self, svc: SuperadminTenantService
    ) -> None:
        tenant = svc.create_tenant(
            name="Acme",
            slug="acme",
            timezone="America/Argentina/Buenos_Aires",
            status="trial",
            initial_settings={"language": "es-AR"},
        )
        assert tenant.id is not None
        assert tenant.slug == "acme"
        settings = svc.get(tenant.id)
        assert settings is not None

    def test_create_duplicate_slug_raises(
        self, svc: SuperadminTenantService
    ) -> None:
        svc.create_tenant(name="a", slug="dup")
        with pytest.raises(ValueError):
            svc.create_tenant(name="b", slug="dup")

    def test_list_tenants_returns_all(
        self, svc: SuperadminTenantService
    ) -> None:
        a = svc.create_tenant(name="a", slug="a")
        b = svc.create_tenant(name="b", slug="b")
        rows = svc.list_tenants()
        # Both should be there. The order is by `created_at DESC`;
        # sqlite's `func.now()` is resolved at flush time, so the two
        # rows can have the same timestamp — we do not pin the order
        # here, only the presence.
        ids = {t.id for t in rows}
        assert a.id in ids and b.id in ids

    def test_list_tenants_filters_by_status(
        self, svc: SuperadminTenantService
    ) -> None:
        a = svc.create_tenant(name="a", slug="a", status="trial")
        b = svc.create_tenant(name="b", slug="b", status="active")
        rows = svc.list_tenants(status="active")
        assert [t.id for t in rows] == [b.id]

    def test_update_status(self, svc: SuperadminTenantService) -> None:
        tenant = svc.create_tenant(name="a", slug="a", status="trial")
        updated = svc.update_status(tenant.id, "suspended")
        assert updated is not None
        assert updated.status == "suspended"
        # Update on a missing tenant is a no-op.
        from uuid import uuid4

        assert svc.update_status(uuid4(), "active") is None

    def test_soft_delete_marks_churned_and_preserves_row(
        self, svc: SuperadminTenantService
    ) -> None:
        tenant = svc.create_tenant(name="Acme", slug="acme", status="active")
        result = svc.soft_delete(tenant.id)
        assert result is not None
        assert result.id == tenant.id
        assert result.status == "churned"
        # Row must still be retrievable — soft delete never removes data.
        assert svc.get(tenant.id) is not None

    def test_soft_delete_is_idempotent(self, svc: SuperadminTenantService) -> None:
        tenant = svc.create_tenant(name="Acme", slug="acme", status="active")
        first = svc.soft_delete(tenant.id)
        second = svc.soft_delete(tenant.id)
        assert first is not None and second is not None
        assert first.status == "churned"
        assert second.status == "churned"

    def test_soft_delete_missing_tenant_returns_none(
        self, svc: SuperadminTenantService
    ) -> None:
        from uuid import uuid4

        assert svc.soft_delete(uuid4()) is None
