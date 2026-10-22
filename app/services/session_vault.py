# -*- coding: utf-8 -*-
"""Cloud-only Epic session vault.

Why this exists
---------------
The only place the login hCaptcha appears is the Epic login page. Every historical
failure in this repository is a login failure, and every success came from a run
that never had to log in. The vault turns "hope the browser profile is still valid"
into "rebuild a valid session over plain HTTP in about two seconds".

Two directions
--------------
* ``ensure_state()`` mints a launcher ``refresh_token`` from an already logged-in
  browser context (authorization-code grant). One successful browser login is then
  enough forever, because later runs refresh the token over HTTP.
* ``try_seed()`` turns a stored token back into browser cookies, so the store opens
  already logged in and the login page (and its captcha) never renders.

Failure policy
--------------
Every public helper is best-effort and returns ``False`` instead of raising. When the
vault cannot help, the caller falls back to the password login path, so enabling this
module can never make a run worse than the current behaviour.

Secrets are never logged. Only lengths, prefixes and expiry timestamps are emitted.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

# --- Epic public clients -------------------------------------------------------
# The launcher client is the one that can hold a long-lived refresh token without a
# device-code browser round trip. Overridable for forks that want their own client.
LAUNCHER_CLIENT_ID = os.getenv("EPIC_LAUNCHER_CLIENT_ID", "34a02cf8f4414e29b15921876da36f9a")
LAUNCHER_CLIENT_SECRET = os.getenv(
    "EPIC_LAUNCHER_CLIENT_SECRET", "daafbccc737745039dffe53d94fc76cf"
)

OAUTH_TOKEN_URL = os.getenv(
    "EPIC_OAUTH_TOKEN_URL",
    "https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token",
)
# The web client that owns store.epicgames.com / www.epicgames.com sessions. Codes issued
# for this client are the ones ``id/api/exchange`` accepts, which is why seeding uses it
# while minting has to keep the launcher client (that pair is what grants a refresh token).
WEB_CLIENT_ID = os.getenv("EPIC_WEB_CLIENT_ID", "007c0bfe154c4f5396648f013c641dcf")
# The store domain (store.epicgames.com) runs its own OAuth client; its silent-login URL is
# /auth/login?returnPath=...&clientId=<this>&prompt=none. Minting codes for the web client
# does not create a store session, which is why clicking Get bounced to switch-account.
STORE_CLIENT_ID = os.getenv("EPIC_STORE_CLIENT_ID", "875a3b57d3a640a6b7f9b4e883463ab4")
WEB_REDIRECT_URL = "https://www.epicgames.com/id/api/redirect"
WEB_EXCHANGE_URL = "https://www.epicgames.com/id/api/exchange"
ORDER_HISTORY_URL = "https://www.epicgames.com/account/v2/payment/ajaxGetOrderHistory"

STATE_PATH = (
    Path(__file__).resolve().parents[1].joinpath("volumes", "runtime", "session-state.json")
)
STATE_VERSION = 1
# Refresh a little before the server-side expiry so a token never dies mid-run.
REFRESH_SKEW_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 25.0


def enabled() -> bool:
    """Whether the vault is allowed to touch the session at all."""
    return (os.getenv("SESSION_STATE_ENABLED", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _mask(value: str | None) -> str:
    if not value:
        return "<empty>"
    return f"len={len(value)} prefix={value[:4]}…"


def _basic_auth() -> httpx.BasicAuth:
    return httpx.BasicAuth(LAUNCHER_CLIENT_ID, LAUNCHER_CLIENT_SECRET)


def _proxy_url() -> str | None:
    """Explicit proxy only.

    ``trust_env`` is disabled on purpose: a stale or duplicated ``HTTP_PROXY`` in the
    environment surfaces as ``InvalidURL("Invalid port")`` and would look like an Epic
    outage. Operators who need a proxy set ``EPIC_HTTP_PROXY``.
    """
    value = (os.getenv("EPIC_HTTP_PROXY") or "").strip()
    return value or None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        trust_env=False,
        proxy=_proxy_url(),
    )


# --- state file ----------------------------------------------------------------
def read_state() -> dict[str, Any]:
    """Return the persisted session state, or an empty dict when unavailable."""
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Session vault: state file is not valid JSON, ignoring it")
        return {}
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return {}
    return data


def write_state(state: dict[str, Any]) -> None:
    """Persist the session state; never raises."""
    payload = dict(state)
    payload["version"] = STATE_VERSION
    payload["updated_at"] = int(time.time())
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError as err:
        logger.warning("Session vault: could not persist state | error={!r}", err)


def clear_state(reason: str) -> None:
    """Drop the stored token; used when Epic rejects it."""
    if STATE_PATH.exists():
        try:
            STATE_PATH.unlink()
        except OSError as err:
            logger.warning("Session vault: could not remove state | error={!r}", err)
    logger.warning("Session vault: state cleared | reason={}", reason)


def _store_token_payload(payload: dict[str, Any], source: str) -> bool:
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        logger.warning("Session vault: token response without access_token | source={}", source)
        return False

    expires_in = payload.get("expires_in") or 0
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError):
        expires_in = 0

    previous = read_state()
    refresh_token = payload.get("refresh_token") or previous.get("refresh_token")
    state = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + expires_in if expires_in else 0,
        "account_id": payload.get("account_id") or previous.get("account_id") or "",
        "display_name": payload.get("displayName") or previous.get("display_name") or "",
        "source": source,
    }
    write_state(state)
    logger.success(
        "Session vault: token stored | source={} | access={} | refresh={} | expires_in={}s",
        source,
        _mask(access_token),
        _mask(refresh_token),
        expires_in,
    )
    return True


# --- token refresh (no browser) -------------------------------------------------
async def _refresh_with(token: str) -> tuple[int, dict[str, Any] | None]:
    """POST one refresh grant; returns (http_status, body). Never raises."""
    payload = {"grant_type": "refresh_token", "refresh_token": token, "token_type": "eg1"}
    try:
        async with _client() as client:
            response = await client.post(OAUTH_TOKEN_URL, data=payload, auth=_basic_auth())
    except Exception as err:  # network/proxy/DNS — never fatal for the run
        logger.warning("Session vault: refresh request failed | error={!r}", err)
        return 0, None
    if response.status_code != 200:
        logger.warning(
            "Session vault: refresh rejected | status={} | body={}",
            response.status_code,
            response.text[:200],
        )
        return response.status_code, None
    try:
        body = response.json()
    except ValueError:
        logger.warning("Session vault: refresh response was not JSON")
        return response.status_code, None
    return response.status_code, body if isinstance(body, dict) else None


async def refresh_access_token() -> str | None:
    """Exchange the stored refresh token for a fresh access token.

    Epic rotates refresh tokens: the response carries a new one, and the old value stops
    working, so the rotated value is always persisted. If the cached token is rejected
    while a different ``EPIC_REFRESH_TOKEN`` exists, the cached state is dropped and the
    environment token is tried once - that is what lets the secret act as a real fallback
    after a manual re-bootstrap.
    """
    state = read_state()
    state_token = (state.get("refresh_token") or "").strip()
    env_token = (os.getenv("EPIC_REFRESH_TOKEN") or "").strip()

    if not state_token:
        if not env_token:
            return None
        logger.info("Session vault: using EPIC_REFRESH_TOKEN from the environment")
        status, body = await _refresh_with(env_token)
        if body is None:
            if status in (400, 401, 403):
                logger.warning(
                    "Session vault: EPIC_REFRESH_TOKEN is not usable; remove or refresh it"
                )
            return None
        return body.get("access_token") if _store_token_payload(body, "env_token") else None

    status, body = await _refresh_with(state_token)
    if body is not None:
        return body.get("access_token") if _store_token_payload(body, "refresh") else None

    if status in (400, 401, 403):
        clear_state(f"refresh_rejected_{status}")
        if env_token and env_token != state_token:
            logger.warning("Session vault: cached token rejected; retrying with EPIC_REFRESH_TOKEN")
            status, body = await _refresh_with(env_token)
            if body is not None:
                return body.get("access_token") if _store_token_payload(body, "env_token") else None
            if status in (400, 401, 403):
                logger.warning("Session vault: EPIC_REFRESH_TOKEN is not usable either")
    return None


async def access_token() -> str | None:
    """Return a usable access token, refreshing only when it is close to expiry."""
    state = read_state()
    token = (state.get("access_token") or "").strip()
    expires_at = int(state.get("expires_at") or 0)
    if token and expires_at and expires_at - time.time() > REFRESH_SKEW_SECONDS:
        return token
    return await refresh_access_token()


# --- browser side ---------------------------------------------------------------
async def _api_json(response: Any) -> dict[str, Any] | None:
    """Read a Playwright APIResponse body as JSON.

    ``APIResponse.json()`` is a coroutine; treating it as a dict raises
    ``AttributeError: 'coroutine' object has no attribute 'get'`` and once killed a run
    right after a successful login.
    """
    try:
        data = await response.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


async def _api_text(response: Any, limit: int = 200) -> str:
    try:
        text = await response.text()
    except Exception:
        return ""
    return (text or "")[:limit]


def _extract_code(payload: dict[str, Any] | None) -> str | None:
    """Pull an authorization code out of either response shape.

    ``id/api/redirect`` answers ``{"authorizationCode": ...}`` only in some contexts; the
    cookie-session variant answers ``{"redirectUrl": ".../callback?code=..."}``, which is
    why a strict ``authorizationCode`` lookup returned nothing and seeding silently died.
    """
    if not payload:
        return None
    direct = payload.get("authorizationCode")
    if isinstance(direct, str) and direct:
        return direct
    for key in ("redirectUrl", "redirect_url", "url", "location"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        match = re.search(r"[?&]code=([^&#]+)", value)
        if match:
            return match.group(1)
    return None


async def mint_from_context(context: Any) -> bool:
    """Mint a refresh token from an already logged-in browser context.

    Uses the authorization-code grant: the logged-in web session yields a code, and
    the launcher client trades that code for an access/refresh token pair.
    """
    if not enabled():
        return False
    try:
        return await _mint(context)
    except Exception as err:  # session housekeeping must never abort a run
        logger.warning("Session vault: mint aborted | error={!r}", err)
        return False


async def _mint(context: Any) -> bool:
    redirect = await context.request.get(
        WEB_REDIRECT_URL,
        params={"clientId": LAUNCHER_CLIENT_ID, "responseType": "code"},
        timeout=HTTP_TIMEOUT_SECONDS * 1000,
    )
    if redirect.status != 200:
        logger.warning(
            "Session vault: mint redirect rejected | status={} | body={}",
            redirect.status,
            await _api_text(redirect),
        )
        return False

    code = _extract_code(await _api_json(redirect))
    if not code:
        logger.warning("Session vault: mint redirect returned no authorizationCode")
        return False

    payload = {"grant_type": "authorization_code", "code": code, "token_type": "eg1"}
    async with _client() as client:
        token_response = await client.post(OAUTH_TOKEN_URL, data=payload, auth=_basic_auth())

    if token_response.status_code != 200:
        logger.warning(
            "Session vault: code exchange rejected | status={} | body={}",
            token_response.status_code,
            token_response.text[:200],
        )
        return False

    try:
        body = token_response.json()
    except ValueError:
        logger.warning("Session vault: code exchange response was not JSON")
        return False

    return _store_token_payload(body, source="browser_login")


async def try_seed(context: Any) -> bool:
    """Turn the stored token into browser cookies so the login page never renders.

    ``context.request`` shares the cookie jar with the browser context, so a
    successful exchange leaves the context logged in for subsequent navigations.
    """
    if not enabled():
        return False

    token = await access_token()
    if not token:
        logger.info("Session vault: no usable token; falling back to the login page")
        return False

    try:
        return await _seed(context, token)
    except Exception as err:
        logger.warning("Session vault: seed aborted | error={!r}", err)
        return False


async def _seed(context: Any, token: str) -> bool:
    codes: list[str] = []
    # Two dimensions matter here and both were wrong in the first attempt:
    #  * id/api/exchange only accepts codes issued for the web client;
    #  * id/api/redirect is issued for the *cookie* session, and adding a bearer header
    #    makes it answer 400 "authorization is invalid" even for a valid token.
    # So try cookie-session first, then bearer, for the web client, then the launcher.
    for client_id in (STORE_CLIENT_ID, WEB_CLIENT_ID, LAUNCHER_CLIENT_ID):
        for auth_header in ({}, {"Authorization": f"bearer {token}"}):
            redirect = await context.request.get(
                WEB_REDIRECT_URL,
                params={"clientId": client_id, "responseType": "code"},
                headers=auth_header,
                timeout=HTTP_TIMEOUT_SECONDS * 1000,
            )
            if redirect.status != 200:
                logger.warning(
                    "Session vault: seed redirect rejected | client={} | auth_header={} | "
                    "status={} | body={}",
                    client_id,
                    bool(auth_header),
                    redirect.status,
                    await _api_text(redirect),
                )
                continue
            code = _extract_code(await _api_json(redirect))
            if not code:
                logger.warning("Session vault: seed redirect returned no authorizationCode")
                continue
            logger.info(
                "Session vault: authorization code issued | client={} | auth_header={}",
                client_id,
                bool(auth_header),
            )
            codes.append(code)

    if not codes:
        return False

    for code in codes:
        exchange = await context.request.get(
            WEB_EXCHANGE_URL,
            params={"code": code},
            timeout=HTTP_TIMEOUT_SECONDS * 1000,
        )
        if exchange.status < 400:
            logger.success("Session vault: session seeded from token | status={}", exchange.status)
            return True
        logger.warning(
            "Session vault: cookie exchange rejected | status={} | body={}",
            exchange.status,
            await _api_text(exchange),
        )
    return False


STORE_SILENT_LOGIN_URL = (
    "https://store.epicgames.com/auth/login?returnPath=%2Fen-US%2Ffree-games&prompt=none"
)
# Entry point that produces a store session as a by-product of logging in: the store
# redirects to the account service with its own client id and comes back with a code.
STORE_LOGIN_ENTRY = "https://store.epicgames.com/login"

_SWITCH_ACCOUNT_SELECTORS = (
    "[data-testid='modal-content'] [role='button']",
    "[data-testid='modal-content'] button",
    "button:has-text('Continue')",
    "//button[contains(normalize-space(.), 'Continue')]",
    "//*[@role='button'][contains(normalize-space(.), 'Continue')]",
)


async def _dump_page_html(page: Any, name: str, limit: int = 200_000) -> None:
    """Persist the current DOM so a failed handshake can be diagnosed from artifacts."""
    try:
        html = await page.content()
    except Exception as err:
        logger.warning("Could not dump {} DOM | error={!r}", name, err)
        return
    try:
        target = STATE_PATH.parent.joinpath(f"{name}.html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html[:limit], encoding="utf-8")
        logger.warning("Dumped {} DOM | path={} | url={}", name, target, page.url)
    except OSError as err:
        logger.warning("Could not write {} DOM | error={!r}", name, err)


async def store_logged_in(page: Any) -> bool:
    """Whether the store domain currently has a session.

    The old ``<egs-navigation isloggedin>`` attribute no longer exists on the 2026 React
    storefront. Order matters here: a visible "Sign In" control is checked first, because
    generic account links exist in the footer for logged-out visitors too - treating those
    as a session made the auth stage return ok without ever logging in.
    """
    for selector in (
        "button:has-text('Sign In')",
        "a:has-text('Sign In')",
        "button:has-text('Sign in')",
        "a[href*='/login']:has-text('Sign')",
    ):
        try:
            if await page.locator(selector).first.is_visible(timeout=800):
                return False
        except Exception:
            continue

    for selector in (
        "[data-testid='user-menu']",
        "[data-testid='account-menu']",
        "[data-testid='avatar']",
        "button[aria-label*='Account']",
        "[data-testid='user-avatar']",
        "[data-testid='account-avatar']",
    ):
        try:
            if await page.locator(selector).first.is_visible(timeout=800):
                return True
        except Exception:
            continue

    try:
        value = await page.locator("//egs-navigation").get_attribute("isloggedin", timeout=1200)
        if value == "true":
            return True
    except Exception:
        pass

    return False


async def confirm_switch_account(page: Any, email: str = "", timeout_seconds: int = 20) -> str:
    """Answer Epic's "Confirm Your Account" page.

    The dumped page (runtime/switch-account.html) contains "Welcome back <name> <email>",
    a clickable account tile, and exactly one button: "Use another account". There is no
    Continue button, which is why the first attempts matched nothing.

    Returns ``"confirmed"`` (left the page), ``"form"`` (switched to the credential form,
    which the caller fills) or ``"failed"``.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if "switch-account" not in (page.url or ""):
            return "confirmed"

        tile_selectors: list[str] = []
        if email:
            tile_selectors.append(f"text={email}")
            tile_selectors.append(f"//*[contains(normalize-space(.), '{email}')]")
        tile_selectors.extend(_SWITCH_ACCOUNT_SELECTORS)

        for selector in tile_selectors:
            try:
                candidate = page.locator(selector).first
                if await candidate.is_visible(timeout=1000):
                    await candidate.click(timeout=3000)
                    logger.info("switch-account: clicked the known account via {}", selector)
                    await page.wait_for_timeout(1500)
                    if "switch-account" not in (page.url or ""):
                        return "confirmed"
            except Exception:
                continue

        for selector in (
            "button:has-text('Use another account')",
            "text=Use another account",
            "//button[contains(normalize-space(.), 'another account')]",
        ):
            try:
                candidate = page.locator(selector).first
                if await candidate.is_visible(timeout=800):
                    await candidate.click(timeout=3000)
                    logger.info("switch-account: switched to the credential form")
                    await page.wait_for_timeout(1500)
                    return "form"
            except Exception:
                continue

        # The account tile is a plain element with a click handler, and the page masks the
        # address ("2***2@qq.com"), so neither an email text match nor a button selector can
        # reach it. Click the smallest node that contains the "Welcome back" greeting.
        # React ignores synthetic clicks (the DOM click above left the URL unchanged), so click
        # the tile and the escape button with real mouse events at their coordinates.
        with suppress(Exception):
            rect = await page.evaluate(
                """() => {
                    const nodes = Array.from(
                        document.querySelectorAll("div,span,button,a,li,[role='button']")
                    );
                    const pick = (re) => {
                        const hits = nodes.filter((n) => re.test(n.textContent || ""));
                        if (!hits.length) return null;
                        hits.sort(
                            (a, b) => (a.textContent || "").length - (b.textContent || "").length
                        );
                        const r = hits[0].getBoundingClientRect();
                        if (!r || !r.width || !r.height) return null;
                        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
                    };
                    return pick(/welcome back/i) || pick(/use another account/i);
                }"""
            )
            if rect:
                await page.mouse.click(rect["x"], rect["y"])
                logger.info("switch-account: trusted mouse click at ({}, {})", rect["x"], rect["y"])
                await page.wait_for_timeout(2000)
                if "switch-account" not in (page.url or ""):
                    return "confirmed"
                if await page.locator("#email").first.is_visible(timeout=1000):
                    return "form"

        with suppress(Exception):
            clicked = await page.evaluate(
                """() => {
                    const nodes = Array.from(
                        document.querySelectorAll("div,span,button,a,li,[role='button']")
                    );
                    const hits = nodes.filter((n) => /welcome back/i.test(n.textContent || ""));
                    if (!hits.length) return 0;
                    hits.sort((a, b) => (a.textContent || "").length - (b.textContent || "").length);
                    const el = hits[0];
                    el.click?.();
                    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
                    return hits.length;
                }"""
            )
            if clicked:
                logger.info(
                    "switch-account: clicked the account tile via DOM ({} candidates)", clicked
                )
                await page.wait_for_timeout(1800)
                if "switch-account" not in (page.url or ""):
                    return "confirmed"

        await page.wait_for_timeout(600)

    await _dump_page_html(page, "switch-account")
    logger.warning("switch-account confirmation failed | url={}", page.url)
    return "failed"


async def store_login_handoff(page: Any, timeout_seconds: int = 40) -> bool:
    """Log in *through the store* so the store domain gets its own session.

    Sequence: store /en-US/login -> account service (switch-account or credentials) ->
    redirect back to the store with a code. The claim page then behaves normally.
    """
    try:
        await page.goto(STORE_LOGIN_ENTRY, wait_until="domcontentloaded", timeout=30000)
    except Exception as err:
        logger.warning("Store login entry navigation failed | error={!r}", err)
        return False

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        url = page.url or ""
        if "switch-account" in url:
            outcome = await confirm_switch_account(
                page, email=(os.getenv("EPIC_EMAIL") or "").strip(), timeout_seconds=15
            )
            if outcome == "confirmed":
                continue
            logger.info("Store login needs credentials (%s); the caller owns that step", outcome)
            return False
        if await store_logged_in(page):
            logger.success("Store login handoff completed | url={}", url)
            return True
        if "epicgames.com/id/login" in url:
            logger.info(
                "Store login entry shows the credential form; the caller handles login | url={}",
                url,
            )
            return False
        await page.wait_for_timeout(700)

    await _dump_page_html(page, "store-login-handoff")
    logger.warning("Store login handoff did not complete | url={}", page.url)
    return False


async def store_silent_login(page: Any, timeout_seconds: int = 25) -> bool:
    """Backwards-compatible alias for the store login handoff."""
    return await store_login_handoff(page, timeout_seconds=timeout_seconds)


async def ensure_state(context: Any) -> bool:
    """Make sure a refresh token is stored, minting one from the live session."""
    if not enabled():
        return False
    state = read_state()
    if state.get("refresh_token"):
        return True
    if (os.getenv("EPIC_REFRESH_TOKEN") or "").strip():
        return True
    return await mint_from_context(context)


# --- shared verification helper --------------------------------------------------
async def load_order_history(context: Any) -> dict[str, Any] | None:
    """Read the order history as JSON through the browser cookie jar.

    The old implementation navigated to this URL and parsed ``<pre>`` text, which has
    failed with ``JSONDecodeError`` on every single run in the recorded history. The
    endpoint answers properly when it is asked for JSON by an XHR.
    """
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.epicgames.com/account/payments",
    }
    try:
        response = await context.request.get(
            ORDER_HISTORY_URL, headers=headers, timeout=HTTP_TIMEOUT_SECONDS * 1000
        )
    except Exception as err:
        logger.warning("Order history request failed | error={!r}", err)
        return None

    if response.status != 200:
        logger.warning(
            "Order history request returned status={} | body={}",
            response.status,
            await _api_text(response, 160),
        )
        return None

    payload = await _api_json(response)
    if payload is None:
        logger.warning(
            "Order history response was not JSON | body={}", await _api_text(response, 160)
        )
        return None

    if not isinstance(payload.get("orders"), list):
        logger.warning("Order history payload did not contain an orders list")
        return None
    return payload


def describe() -> str:
    """One-line state summary for run summaries; contains no secret material."""
    state = read_state()
    if not state:
        return "session_state=missing"
    expires_at = int(state.get("expires_at") or 0)
    remaining = int(expires_at - time.time()) if expires_at else 0
    return (
        f"session_state=present source={state.get('source', '?')} "
        f"refresh={'yes' if state.get('refresh_token') else 'no'} "
        f"access_ttl={remaining}s"
    )


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Best-effort claim dump for diagnostics; never used for decisions."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}
