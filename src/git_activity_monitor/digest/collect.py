"""Aggregate open PRs, recently-merged PRs, and open Dependabot alerts for
every repo an owner has, into a single DigestData ready to render or mail."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from git_activity_monitor.digest import github_source
from git_activity_monitor.digest.models import Alert, DigestData, MergedPR, OpenPR
from git_activity_monitor.github_client import GitHubClient

logger = logging.getLogger(__name__)


def _severity(raw: dict[str, Any]) -> str:
    return str(raw.get("security_advisory", {}).get("severity") or "unknown")


def _advisory_id(raw: dict[str, Any]) -> str:
    advisory = raw.get("security_advisory", {})
    return str(advisory.get("ghsa_id") or advisory.get("cve_id") or "")


def _auto_merge_shas(client: GitHubClient, repo: str, since: dt.datetime) -> frozenset[str]:
    """Auto-merge-workflow head SHAs for `repo`, or empty if they can't be read.

    A failure here only costs the auto-merge annotation, so it must never take
    the whole repo's merged-PR section down with it.
    """
    try:
        return frozenset(github_source.list_auto_merge_shas(client, repo, since))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to list auto-merge runs for %s; assuming manual merges", repo)
        return frozenset()


def collect_digest(
    client: GitHubClient,
    owner: str,
    now: dt.datetime | None = None,
    merged_window_hours: int = 24,
    alert_skip_repos: frozenset[str] = frozenset(),
) -> DigestData:
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(hours=merged_window_hours)

    repos = github_source.list_repos(client, owner)
    data = DigestData(
        owner=owner,
        generated_at=now,
        repos_checked=len(repos),
        merged_window_hours=merged_window_hours,
    )

    for repo in repos:
        try:
            for raw in github_source.list_open_prs(client, repo):
                assignees = [a["login"] for a in raw.get("assignees", [])]
                data.open_prs.append(
                    OpenPR(
                        repo=repo,
                        number=raw["number"],
                        title=raw["title"],
                        author=(raw.get("author") or {}).get("login", "unknown"),
                        assignees=", ".join(assignees) if assignees else "unassigned",
                        url=raw["url"],
                        created_at=raw["createdAt"][:10],
                    )
                )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to list open PRs for %s; skipping", repo)

        try:
            merged = github_source.list_merged_prs_since(client, repo, since)
            # Only worth a second API call if something actually merged.
            auto_merge_shas = _auto_merge_shas(client, repo, since) if merged else frozenset()
            for raw in merged:
                data.merged_prs.append(
                    MergedPR(
                        repo=repo,
                        number=raw["number"],
                        title=raw["title"],
                        author=(raw.get("author") or {}).get("login", "unknown"),
                        merged_by=(raw.get("mergedBy") or {}).get("login", "unknown"),
                        url=raw["url"],
                        merged_at=dt.datetime.fromisoformat(raw["mergedAt"].replace("Z", "+00:00")),
                        auto_merged=bool(raw.get("headRefOid"))
                        and raw["headRefOid"] in auto_merge_shas,
                    )
                )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to list merged PRs for %s; skipping", repo)

        if repo not in alert_skip_repos:
            try:
                for raw in github_source.list_open_alerts(client, repo):
                    dependency = raw.get("dependency", {}).get("package", {})
                    data.alerts.append(
                        Alert(
                            repo=repo,
                            number=raw["number"],
                            severity=_severity(raw),
                            ecosystem=str(dependency.get("ecosystem", "")),
                            package=str(dependency.get("name", "")),
                            advisory_id=_advisory_id(raw),
                            summary=str(raw.get("security_advisory", {}).get("summary", "")),
                            url=raw["html_url"],
                            created_at=raw["created_at"][:10],
                        )
                    )
            except github_source.AlertsDisabledError:
                data.alerts_disabled_repos.append(repo)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Failed to list Dependabot alerts for %s; skipping", repo)

    return data
