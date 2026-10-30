"""Decide whether a scheduled run should do anything at all.

GitHub's `schedule` silently drops firings: this repository had 8 straight days with
zero triggers while every workflow still reported `active`, and on 2026-10-21 the daily
slot was missed again. The cheap fix is redundancy (several cron slots per day) plus a
gate that makes each extra slot a no-op:

* a verified claim inside the last ``CLAIM_WINDOW_DAYS`` days -> skip (nothing to do);
* an authentication attempt inside the last ``ATTEMPT_COOLDOWN_HOURS`` hours -> skip, so
  the account is never hammered (Epic raises its risk score otherwise);
* otherwise -> run.

Stdlib only, so the gate job needs no uv/python setup and costs ~10 seconds.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOREBOARD = ROOT.joinpath("docs", "scoreboard.json")
RUNTIME_OUTCOME = ROOT.joinpath("app", "volumes", "runtime", "run-outcome.json")
OUTPUT_NAME = "run"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
CLAIM_WINDOW_DAYS = int(os.getenv("CLAIM_WINDOW_DAYS", "5"))
ATTEMPT_COOLDOWN_HOURS = float(os.getenv("ATTEMPT_COOLDOWN_HOURS", "12"))
VERIFIED_OUTCOMES = {"claimed_ok"}
ATTEMPT_OUTCOMES = {
    "auth_failed_captcha",
    "auth_failed_2fa",
    "session_ok",
    "manual_action_required",
}


def _load_history() -> list[dict]:
    try:
        data = json.loads(SCOREBOARD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return None


def _has_recent_claim(history: list[dict]) -> tuple[bool, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=CLAIM_WINDOW_DAYS)
    for entry in reversed(history):
        if entry.get("outcome") not in VERIFIED_OUTCOMES:
            continue
        if entry.get("stage") != "verify":
            continue
        at = _parse_at(entry.get("at"))
        if at and at >= cutoff:
            return True, f"verified claim at {entry.get('at')} is inside {CLAIM_WINDOW_DAYS}d"
    return False, "no recent verified claim"


def _recent_attempt(history: list[dict]) -> tuple[bool, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ATTEMPT_COOLDOWN_HOURS)
    for entry in reversed(history):
        outcome = entry.get("outcome")
        if outcome not in ATTEMPT_OUTCOMES:
            continue
        at = _parse_at(entry.get("at"))
        if at and at >= cutoff:
            return (
                True,
                f"attempted {entry.get('at')} ({outcome}), cooldown {ATTEMPT_COOLDOWN_HOURS}h",
            )
    return False, f"no attempt in the last {ATTEMPT_COOLDOWN_HOURS}h"


def main() -> int:
    history = _load_history()
    override = (os.getenv("GATE_FORCE", "") or "").strip().lower()
    if override in {"1", "true", "yes"}:
        decision, reason = True, "GATE_FORCE set"
    else:
        claimed, claim_reason = _has_recent_claim(history)
        attempted, attempt_reason = _recent_attempt(history)
        if claimed:
            decision, reason = False, claim_reason
        elif attempted:
            decision, reason = False, attempt_reason
        else:
            decision, reason = True, f"{claim_reason}; {attempt_reason}"

    runtime_outcome = ""
    try:
        runtime_outcome = str(json.loads(RUNTIME_OUTCOME.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass

    print(f"gate decision: run={decision} | {reason} | history={len(history)} entries")
    if runtime_outcome:
        print(f"gate: stale local outcome present: {runtime_outcome[:120]}")

    output_name = OUTPUT_NAME
    if output_path := os.getenv("GITHUB_OUTPUT"):
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{output_name}={'yes' if decision else 'no'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
