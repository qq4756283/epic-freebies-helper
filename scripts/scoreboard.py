"""Append one run record to docs/scoreboard.json.

The scoreboard is the deadman's memory: it is the only durable evidence of "when did we
last actually claim something". A run that claims nothing still appends a record, so a
long gap of `claimed_ok`/`needs_human` rows is visible without reading any log.

Stdlib only, never raises for missing inputs, safe to run on every workflow run.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.joinpath("app", "volumes", "runtime")
SCOREBOARD = ROOT.joinpath("docs", "scoreboard.json")
STALE_DAYS = int(os.getenv("SCOREBOARD_STALE_DAYS", "8"))
MAX_ENTRIES = 400


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_events() -> list[dict]:
    events: list[dict] = []
    try:
        lines = RUNTIME.joinpath("stage-events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
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


def _stage_summary(events: list[dict]) -> list[dict]:
    summary = []
    for event in events:
        item = {"stage": event.get("stage"), "result": event.get("result")}
        for key in ("ms", "attempts", "max_attempts", "captcha_seen", "login_skipped"):
            if key in event:
                item[key] = event[key]
        summary.append(item)
    return summary


def _last_claim_age_days(history: list[dict]) -> float | None:
    for entry in reversed(history):
        if entry.get("outcome") == "claimed_ok" and entry.get("stage") == "verify":
            try:
                claimed_at = time.mktime(time.strptime(entry.get("at", ""), "%Y-%m-%dT%H:%M:%SZ"))
            except (TypeError, ValueError):
                continue
            return (time.time() - claimed_at) / 86400
    return None


def main() -> int:
    outcome = _read_json(RUNTIME.joinpath("run-outcome.json"), {})
    state = _read_json(RUNTIME.joinpath("session-state.json"), {})
    events = _read_events()

    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "event": os.getenv("GITHUB_EVENT_NAME", ""),
        "outcome": outcome.get("outcome", "unknown"),
        "stage": outcome.get("stage", ""),
        "detail": str(outcome.get("detail", ""))[:160],
        "stages": _stage_summary(events),
        "session": {
            "source": state.get("source", ""),
            "has_refresh_token": bool(state.get("refresh_token")),
        },
    }

    history = _read_json(SCOREBOARD, [])
    if not isinstance(history, list):
        history = []
    history.append(entry)
    history = history[-MAX_ENTRIES:]
    try:
        SCOREBOARD.write_text(
            json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as err:
        print(f"scoreboard: could not write {SCOREBOARD}: {err!r}")
        return 0

    print(
        f"scoreboard: appended outcome={entry['outcome']} run={entry['run_id']} entries={len(history)}"
    )

    age = _last_claim_age_days(history)
    if age is None:
        print("scoreboard: WARNING no verified claim in the recorded history")
    elif age > STALE_DAYS:
        print(
            f"scoreboard: WARNING last verified claim was {age:.1f} days ago "
            f"(threshold {STALE_DAYS}); treat this as a deadman signal"
        )
    else:
        print(f"scoreboard: last verified claim {age:.1f} days ago")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
