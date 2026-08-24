from __future__ import annotations

from git_activity_monitor.main import _parse_args


def test_parse_args_default_no_once() -> None:
    args = _parse_args([])
    assert args.once is False


def test_parse_args_once_flag() -> None:
    args = _parse_args(["--once"])
    assert args.once is True
