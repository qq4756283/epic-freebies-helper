from pathlib import Path

import services.epic_authorization_service as auth_module
from services.browser_context import PROFILE_HEALTH_MARKER, ensure_healthy_profile


def test_unhealthy_profile_is_cleared(tmp_path):
    profile = tmp_path / "user_data"
    profile.mkdir()
    (profile / "cookies.sqlite").write_bytes(b"junk")

    assert ensure_healthy_profile(profile) is True
    assert list(profile.iterdir()) == []


def test_healthy_profile_is_kept(tmp_path):
    profile = tmp_path / "user_data"
    profile.mkdir()
    (profile / "cookies.sqlite").write_bytes(b"junk")
    (profile / PROFILE_HEALTH_MARKER).write_text(
        "2026-08-22T00:00:00+00:00", encoding="utf-8"
    )

    assert ensure_healthy_profile(profile) is False
    assert (profile / "cookies.sqlite").exists()


def test_missing_or_empty_profile_is_untouched(tmp_path):
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    empty.mkdir()

    assert ensure_healthy_profile(missing) is False
    assert ensure_healthy_profile(empty) is False
    assert not missing.exists()
    assert list(empty.iterdir()) == []


def test_marker_written_via_active_profile_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EPIC_ACTIVE_PROFILE_DIR", str(tmp_path))
    agent = auth_module.EpicAuthorization.__new__(auth_module.EpicAuthorization)

    agent._mark_profile_healthy()

    assert (tmp_path / PROFILE_HEALTH_MARKER).is_file()


def test_marker_write_without_env_is_safe(monkeypatch):
    monkeypatch.delenv("EPIC_ACTIVE_PROFILE_DIR", raising=False)
    agent = auth_module.EpicAuthorization.__new__(auth_module.EpicAuthorization)

    # Must not raise when the browser context never recorded a profile dir.
    agent._mark_profile_healthy()
