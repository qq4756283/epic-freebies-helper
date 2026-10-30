# -*- coding: utf-8 -*-
"""Regression tests for the session-vault seeding chain.

The 2026-10 outage had a single mechanical cause: the exchange-code retry loop passed
``json=`` to Playwright's ``APIRequestContext.post()``, which has no such keyword, so every
POST attempt raised ``TypeError`` and was swallowed before it left the process. These tests
pin the request assembly and the bearer-cookie seeding so that regression cannot return
silently.
"""

import asyncio
import json

import services.session_vault as vault


def test_exchange_attempts_never_pass_json_keyword():
    attempts = vault._build_exchange_attempts("abc", xsrf="token-123")

    assert attempts
    for method, _url, kwargs in attempts:
        assert "json" not in kwargs, "APIRequestContext has no json= keyword"
        if method == "post" and "data" in kwargs:
            payload = json.loads(kwargs["data"])
            assert "code" in payload or "exchangeCode" in payload
            assert kwargs["headers"]["Content-Type"] == "application/json"


def test_exchange_attempts_include_csrf_headers_only_when_xsrf_present():
    with_xsrf = vault._build_exchange_attempts("abc", xsrf="token-123")
    for _method, _url, kwargs in with_xsrf:
        headers = kwargs.get("headers") or {}
        if "Content-Type" in headers:
            assert headers["x-xsrf-token"] == "token-123"
            assert headers["x-csrf-token"] == "token-123"

    without_xsrf = vault._build_exchange_attempts("abc", xsrf="")
    for _method, _url, kwargs in without_xsrf:
        headers = kwargs.get("headers") or {}
        assert "x-xsrf-token" not in headers
        assert "x-csrf-token" not in headers


def test_exchange_attempts_cover_every_endpoint_and_shape():
    attempts = vault._build_exchange_attempts("abc", xsrf="")

    urls = {url for _method, url, _kwargs in attempts}
    assert urls == set(vault.WEB_EXCHANGE_ENDPOINTS)

    # Per endpoint: two JSON POSTs, one GET and one form POST.
    assert len(attempts) == len(vault.WEB_EXCHANGE_ENDPOINTS) * 4
    assert {method for method, _url, _kwargs in attempts} == {"post", "get"}


def test_bearer_cookies_cover_all_store_domains():
    cookies = vault._bearer_cookies("tok", 1234567890)

    assert len(cookies) == len(vault.BEARER_COOKIE_DOMAINS)
    assert {cookie["domain"] for cookie in cookies} == set(vault.BEARER_COOKIE_DOMAINS)
    assert ".epicgames.com" in {cookie["domain"] for cookie in cookies}
    for cookie in cookies:
        assert cookie["name"] == vault.BEARER_COOKIE_NAME
        assert cookie["value"] == "tok"
        assert cookie["path"] == "/"
        assert cookie["secure"] is True
        assert cookie["httpOnly"] is True
        assert cookie["sameSite"] == "Lax"
        assert cookie["expires"] == 1234567890.0


def test_seed_bearer_session_returns_false_without_token(monkeypatch):
    async def _no_token():
        return None

    monkeypatch.setattr(vault, "access_token", _no_token)
    assert asyncio.run(vault.seed_bearer_session(object())) is False


def test_seed_bearer_session_returns_false_when_cookie_injection_fails(monkeypatch):
    async def _token():
        return "access-token"

    class _BrokenContext:
        async def add_cookies(self, _cookies):
            raise RuntimeError("cookie jar closed")

    monkeypatch.setattr(vault, "access_token", _token)
    monkeypatch.setattr(vault, "read_state", lambda: {"expires_at": 1234567890})
    assert asyncio.run(vault.seed_bearer_session(_BrokenContext())) is False


def test_store_session_present_detects_eg1_cookie():
    class _Context:
        async def cookies(self):
            return [
                {"name": "EPIC_BEARER_TOKEN", "domain": ".epicgames.com"},
                {"name": "EPIC_EG1", "domain": ".store.epicgames.com"},
            ]

    assert asyncio.run(vault.store_session_present(_Context())) is True


def test_store_session_present_rejects_foreign_domains_and_broken_contexts():
    class _ForeignDomain:
        async def cookies(self):
            return [{"name": "EPIC_EG1", "domain": ".example.com"}]

    class _Broken:
        async def cookies(self):
            raise RuntimeError("context closed")

    assert asyncio.run(vault.store_session_present(_ForeignDomain())) is False
    assert asyncio.run(vault.store_session_present(_Broken())) is False
