from __future__ import annotations

import datetime as dt
from unittest.mock import ANY, MagicMock, patch

import pytest

from git_activity_monitor.digest.cli import main
from git_activity_monitor.digest.models import DigestData, OpenPR

NOW = dt.datetime(2026, 7, 28, 15, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_skips_email_when_digest_empty(
    mock_collect: MagicMock, mock_send: MagicMock, capsys
) -> None:
    mock_collect.return_value = DigestData(owner="jasmeralia", generated_at=NOW, repos_checked=3)

    exit_code = main(["jasmeralia"])

    assert exit_code == 0
    mock_send.assert_not_called()
    assert "skipping email" in capsys.readouterr().out


@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_sends_email_when_digest_has_content(
    mock_collect: MagicMock, mock_send: MagicMock
) -> None:
    data = DigestData(owner="jasmeralia", generated_at=NOW, repos_checked=3)
    data.open_prs = [
        OpenPR(
            repo="jasmeralia/foo",
            number=1,
            title="A",
            author="alice",
            assignees="unassigned",
            url="https://gh/pr/1",
            created_at="2026-07-20",
        )
    ]
    mock_collect.return_value = data

    exit_code = main(["jasmeralia", "--recipient", "someone@example.com"])

    assert exit_code == 0
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["recipient"] == "someone@example.com"
    assert "2026-07-28" in kwargs["subject"]
    assert "jasmeralia/foo" in kwargs["html_body"]


@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_dry_run_never_sends(mock_collect: MagicMock, mock_send: MagicMock) -> None:
    data = DigestData(owner="jasmeralia", generated_at=NOW, repos_checked=3)
    data.open_prs = [
        OpenPR(
            repo="jasmeralia/foo",
            number=1,
            title="A",
            author="alice",
            assignees="unassigned",
            url="https://gh/pr/1",
            created_at="2026-07-20",
        )
    ]
    mock_collect.return_value = data

    exit_code = main(["jasmeralia", "--dry-run"])

    assert exit_code == 0
    mock_send.assert_not_called()


@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_writes_html_out_even_when_empty(
    mock_collect: MagicMock, mock_send: MagicMock, tmp_path
) -> None:
    mock_collect.return_value = DigestData(owner="jasmeralia", generated_at=NOW, repos_checked=3)
    out_path = tmp_path / "digest.html"

    exit_code = main(["jasmeralia", "--html-out", str(out_path)])

    assert exit_code == 0
    assert out_path.exists()
    assert "Git Activity Digest" in out_path.read_text(encoding="utf-8")
    mock_send.assert_not_called()


@patch("git_activity_monitor.digest.cli.github_source.get_authenticated_user")
@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_defaults_owner_to_authenticated_user(
    mock_collect: MagicMock, mock_send: MagicMock, mock_get_user: MagicMock
) -> None:
    mock_get_user.return_value = "jasmeralia"
    mock_collect.return_value = DigestData(owner="jasmeralia", generated_at=NOW, repos_checked=3)

    main([])

    mock_get_user.assert_called_once_with(ANY)
    mock_collect.assert_called_once()
    # First positional arg is the GitHubClient, second is the owner.
    assert mock_collect.call_args[0][1] == "jasmeralia"


@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_alert_skip_repos_flag(mock_collect: MagicMock, mock_send: MagicMock) -> None:
    mock_collect.return_value = DigestData(owner="jasmeralia", generated_at=NOW, repos_checked=3)

    main(["jasmeralia", "--alert-skip-repos", "jasmeralia/a, jasmeralia/b"])

    assert mock_collect.call_args.kwargs["alert_skip_repos"] == frozenset(
        {"jasmeralia/a", "jasmeralia/b"}
    )


@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_alert_skip_repos_defaults_to_skip_repos_env_var(
    mock_collect: MagicMock, mock_send: MagicMock, monkeypatch
) -> None:
    monkeypatch.setenv("SKIP_REPOS", "jasmeralia/skipped")
    mock_collect.return_value = DigestData(owner="jasmeralia", generated_at=NOW, repos_checked=3)

    main(["jasmeralia"])

    assert mock_collect.call_args.kwargs["alert_skip_repos"] == frozenset({"jasmeralia/skipped"})


@patch("git_activity_monitor.digest.cli.mailer.send_digest_email")
@patch("git_activity_monitor.digest.cli.collect_digest")
def test_main_requires_github_token(
    mock_collect: MagicMock, mock_send: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(SystemExit):
        main(["jasmeralia"])

    mock_collect.assert_not_called()
