# -*- coding: utf-8 -*-
"""
Structured run-outcome recording.

Writes a single-line JSON record to ``app/volumes/runtime/run-outcome.json`` so
GitHub Actions summaries and post-mortems can classify a finished run without
parsing raw browser logs. The file is overwritten by the final outcome of the
run (including per-account outcomes in multi-account setups).
"""

import json
from datetime import datetime, timezone

from loguru import logger

from settings import RUNTIME_DIR

OUTCOME_CLAIMED_OK = "claimed_ok"
OUTCOME_ALL_ALREADY_OWNED = "all_already_owned"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_AUTH_FAILED_CAPTCHA = "auth_failed_captcha"
OUTCOME_AUTH_FAILED_2FA = "auth_failed_2fa"
OUTCOME_MANUAL_ACTION_REQUIRED = "manual_action_required"
OUTCOME_BROWSER_CRASHED_EXHAUSTED = "browser_crashed_exhausted"
OUTCOME_SESSION_VALID = "session_valid"
OUTCOME_SESSION_EXPIRED = "session_expired"

_AUTH_OUTCOMES = {OUTCOME_AUTH_FAILED_CAPTCHA, OUTCOME_AUTH_FAILED_2FA}

RUN_OUTCOME_PATH = RUNTIME_DIR.joinpath("run-outcome.json")


def write_run_outcome(outcome: str, detail: str | None = None) -> None:
    """Persist the terminal outcome of the current run; never raises."""
    payload = {
        "outcome": outcome,
        "detail": (detail or "").strip()[:300],
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        RUN_OUTCOME_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUN_OUTCOME_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        logger.info("Run outcome recorded | outcome={} | detail={}", outcome, payload["detail"])
    except OSError as err:
        logger.warning("Failed to persist run outcome | error={!r}", err)


def read_run_outcome_text() -> str:
    """Return the recorded outcome tag, or an empty string when unavailable."""
    try:
        payload = json.loads(RUN_OUTCOME_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    outcome = payload.get("outcome") if isinstance(payload, dict) else None
    return outcome if isinstance(outcome, str) else ""


def is_auth_outcome(outcome: str) -> bool:
    return outcome in _AUTH_OUTCOMES
