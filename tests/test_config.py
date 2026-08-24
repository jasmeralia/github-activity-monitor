from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from git_activity_monitor.config import Settings


def _make(**kwargs: object) -> Settings:
    base = {
        "github_token": "tok",
        "discord_webhook_url": "https://discord.com/api/webhooks/1/t",
        "repositories": ["owner/repo"],
        "_env_file": None,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_basic_valid() -> None:
    s = _make()
    assert s.github_token == "tok"
    assert s.repositories == ["owner/repo"]
    assert s.poll_interval_seconds == 300


def test_comma_split_repositories() -> None:
    s = _make(repositories="owner/a,owner/b, owner/c")
    assert s.repositories == ["owner/a", "owner/b", "owner/c"]


def test_comma_split_enabled_events() -> None:
    s = _make(enabled_events="stars,prs")
    assert s.enabled_events == ["stars", "prs"]


def test_comma_split_ghcr_packages() -> None:
    s = _make(ghcr_packages="owner/pkg1,owner/pkg2")
    assert s.ghcr_packages == ["owner/pkg1", "owner/pkg2"]


def test_invalid_repo_format_raises() -> None:
    with pytest.raises(ValidationError, match="owner/repo"):
        _make(repositories=["notarepo"])


def test_unknown_event_raises() -> None:
    with pytest.raises(ValidationError, match="Unknown event"):
        _make(enabled_events=["stars", "unknown_event"])


def test_missing_github_token_raises() -> None:
    with pytest.raises(ValidationError):
        Settings(
            discord_webhook_url="https://discord.com/api/webhooks/1/t",
            repositories=["owner/repo"],
            _env_file=None,  # type: ignore[call-arg]
        )


def test_neither_owners_nor_repositories_raises() -> None:
    with pytest.raises(ValidationError, match="OWNERS or REPOSITORIES"):
        Settings(
            github_token="tok",
            discord_webhook_url="https://discord.com/api/webhooks/1/t",
            owners=[],
            repositories=[],
            _env_file=None,  # type: ignore[call-arg]
        )


def test_owners_alone_is_valid() -> None:
    s = Settings(
        github_token="tok",
        discord_webhook_url="https://discord.com/api/webhooks/1/t",
        owners=["jasmeralia"],
        _env_file=None,  # type: ignore[call-arg]
    )
    assert s.owners == ["jasmeralia"]
    assert s.repositories == []


def test_comma_split_owners() -> None:
    s = _make(owners="alice, bob,carol")
    assert s.owners == ["alice", "bob", "carol"]


def test_dotenv_comma_split_list_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GITHUB_TOKEN=tok",
                "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/t",
                "OWNERS=alice,bob",
                "ENABLED_EVENTS=stars,watches,prs",
            ]
        ),
        encoding="utf-8",
    )

    s = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert s.owners == ["alice", "bob"]
    assert s.enabled_events == ["stars", "watches", "prs"]


def test_invalid_log_level_raises() -> None:
    with pytest.raises(ValidationError, match="Invalid log level"):
        _make(log_level="VERBOSE")


def test_ghcr_enabled_no_packages_no_owners_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        _make(enabled_events="stars,ghcr", ghcr_packages=[])
    assert "ghcr" in caplog.text.lower()


def test_ghcr_enabled_no_packages_with_owners_no_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        _make(enabled_events="stars,ghcr", ghcr_packages=[], owners=["alice"])
    assert "no-op" not in caplog.text


def test_state_file_path_default() -> None:
    s = _make()
    assert s.state_file_path == Path("/data/state.json")


def test_poll_interval_too_small_raises() -> None:
    with pytest.raises(ValidationError, match="30"):
        _make(poll_interval_seconds=5)


def test_invalid_ghcr_package_format_raises() -> None:
    with pytest.raises(ValidationError, match="owner/package"):
        _make(ghcr_packages=["notapackage"])


def test_run_once_default_false() -> None:
    s = _make()
    assert s.run_once is False


def test_run_once_from_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GITHUB_TOKEN=tok",
                "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/t",
                "REPOSITORIES=owner/repo",
                "RUN_ONCE=1",
            ]
        ),
        encoding="utf-8",
    )

    s = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert s.run_once is True


def test_pinned_message_id_optional() -> None:
    s = _make(discord_pinned_message_id="12345")
    assert s.discord_pinned_message_id == "12345"

    s2 = _make()
    assert s2.discord_pinned_message_id is None
