from __future__ import annotations


class TestTenantUserRoles:
    def test_owner_can_create_barber_with_linked_barber(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/users",
            headers={**auth_header, "content-type": "application/json"},
            json={
                "email": "barber@test.local",
                "password": "test123",
                "name": "Pedro Barber",
                "role": "barber",
                "barber_id": str(seeded["barber_id"]),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["role"] == "barber"
        assert body["barber_id"] == str(seeded["barber_id"])

    def test_barber_role_requires_linked_barber(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/users",
            headers={**auth_header, "content-type": "application/json"},
            json={
                "email": "nolink@test.local",
                "password": "test123",
                "name": "No Link",
                "role": "barber",
            },
        )
        assert resp.status_code == 400, resp.text
        assert "requires a linked barber_id" in resp.json()["detail"]

    def test_owner_cannot_create_admin_anymore(
        self, client, seeded, auth_header
    ) -> None:
        resp = client.post(
            f"/tenants/{seeded['tenant_id']}/users",
            headers={**auth_header, "content-type": "application/json"},
            json={
                "email": "adminish@test.local",
                "password": "test123",
                "name": "Wrong Role",
                "role": "admin",
            },
        )
        assert resp.status_code == 400, resp.text
        assert "Tenant owners can assign only" in resp.json()["detail"]

    def test_update_to_non_barber_clears_existing_barber_link(
        self, client, seeded, auth_header
    ) -> None:
        create_resp = client.post(
            f"/tenants/{seeded['tenant_id']}/users",
            headers={**auth_header, "content-type": "application/json"},
            json={
                "email": "convert@test.local",
                "password": "test123",
                "name": "Convert Me",
                "role": "barber",
                "barber_id": str(seeded["barber_id"]),
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        user_id = create_resp.json()["id"]

        update_resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/users/{user_id}",
            headers={**auth_header, "content-type": "application/json"},
            json={
                "role": "staff",
                "barber_id": None,
                "is_active": True,
            },
        )
        assert update_resp.status_code == 200, update_resp.text
        body = update_resp.json()
        assert body["role"] == "staff"
        assert body["barber_id"] is None

    def test_update_to_barber_without_link_is_rejected(
        self, client, seeded, auth_header
    ) -> None:
        create_resp = client.post(
            f"/tenants/{seeded['tenant_id']}/users",
            headers={**auth_header, "content-type": "application/json"},
            json={
                "email": "staffonly@test.local",
                "password": "test123",
                "name": "Staff Only",
                "role": "staff",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        user_id = create_resp.json()["id"]

        update_resp = client.patch(
            f"/tenants/{seeded['tenant_id']}/users/{user_id}",
            headers={**auth_header, "content-type": "application/json"},
            json={
                "role": "barber",
                "barber_id": None,
                "is_active": True,
            },
        )
        assert update_resp.status_code == 400, update_resp.text
        assert "requires a linked barber_id" in update_resp.json()["detail"]
