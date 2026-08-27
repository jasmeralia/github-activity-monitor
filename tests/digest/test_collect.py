from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from git_activity_monitor.digest import github_source
from git_activity_monitor.digest.collect import collect_digest

NOW = dt.datetime(2026, 7, 28, 15, 0, tzinfo=dt.UTC)
CLIENT = MagicMock(name="GitHubClient")


def _open_pr(number: int, assignees: list[str] | None = None) -> dict:  # type: ignore[type-arg]
    return {
        "number": number,
        "title": f"PR {number}",
        "author": {"login": "alice"},
        "assignees": [{"login": a} for a in (assignees or [])],
        "url": f"https://gh/pr/{number}",
        "createdAt": "2026-07-20T10:00:00Z",
    }


def _merged_pr(number: int, merged_at: str) -> dict:  # type: ignore[type-arg]
    return {
        "number": number,
        "title": f"Merged PR {number}",
        "author": {"login": "bob"},
        "mergedBy": {"login": "carol"},
        "url": f"https://gh/pr/{number}",
        "mergedAt": merged_at,
    }


def _alert(number: int, severity: str = "high") -> dict:  # type: ignore[type-arg]
    return {
        "number": number,
        "security_advisory": {
            "severity": severity,
            "ghsa_id": f"GHSA-{number}",
            "cve_id": None,
            "summary": "Something bad",
        },
        "dependency": {"package": {"ecosystem": "npm", "name": "leftpad"}},
        "html_url": f"https://gh/alert/{number}",
        "created_at": "2026-07-21T10:00:00Z",
    }


@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_aggregates_across_repos(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts
) -> None:
    mock_list_repos.return_value = ["jasmeralia/a", "jasmeralia/b"]
    mock_open_prs.side_effect = lambda client, repo: [_open_pr(1)] if repo == "jasmeralia/a" else []
    mock_merged_prs.side_effect = lambda client, repo, since: (
        [_merged_pr(2, "2026-07-28T10:00:00Z")] if repo == "jasmeralia/b" else []
    )
    mock_alerts.side_effect = lambda client, repo: (
        [_alert(3, "critical")] if repo == "jasmeralia/a" else []
    )

    data = collect_digest(CLIENT, "jasmeralia", now=NOW)

    assert data.repos_checked == 2
    assert data.open_pr_count == 1
    assert data.open_prs[0].repo == "jasmeralia/a"
    assert data.open_prs[0].assignees == "unassigned"
    assert data.merged_pr_count == 1
    assert data.merged_prs[0].repo == "jasmeralia/b"
    assert data.merged_prs[0].author == "bob"
    assert data.merged_prs[0].merged_by == "carol"
    assert data.alert_count == 1
    assert data.alerts[0].severity == "critical"
    assert data.alerts[0].advisory_id == "GHSA-3"


@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_records_assignees(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts
) -> None:
    mock_list_repos.return_value = ["jasmeralia/a"]
    mock_open_prs.return_value = [_open_pr(1, assignees=["alice", "bob"])]
    mock_merged_prs.return_value = []
    mock_alerts.return_value = []

    data = collect_digest(CLIENT, "jasmeralia", now=NOW)

    assert data.open_prs[0].assignees == "alice, bob"


@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_tracks_disabled_alert_repos(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts
) -> None:
    mock_list_repos.return_value = ["jasmeralia/a"]
    mock_open_prs.return_value = []
    mock_merged_prs.return_value = []
    mock_alerts.side_effect = github_source.AlertsDisabledError("jasmeralia/a")

    data = collect_digest(CLIENT, "jasmeralia", now=NOW)

    assert data.alerts_disabled_repos == ["jasmeralia/a"]
    assert data.alert_count == 0
    assert data.is_empty()


@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_alert_skip_repos_excludes_entirely(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts
) -> None:
    # A repo with alerts disabled by design: skipping it entirely should mean
    # it never shows up as "disabled" noise either, while still being
    # scanned normally for open/merged PRs.
    mock_list_repos.return_value = ["jasmeralia/skipped", "jasmeralia/ok"]
    mock_open_prs.return_value = [_open_pr(1)]
    mock_merged_prs.return_value = []
    # list_open_alerts must never be called for the skipped repo at all --
    # if the skip logic didn't short-circuit, this would raise for it too.
    mock_alerts.return_value = []

    data = collect_digest(
        CLIENT, "jasmeralia", now=NOW, alert_skip_repos=frozenset({"jasmeralia/skipped"})
    )

    assert data.alerts_disabled_repos == []
    mock_alerts.assert_called_once_with(CLIENT, "jasmeralia/ok")
    # PRs are unaffected by the alert skip list.
    assert data.open_pr_count == 2


@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_one_repo_failure_does_not_abort_others(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts
) -> None:
    mock_list_repos.return_value = ["jasmeralia/broken", "jasmeralia/ok"]
    mock_open_prs.side_effect = lambda client, repo: (
        (_ for _ in ()).throw(RuntimeError("boom"))
        if repo == "jasmeralia/broken"
        else [_open_pr(9)]
    )
    mock_merged_prs.return_value = []
    mock_alerts.return_value = []

    data = collect_digest(CLIENT, "jasmeralia", now=NOW)

    assert data.open_pr_count == 1
    assert data.open_prs[0].repo == "jasmeralia/ok"


@patch("git_activity_monitor.digest.collect.github_source.list_auto_merge_shas")
@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_flags_auto_merged_prs(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts, mock_auto_shas
) -> None:
    mock_list_repos.return_value = ["jasmeralia/a"]
    mock_open_prs.return_value = []
    mock_alerts.return_value = []
    auto = _merged_pr(1, "2026-07-28T10:00:00Z") | {"headRefOid": "sha-auto"}
    manual = _merged_pr(2, "2026-07-28T11:00:00Z") | {"headRefOid": "sha-manual"}
    mock_merged_prs.return_value = [auto, manual]
    mock_auto_shas.return_value = {"sha-auto"}

    data = collect_digest(CLIENT, "jasmeralia", now=NOW)

    by_number = {pr.number: pr for pr in data.merged_prs}
    assert by_number[1].auto_merged is True
    assert by_number[2].auto_merged is False
    assert data.auto_merged_pr_count == 1


@patch("git_activity_monitor.digest.collect.github_source.list_auto_merge_shas")
@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_skips_auto_merge_lookup_without_merges(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts, mock_auto_shas
) -> None:
    mock_list_repos.return_value = ["jasmeralia/a"]
    mock_open_prs.return_value = []
    mock_merged_prs.return_value = []
    mock_alerts.return_value = []

    collect_digest(CLIENT, "jasmeralia", now=NOW)

    mock_auto_shas.assert_not_called()


@patch("git_activity_monitor.digest.collect.github_source.list_auto_merge_shas")
@patch("git_activity_monitor.digest.collect.github_source.list_open_alerts")
@patch("git_activity_monitor.digest.collect.github_source.list_merged_prs_since")
@patch("git_activity_monitor.digest.collect.github_source.list_open_prs")
@patch("git_activity_monitor.digest.collect.github_source.list_repos")
def test_collect_digest_keeps_merged_prs_when_auto_merge_lookup_fails(
    mock_list_repos, mock_open_prs, mock_merged_prs, mock_alerts, mock_auto_shas
) -> None:
    mock_list_repos.return_value = ["jasmeralia/a"]
    mock_open_prs.return_value = []
    mock_alerts.return_value = []
    mock_merged_prs.return_value = [_merged_pr(1, "2026-07-28T10:00:00Z") | {"headRefOid": "sha"}]
    mock_auto_shas.side_effect = RuntimeError("gh exploded")

    data = collect_digest(CLIENT, "jasmeralia", now=NOW)

    # The PR still shows up; it just falls back to being reported as manual.
    assert len(data.merged_prs) == 1
    assert data.merged_prs[0].auto_merged is False
