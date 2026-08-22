import asyncio

import pytest
from playwright.async_api import TargetClosedError

import deploy
from services.epic_games_service import EpicFreeGameRateLimitError


def test_message_marker_detects_driver_disconnect():
    err = Exception("Page.wait_for_timeout: Connection closed while reading from the driver")
    assert deploy._is_browser_crash_error(err) is True


def test_message_marker_detects_closed_target():
    err = Exception(
        "BrowserContext.new_page: Target page, context or browser has been closed"
    )
    assert deploy._is_browser_crash_error(err) is True


def test_target_closed_error_type_is_detected():
    assert deploy._is_browser_crash_error(TargetClosedError("Context closed")) is True


def test_cause_chain_is_walked():
    root = Exception("Connection closed while reading from the driver")
    head = RuntimeError("claim flow failed")
    head.__cause__ = root
    assert deploy._is_browser_crash_error(head) is True


def test_context_chain_is_walked():
    root = TargetClosedError("Target closed")
    head = RuntimeError("claim flow failed")
    head.__context__ = root
    assert deploy._is_browser_crash_error(head) is True


def test_business_errors_are_not_crashes():
    assert deploy._is_browser_crash_error(EpicFreeGameRateLimitError("wait 24 hours")) is False
    assert deploy._is_browser_crash_error(ValueError("unexpected selector")) is False


def test_restart_recovers_from_midrun_crash(monkeypatch):
    calls = {"browser_runs": 0}

    async def flaky_execute(headless, *, collect_summary):
        calls["browser_runs"] += 1
        if calls["browser_runs"] == 1:
            raise Exception(
                "Page.wait_for_timeout: Connection closed while reading from the driver"
            )
        return {"ok": True}

    async def instant_sleep(_seconds):
        return None

    monkeypatch.setattr(deploy, "execute_browser_tasks", flaky_execute)
    monkeypatch.setattr(deploy.asyncio, "sleep", instant_sleep)

    summary = asyncio.run(
        deploy._execute_browser_tasks_with_restarts(headless=True, collect_summary=False)
    )

    assert summary == {"ok": True}
    assert calls["browser_runs"] == 2


def test_no_restart_for_business_errors(monkeypatch):
    calls = {"browser_runs": 0}

    async def failing_execute(headless, *, collect_summary):
        calls["browser_runs"] += 1
        raise EpicFreeGameRateLimitError("wait 24 hours before trying again")

    async def unexpected_sleep(_seconds):
        raise AssertionError("sleep must not be reached without a browser crash")

    monkeypatch.setattr(deploy, "execute_browser_tasks", failing_execute)
    monkeypatch.setattr(deploy.asyncio, "sleep", unexpected_sleep)

    with pytest.raises(EpicFreeGameRateLimitError):
        asyncio.run(
            deploy._execute_browser_tasks_with_restarts(headless=True, collect_summary=False)
        )

    assert calls["browser_runs"] == 1
