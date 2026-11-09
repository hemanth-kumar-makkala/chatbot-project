"""Unit and security tests for FastAPI API configuration, headers, cookies, and CSRF."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.config import ApiSettings
from app.ports.errors import ConfigurationError


@pytest.fixture
def test_settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        cors_origins=["https://client-portal.example.com"],
        cookie_secure=True,
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        session_upload_retention_hours=24,
        allow_bearer_auth=False,  # strictly cookie-only in standard configuration
        profile="mock",
        storage_dir=str(tmp_path),
    )


@pytest.fixture
def app(test_settings: ApiSettings):
    return create_app(settings=test_settings)


@pytest.mark.asyncio
class TestApiAuthAndSecurityUnit:
    """Verifies security headers, CORS origin restriction, cookie flags, and CSRF protection."""

    async def test_public_health_returns_only_status_ok(self, app) -> None:
        """Public /health returns only ok/not-ok; profile and database details are excluded."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data == {"status": "ok"}
            assert "profile" not in data
            assert "database" not in data
            assert "engine" not in data

    async def test_admin_health_requires_platform_admin(self, app) -> None:
        """Admin health endpoint is inaccessible without platform admin authentication."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/admin/health")
            # Unauthenticated or non-admin rejected
            assert resp.status_code in (401, 403)

    async def test_cors_rejects_unconfigured_origins(self, app) -> None:
        """CORS middleware allows only configured web origins, never '*'."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.options(
                "/health",
                headers={
                    "Origin": "https://malicious-site.example.org",
                    "Access-Control-Request-Method": "GET",
                },
            )
            allow_origin = resp.headers.get("access-control-allow-origin")
            assert allow_origin != "*"
            assert allow_origin != "https://malicious-site.example.org"

    async def test_cors_allows_configured_origin(self, app) -> None:
        """CORS allows explicitly configured web origin."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.options(
                "/health",
                headers={
                    "Origin": "https://client-portal.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert (
                resp.headers.get("access-control-allow-origin")
                == "https://client-portal.example.com"
            )

    async def test_csrf_token_required_for_cookie_authenticated_state_changing_requests(
        self, app
    ) -> None:
        """State-changing requests using cookie authentication must provide
        matching X-CSRF-Token.
        """
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Setting session cookie without CSRF header
            client.cookies.set("session_token", "fake-session-token")
            client.cookies.set("csrf_token", "expected-csrf-value")
            resp = await client.post(
                "/v1/auth/logout",
                headers={},  # Missing X-CSRF-Token header
            )
            assert resp.status_code == 403
            assert "CSRF" in resp.text or "csrf" in resp.text.lower()

    async def test_csrf_token_passes_when_header_matches_cookie(self, app) -> None:
        """State-changing requests succeed when X-CSRF-Token header matches csrf_token cookie."""
        from unittest.mock import AsyncMock, MagicMock

        from app.api.deps import get_auth_provider

        mock_auth = MagicMock()
        mock_auth.revoke = AsyncMock(return_value=False)
        app.dependency_overrides[get_auth_provider] = lambda: mock_auth
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                client.cookies.set("session_token", "fake-session-token")
                client.cookies.set("csrf_token", "valid-csrf-token-12345")
                resp = await client.post(
                    "/v1/auth/logout",
                    headers={"X-CSRF-Token": "valid-csrf-token-12345"},
                )
                # Passes CSRF check (status != 403 CSRF)
                assert resp.status_code != 403
        finally:
            app.dependency_overrides.pop(get_auth_provider, None)

    async def test_db_engines_configure_connect_timeout_of_3_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_app_engine and get_retriever_engine pass connect_timeout=3 so tests never hang."""
        from unittest.mock import MagicMock

        import app.db.session as session_mod

        session_mod.reset_session_engines()
        captured_calls: list[dict[str, object]] = []

        def fake_create_async_engine(url: str, **kwargs: object) -> object:
            captured_calls.append({"url": url, **kwargs})
            return MagicMock()

        monkeypatch.setattr(session_mod, "create_async_engine", fake_create_async_engine)
        monkeypatch.setattr(session_mod, "async_sessionmaker", lambda *_a, **_kw: MagicMock())

        try:
            session_mod.get_app_engine()
            session_mod.get_retriever_engine()
        finally:
            session_mod.reset_session_engines()

        assert len(captured_calls) == 2
        for call in captured_calls:
            assert call.get("connect_args") == {"connect_timeout": 3}

    async def test_create_app_reads_cors_origins_and_allow_bearer_auth_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """create_app(settings=None) reads CORS_ORIGINS and ALLOW_BEARER_AUTH from env vars."""
        monkeypatch.setenv("APP_PROFILE", "mock")
        monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path))
        monkeypatch.setenv("SESSION_UPLOAD_RETENTION_HOURS", "24")
        monkeypatch.setenv("CORS_ORIGINS", "https://portal.example.com, https://admin.example.com")
        monkeypatch.setenv("ALLOW_BEARER_AUTH", "true")

        env_app = create_app()
        assert env_app.state.settings.cors_origins == [
            "https://portal.example.com",
            "https://admin.example.com",
        ]
        assert env_app.state.settings.allow_bearer_auth is True

    async def test_wildcard_cors_and_wildcard_frame_ancestors_are_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Guardrail: wildcard '*' in CORS_ORIGINS or ALLOWED_FRAME_ANCESTORS is blocked."""
        with pytest.raises(ValueError, match="Wildcard CORS origin"):
            create_app(
                ApiSettings(
                    profile="mock",
                    storage_dir=str(tmp_path),
                    session_upload_retention_hours=24,
                    cors_origins=["*"],
                )
            )

        monkeypatch.setenv("ALLOWED_FRAME_ANCESTORS", "*")
        with pytest.raises(ValueError, match="Wildcard frame-ancestors"):
            create_app(
                ApiSettings(
                    profile="mock",
                    storage_dir=str(tmp_path),
                    session_upload_retention_hours=24,
                )
            )

    async def test_security_headers_default_deny_framing_and_allowlist_override(
        self, tmp_path: Path
    ) -> None:
        """Default responses include X-Frame-Options: DENY and CSP frame-ancestors 'none';
        explicit allowed_frame_ancestors emits CSP frame-ancestors allowlist without X-Frame.
        """
        default_app = create_app(
            ApiSettings(
                profile="mock",
                storage_dir=str(tmp_path),
                session_upload_retention_hours=24,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=default_app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
            assert resp.headers.get("x-content-type-options") == "nosniff"
            assert resp.headers.get("x-frame-options") == "DENY"
            assert "frame-ancestors 'none'" in resp.headers.get("content-security-policy", "")

        embed_app = create_app(
            ApiSettings(
                profile="mock",
                storage_dir=str(tmp_path),
                session_upload_retention_hours=24,
                allowed_frame_ancestors=["https://host-app.example.com"],
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=embed_app), base_url="http://test"
        ) as client:
            resp2 = await client.get("/health")
            assert resp2.headers.get("x-content-type-options") == "nosniff"
            assert resp2.headers.get("x-frame-options") is None
            assert "frame-ancestors https://host-app.example.com" in resp2.headers.get(
                "content-security-policy", ""
            )

    async def test_secure_cookie_defaults_to_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Secure cookie defaults to on (True). Only an explicit local-dev setting turns it off."""
        monkeypatch.delenv("COOKIE_SECURE", raising=False)
        settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))
        assert settings.cookie_secure is True

        # Explicit local-dev setting turns it off
        monkeypatch.setenv("COOKIE_SECURE", "false")
        dev_settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))
        assert dev_settings.cookie_secure is False

        # Explicit positive setting turns it on
        monkeypatch.setenv("COOKIE_SECURE", "true")
        prod_settings = ApiSettings(profile="mock", storage_dir=str(tmp_path))
        assert prod_settings.cookie_secure is True

    async def test_building_api_settings_without_profile_fails(self, tmp_path: Path) -> None:
        """Building ApiSettings without a profile fails at startup with ConfigurationError."""
        with pytest.raises(ConfigurationError, match="profile"):
            ApiSettings(storage_dir=str(tmp_path))

    async def test_building_api_settings_without_storage_dir_fails(self) -> None:
        """Building ApiSettings without storage_dir fails at startup with ConfigurationError."""
        with pytest.raises(ConfigurationError, match="storage_dir"):
            ApiSettings(profile="mock")

    async def test_unknown_profile_raises_configuration_error(self, tmp_path: Path) -> None:
        """Unknown profile raises ConfigurationError indicating profile is invalid."""
        with pytest.raises(ConfigurationError) as exc_info:
            ApiSettings(profile="invalid-unknown-profile", storage_dir=str(tmp_path))
        assert "profile" in str(exc_info.value)
        assert "INVALID_CONFIGURATION" in str(exc_info.value)

    async def test_invalid_config_marker_never_in_error_or_traceback(self, tmp_path: Path) -> None:
        """An invalid config marker string never shows in error or traceback."""
        import traceback

        marker = "MARKER_SECRET_SENSITIVE_LEAK_CHECK_98765"
        with pytest.raises(ConfigurationError) as exc_info:
            ApiSettings(profile=marker, storage_dir=str(tmp_path))

        error_message = str(exc_info.value)
        tb_str = "".join(traceback.format_exception(exc_info.value))

        assert marker not in error_message
        assert marker not in tb_str
        assert "INVALID_CONFIGURATION" in error_message
        assert "profile" in error_message

    async def test_client_ip_hash_uses_hmac_with_secret_key(self) -> None:
        """client_ip_hash must be an HMAC with a secret key, not a plain hash."""
        import hashlib
        import hmac
        import secrets
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.deps import get_client_ip_hash

        mock_req = MagicMock(spec=Request)
        mock_req.client.host = "192.168.1.100"

        hmac_key = secrets.token_hex(16)
        ip_hash = get_client_ip_hash(mock_req, key=hmac_key)

        expected_hmac = hmac.new(
            hmac_key.encode("utf-8"), b"192.168.1.100", hashlib.sha256
        ).hexdigest()
        plain_sha256 = hashlib.sha256(b"192.168.1.100").hexdigest()

        assert ip_hash == expected_hmac
        assert ip_hash != plain_sha256
