# -*- coding: utf-8 -*-
"""One-time bootstrap: turn a human Epic login into a cloud-stored refresh token.

Why
---
The cloud workflow cannot pass the login hCaptcha reliably from a datacenter IP, and the
login page is the only place hCaptcha appears. One successful human login is enough
forever, because the token minted here is refreshed over plain HTTP on every later run
(see app/services/session_vault.py).

What it does
------------
1. Opens a real browser window on this machine (system Chrome/Edge if available,
   otherwise Firefox) with a persistent profile under app/volumes/bootstrap_profile.
2. You log in normally; the script waits until the order-history endpoint answers,
   which is the same test the cloud run uses.
3. Mints a launcher refresh_token through the authorization-code grant and writes it to
   app/volumes/runtime/session-state.json.
4. Verifies the token by refreshing it once, then offers to push it to the repository
   secret EPIC_REFRESH_TOKEN so the next scheduled run starts already logged in.

Usage
-----
    uv run python scripts/bootstrap_refresh_token.py            # normal flow
    uv run python scripts/bootstrap_refresh_token.py --check    # environment only
    uv run python scripts/bootstrap_refresh_token.py --no-secret --timeout 900

The password never passes through this script: you type it into Epic's own page. The
token is written to a git-ignored path and printed masked.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT.joinpath("app")
VOLUMES = APP_DIR.joinpath("volumes")
PROFILE_DIR = VOLUMES.joinpath("bootstrap_profile")
TOKEN_FILE = VOLUMES.joinpath("runtime", "refresh-token.txt")
DEFAULT_TIMEOUT = 600
GH_REPO = "qq4756283/epic-freebies-helper"
EPIC_LOGIN_URL = (
    "https://www.epicgames.com/id/login?lang=en-US&noHostRedirect=true"
    "&redirectUrl=https://store.epicgames.com/en-US/free-games"
)


def _bootstrap_paths() -> None:
    sys.path.insert(0, str(APP_DIR))
    VOLUMES.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def _mask(value: str | None) -> str:
    if not value:
        return "<empty>"
    return f"{value[:6]}…{value[-4:]} (len={len(value)})"


def _gh_available() -> bool:
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return False
    return result.returncode == 0


def _set_secret(token: str) -> bool:
    try:
        result = subprocess.run(
            ["gh", "secret", "set", "EPIC_REFRESH_TOKEN", "-R", GH_REPO, "--body", token],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as err:
        print(f"  ! gh secret set failed: {err!r}")
        return False
    if result.returncode != 0:
        print(f"  ! gh secret set returned {result.returncode}: {(result.stderr or '')[:200]}")
        return False
    print(f"  ✓ secret EPIC_REFRESH_TOKEN set on {GH_REPO}")
    return True


async def _open_context(playwright, proxy: str | None = None):
    """Prefer a real Chrome/Edge install; those produce far fewer hCaptcha prompts."""
    proxy_opts = {"server": proxy} if proxy else None
    attempts: list[tuple[str, dict]] = [
        ("chrome (system)", {"channel": "chrome"}),
        ("msedge (system)", {"channel": "msedge"}),
        ("firefox (playwright)", {"browser": "firefox"}),
        ("chromium (playwright)", {"browser": "chromium"}),
    ]
    errors: list[str] = []
    for label, kwargs in attempts:
        try:
            launch: dict = {
                "headless": False,
                "viewport": {"width": 1280, "height": 900},
            }
            if proxy_opts:
                launch["proxy"] = proxy_opts
            if kwargs.get("browser") == "firefox":
                context = await playwright.firefox.launch_persistent_context(
                    str(PROFILE_DIR), **launch
                )
            elif kwargs.get("browser") == "chromium":
                context = await playwright.chromium.launch_persistent_context(
                    str(PROFILE_DIR), **launch
                )
            else:
                context = await playwright.chromium.launch_persistent_context(
                    str(PROFILE_DIR), channel=kwargs["channel"], **launch
                )
        except Exception as err:
            errors.append(f"{label}: {type(err).__name__}: {str(err)[:120]}")
            continue
        print(f"  ✓ browser: {label}" + (f" | proxy={proxy}" if proxy else " | direct"))
        return context
    print("  ! no browser could be launched:")
    for line in errors:
        print(f"    - {line}")
    print("\n  Install one of them first, e.g.:  uv run playwright install firefox")
    return None


async def _wait_for_login(context, session_vault, timeout_seconds: int) -> bool:
    """Block until the order-history endpoint answers with real JSON."""
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(EPIC_LOGIN_URL, wait_until="domcontentloaded")
    print("\n>>> 在弹出的浏览器里登录你的 Epic 账号（密码只输入给 Epic，不经过本脚本）。")
    print(">>> 登录成功、看到商店页面后脚本会自动继续；不必关闭窗口。\n")

    started = time.monotonic()
    next_hint = 15
    while time.monotonic() - started < timeout_seconds:
        payload = await session_vault.load_order_history(context)
        if payload is not None:
            orders = payload.get("orders") or []
            print(
                f"  ✓ login detected after {int(time.monotonic() - started)}s "
                f"({len(orders)} orders visible)"
            )
            return True
        elapsed = int(time.monotonic() - started)
        if elapsed >= next_hint:
            print(f"  … 等待登录中（{elapsed}s / {timeout_seconds}s）")
            next_hint += 15
        await asyncio.sleep(2)

    print(f"  ! no login detected within {timeout_seconds}s")
    return False


async def _run(args: argparse.Namespace) -> int:
    _bootstrap_paths()
    try:
        from playwright.async_api import async_playwright
    except Exception as err:
        print(f"playwright is not importable: {err!r}")
        return 2

    import services.session_vault as session_vault

    print("== Epic refresh-token bootstrap ==")
    print(f"profile : {PROFILE_DIR}")
    print(f"state   : {session_vault.STATE_PATH}")

    async with async_playwright() as playwright:
        context = await _open_context(playwright, proxy=(args.proxy or None))
        if context is None:
            return 3
        try:
            if not await _wait_for_login(context, session_vault, args.timeout):
                return 4

            print("\n[1/3] 铸造 refresh_token（launcher client，authorization_code grant）")
            if not await session_vault.mint_from_context(context):
                print("  ! mint failed; see the warnings above")
                return 5
            state = session_vault.read_state()
            refresh_token = (state.get("refresh_token") or "").strip()
            if not refresh_token:
                print("  ! mint reported success but no refresh_token was stored")
                return 5
            print(f"  ✓ stored: {_mask(refresh_token)}")
            print(f"  ✓ {session_vault.describe()}")

            print("\n[2/3] 验证：用 refresh_token 换一次 access_token（服务器侧轮换）")
            access_token = await session_vault.refresh_access_token()
            if not access_token:
                print("  ! refresh failed; the token is not usable for the cloud run")
                return 6
            print(f"  ✓ refreshed access token: {_mask(access_token)}")

            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(
                session_vault.read_state().get("refresh_token", ""), encoding="utf-8"
            )
            print(f"  · full token also written to (git-ignored): {TOKEN_FILE}")

            if args.no_secret:
                print("\n[3/3] 跳过 secret 设置（--no-secret）")
                print("  手动设置：gh secret set EPIC_REFRESH_TOKEN -R " + GH_REPO)
                return 0

            print(f"\n[3/3] 写入仓库 secret EPIC_REFRESH_TOKEN（{GH_REPO}）")
            current = session_vault.read_state().get("refresh_token", "")
            if not _gh_available():
                print("  ! gh CLI 未登录，跳过自动设置")
                print("  手动设置：gh secret set EPIC_REFRESH_TOKEN -R " + GH_REPO)
                return 0
            if not args.yes:
                answer = input("  现在写入吗？[Y/n] ").strip().lower()
                if answer not in {"", "y", "yes"}:
                    print("  已跳过；稍后可用 --no-secret 之外的参数重跑，或手动 gh secret set")
                    return 0
            if not _set_secret(current):
                return 7

            print("\n完成。接下来：")
            print("  · 下一次定时运行（北京时间 00:30）会用这个令牌播种会话，不再出现登录页")
            print("  · 首次运行成功后，令牌会轮换并保存在 Actions 缓存里，secret 只作为兜底")
            print("  · 想立刻验证：gh workflow run epic-gamer.yml -R " + GH_REPO)
            return 0
        finally:
            try:
                await context.close()
            except Exception:
                pass


def _selftest() -> int:
    _bootstrap_paths()
    print("== self test ==")
    ok = True
    try:
        from playwright.async_api import async_playwright  # noqa: F401

        print("  ✓ playwright importable")
    except Exception as err:
        print(f"  ! playwright import failed: {err!r}")
        ok = False
    try:
        import services.session_vault as session_vault

        print(f"  ✓ session_vault importable ({session_vault.describe()})")
        print(f"  · state path: {session_vault.STATE_PATH}")
    except Exception as err:
        print(f"  ! session_vault import failed: {err!r}")
        ok = False
    print(f"  · gh available: {_gh_available()}")
    print(f"  · profile dir : {PROFILE_DIR}")
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="environment self test only")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="login wait (s)")
    parser.add_argument("--no-secret", action="store_true", help="do not touch gh secrets")
    parser.add_argument("--yes", action="store_true", help="do not prompt before setting secret")
    parser.add_argument(
        "--proxy",
        default=os.getenv("BOOTSTRAP_PROXY", ""),
        help="HTTP(S) proxy for the bootstrap browser, e.g. http://127.0.0.1:10808",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _selftest()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
