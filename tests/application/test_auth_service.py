"""Application-layer tests for `AuthService`.

Uses the in-memory sqlite engine from the root conftest. Verifies:

- `create_superadmin` is idempotent on email and hashes the password.
- `authenticate` returns an issued token on correct credentials and
  raises `AuthError` on every failure mode (unknown email, wrong
  password, deactivated user).
- `verify_bearer` resolves a fresh token into a `Principal` and rejects
  unknown / revoked tokens.
- `revoke` flips the `revoked_at` column and a subsequent
  `verify_bearer` raises `AuthError`.
"""

from __future__ import annotations

import pytest

from packages.application.auth import AuthError, AuthService


@pytest.fixture()
def auth_service(session) -> AuthService:
    return AuthService(session)


class TestCreateSuperadmin:
    def test_creates_a_user(self, auth_service: AuthService) -> None:
        user = auth_service.create_superadmin("owner@example.com", "secret-pw")
        assert user.id is not None
        assert user.email == "owner@example.com"
        assert user.password_hash.startswith("pbkdf2_sha256$")

    def test_idempotent_on_email(self, auth_service: AuthService) -> None:
        first = auth_service.create_superadmin("a@b.com", "pw-1")
        second = auth_service.create_superadmin("A@B.com  ", "pw-2")
        # Email is normalised (lowercased + stripped) so re-running
        # with the same address returns the same row.
        assert first.id == second.id

    def test_password_is_not_stored_in_plaintext(
        self, auth_service: AuthService
    ) -> None:
        auth_service.create_superadmin("x@y.com", "supersecret")
        from sqlalchemy import select

        from packages.infrastructure.db.models.auth import SuperadminUser

        row = auth_service._session.execute(
            select(SuperadminUser).where(SuperadminUser.email == "x@y.com")
        ).scalar_one()
        assert "supersecret" not in row.password_hash


class TestAuthenticate:
    def test_happy_path_returns_a_token(
        self, auth_service: AuthService
    ) -> None:
        auth_service.create_superadmin("admin@barber.io", "topsecret")
        issued = auth_service.authenticate("admin@barber.io", "topsecret", label="cli")
        assert issued.raw
        assert len(issued.hash) == 64

    def test_wrong_password_raises(self, auth_service: AuthService) -> None:
        auth_service.create_superadmin("admin@barber.io", "topsecret")
        with pytest.raises(AuthError):
            auth_service.authenticate("admin@barber.io", "WRONG")

    def test_unknown_email_raises(self, auth_service: AuthService) -> None:
        with pytest.raises(AuthError):
            auth_service.authenticate("nobody@barber.io", "x")

    def test_deactivated_user_raises(self, auth_service: AuthService) -> None:
        auth_service.create_superadmin(
            "admin@barber.io", "topsecret", activate=False
        )
        with pytest.raises(AuthError):
            auth_service.authenticate("admin@barber.io", "topsecret")

    def test_unknown_email_and_wrong_password_take_similar_paths(
        self, auth_service: AuthService
    ) -> None:
        """We never want to leak which case we hit. The AuthError
        message must be the same string for both failures."""
        auth_service.create_superadmin("admin@barber.io", "topsecret")
        with pytest.raises(AuthError) as e1:
            auth_service.authenticate("admin@barber.io", "WRONG")
        with pytest.raises(AuthError) as e2:
            auth_service.authenticate("nobody@barber.io", "WRONG")
        assert str(e1.value) == str(e2.value)


class TestVerifyBearer:
    def test_resolves_fresh_token(self, auth_service: AuthService) -> None:
        auth_service.create_superadmin("admin@barber.io", "topsecret")
        issued = auth_service.authenticate("admin@barber.io", "topsecret")
        principal = auth_service.verify_bearer(issued.raw)
        assert principal.email == "admin@barber.io"
        assert principal.scope == "superadmin"

    def test_rejects_missing_token(self, auth_service: AuthService) -> None:
        with pytest.raises(AuthError):
            auth_service.verify_bearer("")

    def test_rejects_unknown_token(self, auth_service: AuthService) -> None:
        with pytest.raises(AuthError):
            auth_service.verify_bearer("not-a-real-token")

    def test_rejects_revoked_token(self, auth_service: AuthService) -> None:
        auth_service.create_superadmin("admin@barber.io", "topsecret")
        issued = auth_service.authenticate("admin@barber.io", "topsecret")
        assert auth_service.revoke(issued.raw) is True
        with pytest.raises(AuthError):
            auth_service.verify_bearer(issued.raw)

    def test_revoke_is_idempotent(self, auth_service: AuthService) -> None:
        auth_service.create_superadmin("admin@barber.io", "topsecret")
        issued = auth_service.authenticate("admin@barber.io", "topsecret")
        assert auth_service.revoke(issued.raw) is True
        assert auth_service.revoke(issued.raw) is False
