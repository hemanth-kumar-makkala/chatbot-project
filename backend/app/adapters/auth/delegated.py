"""Delegated host authentication providers: JwtAuthProvider (HS256) and ApiKeyAuthProvider.

Enables a master host application to authenticate requests via Authorization: Bearer <token>
(when ALLOW_BEARER_AUTH=true) while strictly enforcing:
- Constant-time HMAC-SHA256 signature / key digest comparison (hmac.compare_digest).
- Rejection of 'alg: none' or any non-HS256 JWT header.
- Mandatory exp, sub, and tenant_id claims (plus optional iss, aud, and nbf checks).
- Strict tenant isolation: roles(user_id, tenant_id) grants roles ONLY for the verified
  tenant_id in the token/key, returning [] for any other tenant (Invariants 2 & 3).
- Zero new third-party dependencies (uses Python stdlib hmac, hashlib, base64, json, time).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from app.ports.auth import AuthProvider
from app.ports.errors import AuthenticationError, ConfigurationError
from app.ports.types import (
    AuthCredentials,
    AuthToken,
    SessionContext,
    UserRole,
)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:
        raise AuthenticationError("Invalid base64url encoding in token") from exc


def _parse_roles(raw_roles: Any) -> list[UserRole]:
    if not isinstance(raw_roles, list):
        return []
    parsed: list[UserRole] = []
    for item in raw_roles:
        val = str(item).strip().lower()
        if val == "client_admin" and UserRole.CLIENT_ADMIN not in parsed:
            parsed.append(UserRole.CLIENT_ADMIN)
        elif val == "staff" and UserRole.STAFF not in parsed:
            parsed.append(UserRole.STAFF)
    return parsed


class JwtAuthProvider(AuthProvider):
    """Stateless HMAC-SHA256 (HS256) JWT authentication provider for host delegation."""

    def __init__(
        self,
        jwt_secret: str,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> None:
        if not jwt_secret or len(jwt_secret.strip()) < 16:
            raise ConfigurationError(
                "JWT_SECRET is required and must be at least 16 characters when AUTH_PROVIDER=jwt",
                error_code="INVALID_CONFIGURATION",
            )
        self._secret_bytes = jwt_secret.encode("utf-8")
        self._issuer = issuer.strip() if issuer and issuer.strip() else None
        self._audience = audience.strip() if audience and audience.strip() else None
        # Maps (user_id, tenant_id) -> (roles, exp_timestamp, token_hash)
        self._active_grants: dict[tuple[str, str], tuple[list[UserRole], int, str]] = {}
        self._revoked_hashes: set[str] = set()

    @staticmethod
    def encode_hs256_token(payload: dict[str, Any], secret: str) -> str:
        """Encode and sign an HS256 JWT using Python stdlib hmac/hashlib."""
        header = {"alg": "HS256", "typ": "JWT"}
        header_b64 = _b64url_encode(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        payload_b64 = _b64url_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        sig_b64 = _b64url_encode(sig)
        return f"{header_b64}.{payload_b64}.{sig_b64}"

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def authenticate(
        self,
        credentials: AuthCredentials,
        client_ip_hash: str | None = None,
    ) -> AuthToken:
        """Password login is not supported on JwtAuthProvider; tokens are issued by host."""
        _ = (credentials, client_ip_hash)
        raise AuthenticationError("Direct password login is disabled when AUTH_PROVIDER=jwt")

    async def session(self, token: str) -> SessionContext:
        """Verify HS256 signature and claims, returning a tenant-scoped SessionContext."""
        if not token or not isinstance(token, str):
            raise AuthenticationError("Empty or malformed JWT")

        token_hash = self._hash_token(token)
        if token_hash in self._revoked_hashes:
            raise AuthenticationError("Token has been revoked")

        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationError("Malformed JWT structure")

        header_b64, payload_b64, sig_b64 = parts
        try:
            header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        except Exception as exc:
            raise AuthenticationError("Malformed JWT header") from exc

        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise AuthenticationError(
                "Unsupported or insecure JWT algorithm; only HS256 is allowed"
            )

        if not sig_b64:
            raise AuthenticationError("Missing JWT signature")

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = hmac.new(self._secret_bytes, signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            raise AuthenticationError("Invalid JWT signature")

        try:
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        except Exception as exc:
            raise AuthenticationError("Malformed JWT payload") from exc

        if not isinstance(payload, dict):
            raise AuthenticationError("Invalid JWT payload")

        now = int(time.time())
        exp = payload.get("exp")
        if not isinstance(exp, int | float):
            raise AuthenticationError("Missing or invalid 'exp' claim in JWT")
        if now >= int(exp):
            raise AuthenticationError("JWT has expired")

        nbf = payload.get("nbf")
        if isinstance(nbf, int | float) and now < int(nbf):
            raise AuthenticationError("JWT is not yet valid (nbf)")

        if self._issuer is not None and payload.get("iss") != self._issuer:
            raise AuthenticationError("Invalid JWT issuer")

        if self._audience is not None:
            aud = payload.get("aud")
            aud_ok = (isinstance(aud, str) and aud == self._audience) or (
                isinstance(aud, list) and self._audience in aud
            )
            if not aud_ok:
                raise AuthenticationError("Invalid JWT audience")

        sub = str(payload.get("sub") or "").strip()
        tenant_id = str(payload.get("tenant_id") or "").strip()
        if not sub or not tenant_id:
            raise AuthenticationError("JWT must include non-empty 'sub' and 'tenant_id' claims")

        roles = _parse_roles(payload.get("roles", ["staff"]))
        if not roles:
            raise AuthenticationError("JWT contains no valid roles")

        jti = str(payload.get("jti") or token_hash[:16]).strip()
        self._active_grants[(sub, tenant_id)] = (roles, int(exp), token_hash)

        return SessionContext(
            user_id=sub,
            tenant_id=tenant_id,
            roles=roles,
            session_id=jti,
        )

    async def roles(self, user_id: str, tenant_id: str) -> list[UserRole]:
        """Return granted roles strictly for the verified (user_id, tenant_id) grant."""
        grant = self._active_grants.get((user_id, tenant_id))
        if grant is None:
            return []
        roles, exp, token_hash = grant
        if token_hash in self._revoked_hashes or int(time.time()) >= exp:
            self._active_grants.pop((user_id, tenant_id), None)
            return []
        return list(roles)

    async def revoke(self, token: str) -> bool:
        """Revoke a JWT by its SHA-256 digest and purge any cached role grant."""
        if not token:
            return False
        token_hash = self._hash_token(token)
        self._revoked_hashes.add(token_hash)
        to_remove = [
            key for key, (_r, _e, t_hash) in self._active_grants.items() if t_hash == token_hash
        ]
        for key in to_remove:
            self._active_grants.pop(key, None)
        return True


@dataclass(frozen=True)
class _ApiKeyRecord:
    key_sha256: str
    key_id: str
    user_id: str
    tenant_id: str
    roles: list[UserRole]


class ApiKeyAuthProvider(AuthProvider):
    """Service-to-service API key authentication provider storing only SHA-256 key digests."""

    def __init__(self, records: list[_ApiKeyRecord]) -> None:
        if not records:
            raise ConfigurationError(
                "API_KEYS_JSON must configure at least one valid API key record",
                error_code="INVALID_CONFIGURATION",
            )
        self._records = list(records)
        self._revoked_hashes: set[str] = set()

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, raw_json: str) -> ApiKeyAuthProvider:
        """Construct ApiKeyAuthProvider from a JSON array of key definitions."""
        if not raw_json or not raw_json.strip():
            raise ConfigurationError(
                "API_KEYS_JSON is required when AUTH_PROVIDER=api_key",
                error_code="INVALID_CONFIGURATION",
            )
        try:
            items = json.loads(raw_json)
        except Exception as exc:
            raise ConfigurationError(
                "API_KEYS_JSON is not valid JSON",
                error_code="INVALID_CONFIGURATION",
            ) from exc

        if not isinstance(items, list) or not items:
            raise ConfigurationError(
                "API_KEYS_JSON must be a non-empty JSON list",
                error_code="INVALID_CONFIGURATION",
            )

        records: list[_ApiKeyRecord] = []
        for idx, entry in enumerate(items):
            if not isinstance(entry, dict):
                raise ConfigurationError(
                    f"API_KEYS_JSON entry {idx} must be an object",
                    error_code="INVALID_CONFIGURATION",
                )
            raw_key = str(entry.get("key") or "").strip()
            key_sha256 = str(entry.get("key_sha256") or "").strip().lower()
            if raw_key:
                key_sha256 = cls._hash_key(raw_key)
            if not key_sha256 or len(key_sha256) != 64:
                raise ConfigurationError(
                    f"API_KEYS_JSON entry {idx} requires 'key' or 64-char 'key_sha256'",
                    error_code="INVALID_CONFIGURATION",
                )
            user_id = str(entry.get("user_id") or "").strip()
            tenant_id = str(entry.get("tenant_id") or "").strip()
            if not user_id or not tenant_id:
                raise ConfigurationError(
                    f"API_KEYS_JSON entry {idx} requires non-empty 'user_id' and 'tenant_id'",
                    error_code="INVALID_CONFIGURATION",
                )
            roles = _parse_roles(entry.get("roles", ["staff"]))
            if not roles:
                raise ConfigurationError(
                    f"API_KEYS_JSON entry {idx} requires at least one valid role",
                    error_code="INVALID_CONFIGURATION",
                )
            key_id = str(entry.get("key_id") or f"apikey-{idx + 1}").strip()
            records.append(
                _ApiKeyRecord(
                    key_sha256=key_sha256,
                    key_id=key_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    roles=roles,
                )
            )
        return cls(records)

    async def authenticate(
        self,
        credentials: AuthCredentials,
        client_ip_hash: str | None = None,
    ) -> AuthToken:
        _ = (credentials, client_ip_hash)
        raise AuthenticationError("Direct password login is disabled when AUTH_PROVIDER=api_key")

    async def session(self, token: str) -> SessionContext:
        """Validate API key in constant time against configured key SHA-256 digests."""
        if not token or not isinstance(token, str):
            raise AuthenticationError("Invalid API key")

        candidate_hash = self._hash_key(token.strip())
        if candidate_hash in self._revoked_hashes:
            raise AuthenticationError("API key has been revoked")

        matched: _ApiKeyRecord | None = None
        for rec in self._records:
            if hmac.compare_digest(candidate_hash, rec.key_sha256):
                matched = rec

        if matched is None:
            raise AuthenticationError("Invalid API key")

        return SessionContext(
            user_id=matched.user_id,
            tenant_id=matched.tenant_id,
            roles=list(matched.roles),
            session_id=matched.key_id,
        )

    async def roles(self, user_id: str, tenant_id: str) -> list[UserRole]:
        """Return roles strictly for the tenant and user associated with an active API key."""
        for rec in self._records:
            if (
                rec.user_id == user_id
                and rec.tenant_id == tenant_id
                and rec.key_sha256 not in self._revoked_hashes
            ):
                return list(rec.roles)
        return []

    async def revoke(self, token: str) -> bool:
        if not token:
            return False
        candidate_hash = self._hash_key(token.strip())
        for rec in self._records:
            if hmac.compare_digest(candidate_hash, rec.key_sha256):
                self._revoked_hashes.add(candidate_hash)
                return True
        return False
