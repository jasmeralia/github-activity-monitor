from __future__ import annotations

import datetime as dt
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from git_activity_monitor.digest import gh_cli


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_get_authenticated_user(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="jasmeralia\n")
    assert gh_cli.get_authenticated_user() == "jasmeralia"
    mock_run.assert_called_once_with(
        ["gh", "api", "user", "-q", ".login"],
        capture_output=True,
        text=True,
        check=True,
    )


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_repos_sorted(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        stdout=json.dumps(
            [{"nameWithOwner": "jasmeralia/zzz"}, {"nameWithOwner": "jasmeralia/aaa"}]
        )
    )
    assert gh_cli.list_repos("jasmeralia") == ["jasmeralia/aaa", "jasmeralia/zzz"]


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_open_prs_passes_expected_args(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="[]")
    gh_cli.list_open_prs("jasmeralia/foo")
    args = mock_run.call_args[0][0]
    assert args[:4] == ["gh", "pr", "list", "--repo"]
    assert "jasmeralia/foo" in args
    assert "--state" in args and "open" in args


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_merged_prs_since_builds_search_query(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="[]")
    since = dt.datetime(2026, 7, 27, 15, 0, tzinfo=dt.UTC)
    gh_cli.list_merged_prs_since("jasmeralia/foo", since)
    args = mock_run.call_args[0][0]
    search_idx = args.index("--search")
    assert args[search_idx + 1] == "is:merged merged:>=2026-07-27T15:00:00Z"
    json_idx = args.index("--json")
    assert "mergedBy" in args[json_idx + 1].split(",")


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_open_alerts_parses_paginated_output(mock_run: MagicMock) -> None:
    # gh api --paginate concatenates one JSON array per page back-to-back,
    # not one combined array -- this fixture has two pages glued together.
    page1 = json.dumps([{"number": 1}])
    page2 = json.dumps([{"number": 2}])
    mock_run.return_value = MagicMock(stdout=page1 + page2)
    alerts = gh_cli.list_open_alerts("jasmeralia/foo")
    assert [a["number"] for a in alerts] == [1, 2]


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_open_alerts_empty_output(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="")
    assert gh_cli.list_open_alerts("jasmeralia/foo") == []


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_open_alerts_disabled_raises(mock_run: MagicMock) -> None:
    mock_run.side_effect = subprocess.CalledProcessError(
        1, ["gh"], output="", stderr="Dependabot alerts are disabled for this repository"
    )
    with pytest.raises(gh_cli.AlertsDisabledError):
        gh_cli.list_open_alerts("jasmeralia/foo")


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_open_alerts_other_error_propagates(mock_run: MagicMock) -> None:
    mock_run.side_effect = subprocess.CalledProcessError(1, ["gh"], output="", stderr="boom")
    with pytest.raises(subprocess.CalledProcessError):
        gh_cli.list_open_alerts("jasmeralia/foo")


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_merged_prs_since_requests_head_sha(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="[]")
    gh_cli.list_merged_prs_since("jasmeralia/foo", dt.datetime(2026, 7, 27, tzinfo=dt.UTC))
    args = mock_run.call_args[0][0]
    json_idx = args.index("--json")
    assert "headRefOid" in args[json_idx + 1].split(",")


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_auto_merge_shas_collects_successful_runs(mock_run: MagicMock) -> None:
    # --slurp wraps each page of results in an outer array.
    pages = [
        {"workflow_runs": [{"head_sha": "aaa"}, {"head_sha": "bbb"}]},
        {"workflow_runs": [{"head_sha": "ccc"}]},
    ]
    mock_run.return_value = MagicMock(stdout=json.dumps(pages))
    since = dt.datetime(2026, 7, 27, 15, 0, tzinfo=dt.UTC)
    assert gh_cli.list_auto_merge_shas("jasmeralia/foo", since) == {"aaa", "bbb", "ccc"}
    endpoint = mock_run.call_args[0][0][2]
    assert gh_cli.AUTO_MERGE_WORKFLOW in endpoint
    assert "status=success" in endpoint
    # 30-day lookback before the merge window, URL-encoded ">=".
    assert "created=%3E%3D2026-06-27" in endpoint


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_auto_merge_shas_returns_empty_when_workflow_absent(mock_run: MagicMock) -> None:
    mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="gh: Not Found (HTTP 404)")
    assert (
        gh_cli.list_auto_merge_shas("jasmeralia/foo", dt.datetime(2026, 7, 27, tzinfo=dt.UTC))
        == set()
    )


@patch("git_activity_monitor.digest.gh_cli.subprocess.run")
def test_list_auto_merge_shas_reraises_other_errors(mock_run: MagicMock) -> None:
    mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="boom (HTTP 500)")
    with pytest.raises(subprocess.CalledProcessError):
        gh_cli.list_auto_merge_shas("jasmeralia/foo", dt.datetime(2026, 7, 27, tzinfo=dt.UTC))
