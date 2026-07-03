"""Unit tests for the auth primitives: PasswordHasher + TokenIssuer.

The integration of these helpers with the database is exercised in
`tests/application/test_auth_service.py`. This file pins the
behaviour of the two helpers in isolation so a regression in the
crypto pieces is caught without needing a database.
"""

from __future__ import annotations

import pytest

from packages.domain.auth import PasswordHasher, TokenIssuer


class TestPasswordHasher:
    def test_hash_then_verify_roundtrip(self) -> None:
        h = PasswordHasher()
        encoded = h.hash("correct horse battery staple")
        assert h.verify("correct horse battery staple", encoded) is True

    def test_verify_rejects_wrong_password(self) -> None:
        h = PasswordHasher()
        encoded = h.hash("hello-world")
        assert h.verify("hello-WORLD", encoded) is False
        assert h.verify("", encoded) is False
        assert h.verify("hello", encoded) is False

    def test_hash_rejects_empty_password(self) -> None:
        h = PasswordHasher()
        with pytest.raises(ValueError):
            h.hash("")

    def test_two_hashes_of_the_same_password_differ(self) -> None:
        """The salt is random; two hashes of the same plaintext must
        not be equal."""
        h = PasswordHasher()
        a = h.hash("same")
        b = h.hash("same")
        assert a != b
        assert h.verify("same", a) is True
        assert h.verify("same", b) is True

    def test_verify_handles_malformed_encoded(self) -> None:
        h = PasswordHasher()
        assert h.verify("anything", "not-an-encoded-hash") is False
        assert h.verify("anything", "pbkdf2_sha256$100000$only-three-fields") is False
        assert h.verify("anything", "") is False

    def test_encoded_hash_carries_algorithm_and_iterations(self) -> None:
        h = PasswordHasher()
        encoded = h.hash("pw")
        # `pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>`
        parts = encoded.split("$")
        assert len(parts) == 4
        assert parts[0] == "pbkdf2_sha256"
        assert int(parts[1]) > 0
        assert len(parts[2]) > 0
        assert len(parts[3]) > 0


class TestTokenIssuer:
    def test_issue_returns_raw_and_hash(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue()
        # Raw is URL-safe base64; 32 bytes → ~43 chars.
        assert len(token.raw) >= 40
        # Hash is SHA-256 hex (64 chars).
        assert len(token.hash) == 64
        assert all(c in "0123456789abcdef" for c in token.hash)
        # Prefix is the first 8 chars of raw.
        assert token.prefix == token.raw[:8]

    def test_hash_raw_is_idempotent(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue()
        assert issuer.hash_raw(token.raw) == token.hash

    def test_two_issues_differ(self) -> None:
        issuer = TokenIssuer()
        a = issuer.issue()
        b = issuer.issue()
        assert a.raw != b.raw
        assert a.hash != b.hash
