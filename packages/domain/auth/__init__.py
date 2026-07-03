"""Auth primitives: password hashing and token handling.

Two small, dependency-free pieces:

- `PasswordHasher`  — PBKDF2-HMAC-SHA256 with a per-row random salt and
  200_000 iterations (high enough to slow bulk attacks; tuned to be
  cheap enough for a single login). The hash is self-describing
  (`pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>`) so we can rotate the
  parameters in a later release without a migration.

- `TokenIssuer`  — generates 32-byte URL-safe random tokens, returns
  the raw token (to send to the caller) and the SHA-256 hex digest (to
  store in the database). We never store the raw token.

These helpers are deliberately tiny so the application service can
compose them with the database. The auth surface in
`apps/api/src/routes/superadmin.py` and the dependency in
`apps/api/src/deps.py` consume them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass


# --- Password hashing ------------------------------------------------------

# 200k iterations of PBKDF2-HMAC-SHA256 takes ~150-200ms on a modern
# CPU. Slow enough to make brute-force expensive, fast enough that a
# single login is not painful. The number is part of the encoded hash
# string, so changing it does not invalidate existing rows.
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_ALG = "sha256"
_SALT_BYTES = 16
_HASH_BYTES = 32
_HASH_PREFIX = f"pbkdf2_{_PBKDF2_ALG}"


class PasswordHasher:
    """Hash + verify passwords using PBKDF2-HMAC-SHA256.

    The encoded hash is `pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>`.
    Verification is constant-time and accepts any iteration count from
    the encoded string (so we can rotate the parameter without a
    migration).
    """

    def hash(self, plaintext: str) -> str:
        """Return a self-describing hash for `plaintext`."""
        if not plaintext:
            raise ValueError("password must not be empty")
        salt = secrets.token_bytes(_SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            _PBKDF2_ALG, plaintext.encode("utf-8"), salt, _PBKDF2_ITERATIONS
        )
        return (
            f"{_HASH_PREFIX}${_PBKDF2_ITERATIONS}"
            f"${base64.b64encode(salt).decode('ascii')}"
            f"${base64.b64encode(digest).decode('ascii')}"
        )

    def verify(self, plaintext: str, encoded: str) -> bool:
        """Return True when `plaintext` matches the previously-stored
        hash `encoded`. The comparison is constant-time."""
        if not plaintext or not encoded:
            return False
        try:
            algo, iters_str, salt_b64, hash_b64 = encoded.split("$", 3)
        except ValueError:
            return False
        if algo != _HASH_PREFIX:
            return False
        try:
            iters = int(iters_str)
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(hash_b64.encode("ascii"))
        except (ValueError, Exception):  # base64 / int parse errors
            return False
        actual = hashlib.pbkdf2_hmac(
            _PBKDF2_ALG, plaintext.encode("utf-8"), salt, iters
        )
        return hmac.compare_digest(actual, expected)


# --- Token issuance --------------------------------------------------------


@dataclass(frozen=True)
class IssuedToken:
    """A fresh bearer token.

    `raw`     — the secret to send back to the caller (one-time).
    `hash`    — SHA-256 hex digest of `raw`; the database stores this.
    `prefix`  — first 8 chars of the raw token, useful for "show last
                used token" UIs later. Not used for verification.
    """

    raw: str
    hash: str
    prefix: str


class TokenIssuer:
    """Generate and verify opaque random tokens."""

    # 32 bytes → 43 chars of URL-safe base64, well under the 128-char
    # `api_tokens.token_hash` column. (We store the SHA-256 hex, not the
    # raw token, so the column only needs to hold 64 hex chars.)
    _RAW_BYTES = 32
    _PREFIX_LEN = 8

    def issue(self) -> IssuedToken:
        """Mint a new token. Caller stores `hash`; sends `raw` to the user."""
        raw = secrets.token_urlsafe(self._RAW_BYTES)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return IssuedToken(raw=raw, hash=digest, prefix=raw[: self._PREFIX_LEN])

    def hash_raw(self, raw: str) -> str:
        """Return the SHA-256 hex digest of `raw` for lookup."""
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
