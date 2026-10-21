# -*- coding: utf-8 -*-
"""Login-only entry point used by the Epic Login Bootstrap workflow.

Why a separate entry point
--------------------------
The claim run is heavy: it logs in, discovers offers, drives checkout and reconciles.
When the only missing piece is a usable session, retrying that whole pipeline wastes
~12 minutes per attempt and drags the claim cache along.

This script does exactly one thing: authenticate (the vault mints the refresh token on
success) and report the outcome. Exit code 0 means a usable session exists, so the
workflow only saves the trusted session cache on success.

Usage:  uv run python scripts/login_only.py
"""

import asyncio
import sys

from loguru import logger

from services.browser_context import open_browser_context, resolve_headless_mode
from services.epic_authorization_service import EpicAuthorization
from services.run_outcome import (
    RESULT_FAILED,
    RESULT_OK,
    STAGE_AUTH,
    write_run_outcome,
    write_stage_event,
)
from services.session_vault import describe, read_state
from settings import settings


async def main() -> int:
    headless = resolve_headless_mode()
    logger.info(
        "Login-only bootstrap | headless={} | max_attempts={} | attempt_timeout={}s",
        headless,
        settings.AUTH_MAX_ATTEMPTS,
        settings.AUTH_ATTEMPT_TIMEOUT_SECONDS,
    )

    async with open_browser_context(headless=headless) as browser:
        page = browser.pages[0] if browser.pages else await browser.new_page()
        agent = EpicAuthorization(page)

        try:
            ok = await agent.invoke()
        except Exception as err:
            write_stage_event(STAGE_AUTH, RESULT_FAILED, f"{type(err).__name__}: {err}")
            write_run_outcome("auth_failed_exception", str(err)[:200], stage=STAGE_AUTH)
            logger.exception("Login-only bootstrap crashed")
            return 1

        state = read_state()
        has_token = bool(state.get("refresh_token"))
        logger.info(
            "Login-only result | authenticated={} | refresh_token={} | {}",
            ok,
            has_token,
            describe(),
        )

        if ok:
            write_stage_event(STAGE_AUTH, RESULT_OK, captcha_solved=None, refresh_token=has_token)
            write_run_outcome(
                "session_ok",
                f"refresh_token={'yes' if has_token else 'no'}",
                stage=STAGE_AUTH,
            )
            return 0

        write_stage_event(STAGE_AUTH, RESULT_FAILED, agent.outcome_reason)
        write_run_outcome(agent.outcome_reason or "auth_failed", stage=STAGE_AUTH)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
