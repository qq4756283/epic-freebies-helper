#!/usr/bin/env python3
"""
Session keepalive entrypoint.

Opens the cached browser profile, verifies the Epic session is still alive,
and refreshes the profile-health marker so scheduled claim runs can skip the
login hCaptcha gamble entirely. Never attempts a login and never touches the
claim flow:

- Session alive: refresh cookies by loading the store, mark profile healthy.
- Session dead: wipe the poisoned profile so the saved cache cannot degrade
  later runs; the next scheduled run performs one cold-start login.
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from loguru import logger  # noqa: E402

from services.browser_context import (  # noqa: E402
    ACTIVE_PROFILE_DIR_ENV,
    open_browser_context,
    resolve_headless_mode,
)
from services.epic_authorization_service import EpicAuthorization  # noqa: E402
from services.run_outcome import OUTCOME_SESSION_EXPIRED, OUTCOME_SESSION_VALID, write_run_outcome  # noqa: E402
from utils import init_log  # noqa: E402
from settings import LOG_DIR  # noqa: E402

URL_STORE_FREE_GAMES = "https://store.epicgames.com/en-US/free-games"

init_log(
    runtime=LOG_DIR.joinpath("runtime.log"),
    error=LOG_DIR.joinpath("error.log"),
    serialize=LOG_DIR.joinpath("serialize.log"),
)


def _drop_active_profile() -> None:
    profile_dir = os.getenv(ACTIVE_PROFILE_DIR_ENV)
    if not profile_dir:
        logger.warning("No active profile directory recorded; nothing to wipe")
        return
    target = Path(profile_dir)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    logger.warning("Expired browser profile wiped from cache | dir={}", target)


async def main() -> None:
    headless = resolve_headless_mode()
    async with open_browser_context(headless=headless) as browser:
        page = browser.pages[0] if browser.pages else await browser.new_page()
        auth = EpicAuthorization(page)

        alive = False
        try:
            await page.goto(URL_STORE_FREE_GAMES, wait_until="domcontentloaded", timeout=45000)
            alive = await auth.refresh_session_health()
        except Exception as err:
            logger.warning("Session probe failed; treating as expired | error={!r}", err)

        if alive:
            write_run_outcome(OUTCOME_SESSION_VALID)
            logger.success("Epic session is alive; profile refreshed for scheduled runs")
            return

        write_run_outcome(OUTCOME_SESSION_EXPIRED)
        _drop_active_profile()
        logger.error(
            "Epic session has expired. The poisoned profile was wiped; "
            "the next scheduled claim run will perform one cold-start login."
        )


if __name__ == "__main__":
    asyncio.run(main())
