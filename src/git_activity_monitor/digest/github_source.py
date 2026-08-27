"""Adapter over GitHubClient for the digest.

Runs against the GitHub REST API via httpx (see github_client.GitHubClient)
rather than shelling out to the gh CLI. This digest used to deliberately
avoid a GITHUB_TOKEN by relying on gelfling's pre-authenticated gh CLI (see
git history for gh_cli.py); now that it runs on TrueNAS it reuses the same
GITHUB_TOKEN already deployed there for git-activity-monitor's Discord
poller.

Translates REST PR fields (user, created_at, html_url, merged_by, head.sha)
into the gh-CLI-JSON-style keys (author, createdAt, url, mergedBy,
headRefOid) collect.py's field access already expects, so collect.py itself
didn't need to change.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from git_activity_monitor.github_client import AlertsDisabledError, GitHubClient

__all__ = [
    "AUTO_MERGE_WORKFLOW",
    "AlertsDisabledError",
    "get_authenticated_user",
    "list_auto_merge_shas",
    "list_merged_prs_since",
    "list_open_alerts",
    "list_open_prs",
    "list_repos",
]

# The workflow every repo uses to auto-merge Dependabot PRs. It merges with a
# PAT belonging to the repo owner (a GITHUB_TOKEN merge would not trigger the
# release workflow), so `mergedBy` on such a PR is indistinguishable from a
# manual merge -- hence matching on this workflow's runs instead.
AUTO_MERGE_WORKFLOW = "dependabot-auto-merge.yml"

# How far before the merge window to look for auto-merge runs. The run fires
# when the PR is opened, which can be well before it merges (auto-merge waits
# for CI), so the run may predate `since` by a lot.
_RUN_LOOKBACK_DAYS = 30


def _split(repo: str) -> tuple[str, str]:
    owner, name = repo.split("/", 1)
    return owner, name


def _translate_pr(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": raw["number"],
        "title": raw["title"],
        "author": {"login": (raw.get("user") or {}).get("login", "unknown")},
        "assignees": [{"login": a["login"]} for a in (raw.get("assignees") or [])],
        "url": raw["html_url"],
        "createdAt": raw["created_at"],
    }


def _translate_merged_pr(raw: dict[str, Any]) -> dict[str, Any]:
    translated = _translate_pr(raw)
    translated["mergedBy"] = {"login": (raw.get("merged_by") or {}).get("login", "unknown")}
    translated["mergedAt"] = raw["merged_at"]
    translated["headRefOid"] = (raw.get("head") or {}).get("sha", "")
    return translated


def get_authenticated_user(client: GitHubClient) -> str:
    return client.get_authenticated_user()


def list_repos(client: GitHubClient, owner: str) -> list[str]:
    """Non-fork, non-archived repos owned directly by `owner`, sorted by name."""
    return sorted(client.get_owner_repos(owner))


def list_open_prs(client: GitHubClient, repo: str) -> list[dict[str, Any]]:
    """Raw open-PR dicts for one repo: number, title, author, assignees, url, createdAt."""
    owner, name = _split(repo)
    return [_translate_pr(raw) for raw in client.list_open_pull_requests(owner, name)]


def list_merged_prs_since(
    client: GitHubClient, repo: str, since: dt.datetime
) -> list[dict[str, Any]]:
    """Raw merged-PR dicts for one repo, merged at or after `since` (UTC)."""
    owner, name = _split(repo)
    return [
        _translate_merged_pr(raw)
        for raw in client.list_merged_pull_requests_since(owner, name, since)
    ]


def list_auto_merge_shas(client: GitHubClient, repo: str, since: dt.datetime) -> set[str]:
    """Head SHAs the Dependabot auto-merge workflow ran successfully against.

    Returns an empty set when the repo has no such workflow.
    """
    owner, name = _split(repo)
    created_from = (since - dt.timedelta(days=_RUN_LOOKBACK_DAYS)).astimezone(dt.UTC).date()
    return client.list_workflow_run_head_shas(owner, name, AUTO_MERGE_WORKFLOW, created_from)


def list_open_alerts(client: GitHubClient, repo: str) -> list[dict[str, Any]]:
    """Raw open Dependabot alert dicts for one repo.

    Raises AlertsDisabledError when the repo has Dependabot alerts (or its
    dependency graph) turned off.
    """
    owner, name = _split(repo)
    return client.list_open_dependabot_alerts(owner, name)
