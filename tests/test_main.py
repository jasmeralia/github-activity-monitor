from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from git_activity_monitor import main as main_module
from git_activity_monitor.config import Settings
from git_activity_monitor.main import _parse_args


def test_parse_args_default_no_once() -> None:
    args = _parse_args([])
    assert args.once is False


def test_parse_args_once_flag() -> None:
    args = _parse_args(["--once"])
    assert args.once is True


def _make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    kwargs: dict[str, Any] = {
        "github_token": "tok",
        "discord_webhook_url": "https://discord.com/api/webhooks/1/t",
        "repositories": ["owner/repo"],
        "state_file_path": tmp_path / "state.json",
        "_env_file": None,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def _fake_client_cm() -> MagicMock:
    cm = MagicMock()
    cm.__enter__.return_value = cm
    cm.__exit__.return_value = False
    return cm


def test_main_once_flag_runs_single_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "GitHubClient", lambda token: _fake_client_cm())
    monkeypatch.setattr(main_module, "DiscordClient", lambda url: _fake_client_cm())

    calls: list[Any] = []
    monkeypatch.setattr(main_module, "_run_cycle", lambda *a, **k: calls.append((a, k)))

    main_module.main(["--once"])

    assert len(calls) == 1


def test_main_run_once_setting_runs_single_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _make_settings(tmp_path, run_once=True)
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "GitHubClient", lambda token: _fake_client_cm())
    monkeypatch.setattr(main_module, "DiscordClient", lambda url: _fake_client_cm())

    calls: list[Any] = []
    monkeypatch.setattr(main_module, "_run_cycle", lambda *a, **k: calls.append((a, k)))

    main_module.main([])

    assert len(calls) == 1


def test_main_daemon_loop_stops_on_shutdown_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _make_settings(tmp_path).model_copy(update={"poll_interval_seconds": 0})
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "GitHubClient", lambda token: _fake_client_cm())
    monkeypatch.setattr(main_module, "DiscordClient", lambda url: _fake_client_cm())

    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        main_module.signal,
        "signal",
        lambda sig, handler: handlers.__setitem__(sig, handler),
    )

    calls: list[Any] = []

    def fake_run_cycle(*_args: object, **_kwargs: object) -> None:
        calls.append(None)
        handlers[main_module.signal.SIGTERM](main_module.signal.SIGTERM, None)

    monkeypatch.setattr(main_module, "_run_cycle", fake_run_cycle)

    main_module.main([])

    assert len(calls) == 1


def test_main_daemon_loop_sleeps_then_breaks_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _make_settings(tmp_path).model_copy(update={"poll_interval_seconds": 30})
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "GitHubClient", lambda token: _fake_client_cm())
    monkeypatch.setattr(main_module, "DiscordClient", lambda url: _fake_client_cm())

    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        main_module.signal,
        "signal",
        lambda sig, handler: handlers.__setitem__(sig, handler),
    )

    cycle_calls: list[Any] = []
    monkeypatch.setattr(main_module, "_run_cycle", lambda *a, **k: cycle_calls.append(None))

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        handlers[main_module.signal.SIGTERM](main_module.signal.SIGTERM, None)

    monkeypatch.setattr(main_module.time, "sleep", fake_sleep)

    main_module.main([])

    assert len(cycle_calls) == 1
    assert len(sleep_calls) == 1
