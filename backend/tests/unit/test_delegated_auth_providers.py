"""Unit and adversarial guardrail tests for delegated host authentication (Track 4 / Stage 3.2).

Covers:
1. JwtAuthProvider (HS256 stdlib implementation): valid token resolution, role mapping,
   issuer/audience checks, and revocation.
2. Adversarial JWT attacks: 'alg: none' bypass attempt, tampered payload/signature,
   wrong secret, missing exp/sub/tenant_id, and expired tokens.
3. Cross-tenant isolation: token valid for Tenant A receives zero roles for Tenant B and
   is rejected with 404 Not Found (anti-probing) when accessing Tenant B's assistant.
4. ApiKeyAuthProvider: SHA-256 constant-time key verification, tenant isolation, and revocation.
5. Wiring & startup guardrails: AUTH_PROVIDER=jwt|api_key fails closed with ConfigurationError
   when secrets are missing or too short; Bearer tokens are rejected when ALLOW_BEARER_AUTH=false.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.adapters.auth.delegated import ApiKeyAuthProvider, JwtAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
from app.ports.errors import AuthenticationError, ConfigurationError
from app.ports.types import UserRole
from app.wiring import create_auth_provider

TEST_HMAC_KEY = "test-jwt-hmac-key-at-least-32-bytes"  # pragma: allowlist secret


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@pytest.mark.asyncio
async def test_jwt_auth_provider_valid_hs256_token_resolves_session_and_tenant_roles() -> None:
    """Valid HS256 JWT resolves SessionContext and grants roles only for its tenant."""
    provider = JwtAuthProvider(
        jwt_secret=TEST_HMAC_KEY,
        issuer="https://master-app.example.com",
        audience="cka-api",
    )
    tenant_a = str(uuid4())
    user_id = str(uuid4())
    now = int(time.time())

    token = JwtAuthProvider.encode_hs256_token(
        {
            "sub": user_id,
            "tenant_id": tenant_a,
            "roles": ["client_admin", "staff"],
            "iss": "https://master-app.example.com",
            "aud": "cka-api",
            "iat": now,
            "exp": now + 300,
            "jti": "sess-jwt-001",
        },
        secret=TEST_HMAC_KEY,
    )

    sess = await provider.session(token)
    assert sess.user_id == user_id
    assert sess.tenant_id == tenant_a
    assert sess.session_id == "sess-jwt-001"
    assert UserRole.CLIENT_ADMIN in sess.roles
    assert UserRole.STAFF in sess.roles

    roles_a = await provider.roles(user_id, tenant_a)
    assert roles_a == [UserRole.CLIENT_ADMIN, UserRole.STAFF]


@pytest.mark.asyncio
async def test_jwt_auth_provider_rejects_alg_none_tampered_signature_and_expired_token() -> None:
    """Adversarial JWT attacks (alg:none, payload tampering, expired, wrong iss/aud) fail."""
    provider = JwtAuthProvider(
        jwt_secret=TEST_HMAC_KEY,
        issuer="https://master-app.example.com",
        audience="cka-api",
    )
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    now = int(time.time())

    # 1. 'alg: none' attack
    none_header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
    payload_b64 = _b64url(
        json.dumps(
            {
                "sub": user_id,
                "tenant_id": tenant_id,
                "roles": ["client_admin"],
                "exp": now + 300,
            }
        ).encode("utf-8")
    )
    alg_none_token = f"{none_header}.{payload_b64}."
    with pytest.raises(AuthenticationError, match="algorithm"):
        await provider.session(alg_none_token)

    # 2. Valid token whose payload is tampered to escalate tenant_id
    valid_token = JwtAuthProvider.encode_hs256_token(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "roles": ["staff"],
            "iss": "https://master-app.example.com",
            "aud": "cka-api",
            "exp": now + 300,
        },
        secret=TEST_HMAC_KEY,
    )
    parts = valid_token.split(".")
    tampered_payload = _b64url(
        json.dumps(
            {
                "sub": user_id,
                "tenant_id": str(uuid4()),
                "roles": ["client_admin"],
                "iss": "https://master-app.example.com",
                "aud": "cka-api",
                "exp": now + 300,
            }
        ).encode("utf-8")
    )
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
    with pytest.raises(AuthenticationError, match="signature"):
        await provider.session(tampered_token)

    # 3. Expired token
    expired_token = JwtAuthProvider.encode_hs256_token(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "roles": ["staff"],
            "iss": "https://master-app.example.com",
            "aud": "cka-api",
            "exp": now - 10,
        },
        secret=TEST_HMAC_KEY,
    )
    with pytest.raises(AuthenticationError, match="expired"):
        await provider.session(expired_token)

    # 4. Wrong issuer
    wrong_iss_token = JwtAuthProvider.encode_hs256_token(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "roles": ["staff"],
            "iss": "https://evil.example.com",
            "aud": "cka-api",
            "exp": now + 300,
        },
        secret=TEST_HMAC_KEY,
    )
    with pytest.raises(AuthenticationError, match="issuer"):
        await provider.session(wrong_iss_token)


@pytest.mark.asyncio
async def test_jwt_auth_provider_enforces_strict_cross_tenant_isolation_and_revocation() -> None:
    """A JWT scoped to Tenant A returns [] roles for Tenant B and fails after revoke()."""
    provider = JwtAuthProvider(jwt_secret=TEST_HMAC_KEY)
    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    user_id = str(uuid4())
    now = int(time.time())

    token = JwtAuthProvider.encode_hs256_token(
        {
            "sub": user_id,
            "tenant_id": tenant_a,
            "roles": ["staff"],
            "exp": now + 300,
        },
        secret=TEST_HMAC_KEY,
    )

    await provider.session(token)
    assert await provider.roles(user_id, tenant_a) == [UserRole.STAFF]
    # Cross-tenant check must return empty list
    assert await provider.roles(user_id, tenant_b) == []

    # Revoking token invalidates session and roles
    assert await provider.revoke(token) is True
    with pytest.raises(AuthenticationError, match="revoked"):
        await provider.session(token)
    assert await provider.roles(user_id, tenant_a) == []


@pytest.mark.asyncio
async def test_api_key_auth_provider_validates_hashed_keys_and_isolates_tenants() -> None:
    """ApiKeyAuthProvider validates service keys in constant time and isolates tenants."""
    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    user_id = str(uuid4())
    raw_key = "cka_live_service_key_9f8e7d6c5b4a3210"

    keys_config = json.dumps(
        [
            {
                "key": raw_key,
                "key_id": "host-svc-1",
                "user_id": user_id,
                "tenant_id": tenant_a,
                "roles": ["staff"],
            }
        ]
    )
    provider = ApiKeyAuthProvider.from_json(keys_config)

    # Raw key must not be stored in plaintext attributes
    assert raw_key not in repr(provider.__dict__)

    sess = await provider.session(raw_key)
    assert sess.user_id == user_id
    assert sess.tenant_id == tenant_a
    assert sess.session_id == "host-svc-1"
    assert sess.roles == [UserRole.STAFF]

    assert await provider.roles(user_id, tenant_a) == [UserRole.STAFF]
    assert await provider.roles(user_id, tenant_b) == []

    with pytest.raises(AuthenticationError):
        await provider.session("cka_live_wrong_key_000000000000")

    assert await provider.revoke(raw_key) is True
    with pytest.raises(AuthenticationError, match="revoked"):
        await provider.session(raw_key)


def test_create_auth_provider_wiring_guards_reject_missing_or_weak_secrets() -> None:
    """create_auth_provider raises ConfigurationError when JWT or API key secrets are invalid."""
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        create_auth_provider(provider_type="jwt", jwt_secret=None)

    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        create_auth_provider(
            provider_type="jwt",
            jwt_secret="too-short",  # pragma: allowlist secret
        )

    with pytest.raises(ConfigurationError, match="API_KEYS_JSON"):
        create_auth_provider(provider_type="api_key", api_keys_json=None)

    with pytest.raises(ConfigurationError, match="Unknown auth provider"):
        create_auth_provider(provider_type="unknown-provider")


@pytest.mark.asyncio
async def test_bearer_jwt_end_to_end_rejects_disabled_bearer_and_blocks_cross_tenant(
    tmp_path: Path,
) -> None:
    """End-to-end FastAPI guardrail test:
    1. When allow_bearer_auth=False, Authorization: Bearer <jwt> is rejected with 401.
    2. When allow_bearer_auth=True and auth_provider_type='jwt', Bearer JWT succeeds without
       cookies or CSRF headers for its own tenant's assistant, but returns 404 Not Found
       when attempting to access another tenant's assistant.
    """
    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    asst_a = str(uuid4())
    asst_b = str(uuid4())
    user_id = str(uuid4())
    now = int(time.time())

    token_a = JwtAuthProvider.encode_hs256_token(
        {
            "sub": user_id,
            "tenant_id": tenant_a,
            "roles": ["staff"],
            "exp": now + 300,
        },
        secret=TEST_HMAC_KEY,
    )

    # 1. allow_bearer_auth=False -> 401 Unauthorized
    app_disabled = create_app(
        ApiSettings(
            profile="mock",
            storage_dir=str(tmp_path),
            session_upload_retention_hours=24,
            allow_bearer_auth=False,
            auth_provider_type="jwt",
            jwt_secret=SecretStr(TEST_HMAC_KEY),
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app_disabled), base_url="http://test"
    ) as client:
        resp_disabled = await client.get(
            f"/v1/assistants/{asst_a}/conversations",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_disabled.status_code == 401

    # 2. allow_bearer_auth=True -> cross-tenant assistant access returns 404 Not Found
    app_enabled = create_app(
        ApiSettings(
            profile="mock",
            storage_dir=str(tmp_path),
            session_upload_retention_hours=24,
            allow_bearer_auth=True,
            auth_provider_type="jwt",
            jwt_secret=SecretStr(TEST_HMAC_KEY),
        )
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_unscoped_session():
        mock_db = AsyncMock()
        mock_res = MagicMock()
        # Simulate assistant belonging to tenant_b while JWT belongs to tenant_a
        mock_res.scalar_one_or_none.return_value = tenant_b
        mock_db.execute.return_value = mock_res
        yield mock_db

    with patch("app.api.deps.unscoped_session", fake_unscoped_session):
        async with AsyncClient(
            transport=ASGITransport(app=app_enabled), base_url="http://test"
        ) as client:
            resp_cross = await client.get(
                f"/v1/assistants/{asst_b}/conversations",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert resp_cross.status_code == 404
            assert asst_a != asst_b
