from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

from git_activity_monitor.digest import github_source
from git_activity_monitor.github_client import AlertsDisabledError, GitHubClient


def _client() -> MagicMock:
    return MagicMock(spec=GitHubClient)


def test_get_authenticated_user() -> None:
    client = _client()
    client.get_authenticated_user.return_value = "jasmeralia"
    assert github_source.get_authenticated_user(client) == "jasmeralia"


def test_list_repos_sorted() -> None:
    client = _client()
    client.get_owner_repos.return_value = ["jasmeralia/zzz", "jasmeralia/aaa"]
    assert github_source.list_repos(client, "jasmeralia") == [
        "jasmeralia/aaa",
        "jasmeralia/zzz",
    ]
    client.get_owner_repos.assert_called_once_with("jasmeralia")


def test_list_open_prs_translates_fields() -> None:
    client = _client()
    client.list_open_pull_requests.return_value = [
        {
            "number": 1,
            "title": "Fix thing",
            "user": {"login": "morgan"},
            "assignees": [{"login": "jas"}],
            "html_url": "https://github.com/jasmeralia/foo/pull/1",
            "created_at": "2026-07-27T15:00:00Z",
        }
    ]
    prs = github_source.list_open_prs(client, "jasmeralia/foo")
    client.list_open_pull_requests.assert_called_once_with("jasmeralia", "foo")
    assert prs == [
        {
            "number": 1,
            "title": "Fix thing",
            "author": {"login": "morgan"},
            "assignees": [{"login": "jas"}],
            "url": "https://github.com/jasmeralia/foo/pull/1",
            "createdAt": "2026-07-27T15:00:00Z",
        }
    ]


def test_list_open_prs_handles_missing_author_and_assignees() -> None:
    client = _client()
    client.list_open_pull_requests.return_value = [
        {
            "number": 1,
            "title": "Fix thing",
            "user": None,
            "assignees": [],
            "html_url": "...",
            "created_at": "2026-07-27T15:00:00Z",
        }
    ]
    prs = github_source.list_open_prs(client, "jasmeralia/foo")
    assert prs[0]["author"] == {"login": "unknown"}
    assert prs[0]["assignees"] == []


def test_list_merged_prs_since_translates_fields() -> None:
    client = _client()
    client.list_merged_pull_requests_since.return_value = [
        {
            "number": 2,
            "title": "Bump dep",
            "user": {"login": "dependabot"},
            "assignees": [],
            "html_url": "https://github.com/jasmeralia/foo/pull/2",
            "created_at": "2026-07-26T00:00:00Z",
            "merged_by": {"login": "morgan"},
            "merged_at": "2026-07-27T15:00:00Z",
            "head": {"sha": "abc123"},
        }
    ]
    since = dt.datetime(2026, 7, 27, tzinfo=dt.UTC)
    prs = github_source.list_merged_prs_since(client, "jasmeralia/foo", since)
    client.list_merged_pull_requests_since.assert_called_once_with("jasmeralia", "foo", since)
    assert prs[0]["mergedBy"] == {"login": "morgan"}
    assert prs[0]["mergedAt"] == "2026-07-27T15:00:00Z"
    assert prs[0]["headRefOid"] == "abc123"


def test_list_merged_prs_since_handles_missing_merged_by_and_head() -> None:
    client = _client()
    client.list_merged_pull_requests_since.return_value = [
        {
            "number": 2,
            "title": "Bump dep",
            "user": {"login": "dependabot"},
            "assignees": [],
            "html_url": "...",
            "created_at": "2026-07-26T00:00:00Z",
            "merged_by": None,
            "merged_at": "2026-07-27T15:00:00Z",
            "head": None,
        }
    ]
    prs = github_source.list_merged_prs_since(
        client, "jasmeralia/foo", dt.datetime(2026, 7, 27, tzinfo=dt.UTC)
    )
    assert prs[0]["mergedBy"] == {"login": "unknown"}
    assert prs[0]["headRefOid"] == ""


def test_list_auto_merge_shas_delegates_with_lookback() -> None:
    client = _client()
    client.list_workflow_run_head_shas.return_value = {"aaa", "bbb"}
    since = dt.datetime(2026, 7, 27, 15, 0, tzinfo=dt.UTC)
    result = github_source.list_auto_merge_shas(client, "jasmeralia/foo", since)
    assert result == {"aaa", "bbb"}
    client.list_workflow_run_head_shas.assert_called_once_with(
        "jasmeralia",
        "foo",
        github_source.AUTO_MERGE_WORKFLOW,
        dt.date(2026, 6, 27),
    )


def test_list_open_alerts_passes_through() -> None:
    client = _client()
    client.list_open_dependabot_alerts.return_value = [{"number": 1}, {"number": 2}]
    alerts = github_source.list_open_alerts(client, "jasmeralia/foo")
    assert [a["number"] for a in alerts] == [1, 2]
    client.list_open_dependabot_alerts.assert_called_once_with("jasmeralia", "foo")


def test_list_open_alerts_disabled_raises() -> None:
    client = _client()
    client.list_open_dependabot_alerts.side_effect = AlertsDisabledError("jasmeralia/foo")
    try:
        github_source.list_open_alerts(client, "jasmeralia/foo")
    except AlertsDisabledError:
        pass
    else:
        raise AssertionError("expected AlertsDisabledError")
