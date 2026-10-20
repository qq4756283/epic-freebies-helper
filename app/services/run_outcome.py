# -*- coding: utf-8 -*-
"""
Structured run-outcome recording.

Writes a single-line JSON record to ``app/volumes/runtime/run-outcome.json`` so
GitHub Actions summaries and post-mortems can classify a finished run without
parsing raw browser logs. The file is overwritten by the final outcome of the
run (including per-account outcomes in multi-account setups).

Stage events are appended to ``app/volumes/runtime/stage-events.jsonl`` so a run
can be reconstructed stage by stage (auth / discover / claim / verify / notify)
instead of being reduced to one terminal label. Appending never overwrites, and
every writer swallows its own OSError: telemetry must not fail a claim.
"""

import json
from datetime import datetime, timezone
from typing import Any

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

# Stage names used by the orchestrator. Keep them stable: reports and workflows grep them.
STAGE_AUTH = "auth"
STAGE_DISCOVER = "discover"
STAGE_CLAIM = "claim"
STAGE_VERIFY = "verify"
STAGE_NOTIFY = "notify"

RESULT_OK = "ok"
RESULT_FAILED = "failed"
RESULT_SKIPPED = "skipped"
RESULT_PARTIAL = "partial"

RUN_OUTCOME_PATH = RUNTIME_DIR.joinpath("run-outcome.json")
STAGE_EVENTS_PATH = RUNTIME_DIR.joinpath("stage-events.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_run_outcome(outcome: str, detail: str | None = None, stage: str | None = None) -> None:
    """Persist the terminal outcome of the current run; never raises."""
    payload: dict[str, Any] = {
        "outcome": outcome,
        "detail": (detail or "").strip()[:300],
        "finished_at": _now(),
    }
    if stage:
        payload["stage"] = stage
    try:
        RUN_OUTCOME_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUN_OUTCOME_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        logger.info("Run outcome recorded | outcome={} | stage={} | detail={}", outcome, stage, payload["detail"])
    except OSError as err:
        logger.warning("Failed to persist run outcome | error={!r}", err)


def write_stage_event(
    stage: str,
    result: str,
    detail: str | None = None,
    **metrics: Any,
) -> None:
    """Append one stage record to the JSONL event log; never raises.

    ``metrics`` should hold small, greppable numbers (attempts, ms, captcha_seen,
    login_skipped, ...). Values must stay JSON-serialisable; anything else becomes str().
    """
    payload: dict[str, Any] = {"stage": stage, "result": result, "at": _now()}
    if detail:
        payload["detail"] = str(detail).strip()[:300]
    for key, value in metrics.items():
        if value is None:
            continue
        payload[key] = value if isinstance(value, (int, float, bool, str)) else str(value)
    try:
        STAGE_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STAGE_EVENTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as err:
        logger.warning("Failed to persist stage event | stage={} | error={!r}", stage, err)
        return
    logger.info(
        "Stage event | stage={} | result={} | {}",
        stage,
        result,
        payload.get("detail", ""),
    )


def read_stage_events() -> list[dict[str, Any]]:
    """Return the parsed stage events of the current run; empty list when unavailable."""
    try:
        lines = STAGE_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


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
