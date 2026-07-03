from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select


class TestWorkspaceProfileAPI:
    def test_me_returns_name_and_photo_fields(self, client, seeded, auth_header, api_session_factory):
        from packages.infrastructure.db.models.tenant_user import TenantUser

        s = api_session_factory()
        try:
            user = s.execute(select(TenantUser).where(TenantUser.tenant_id == seeded["tenant_id"])).scalar_one()
            user.photo_url = "https://example.com/me.jpg"
            s.commit()
        finally:
            s.close()

        resp = client.get("/tenants/auth/me", headers=auth_header)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Test Admin"
        assert body["photo_url"] == "https://example.com/me.jpg"

    def test_patch_me_updates_name_and_photo(self, client, seeded, auth_header, api_session_factory):
        from packages.infrastructure.db.models.tenant_user import TenantUser

        resp = client.patch(
            "/tenants/auth/me",
            headers={**auth_header, "content-type": "application/json"},
            json={"name": "Carlos Barber", "photo_url": "https://example.com/carlos.png"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Carlos Barber"
        assert body["photo_url"] == "https://example.com/carlos.png"

        s = api_session_factory()
        try:
            user = s.execute(select(TenantUser).where(TenantUser.tenant_id == seeded["tenant_id"])).scalar_one()
            assert user.name == "Carlos Barber"
            assert user.photo_url == "https://example.com/carlos.png"
        finally:
            s.close()

    def test_patch_me_rejects_invalid_photo_url(self, client, auth_header):
        resp = client.patch(
            "/tenants/auth/me",
            headers={**auth_header, "content-type": "application/json"},
            json={"photo_url": "avatar-file.png"},
        )
        assert resp.status_code == 400, resp.text
        assert "photo_url" in resp.json()["detail"]
