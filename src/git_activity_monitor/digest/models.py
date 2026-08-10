from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class OpenPR:
    repo: str
    number: int
    title: str
    author: str
    assignees: str  # joined logins, or "unassigned"
    url: str
    created_at: str  # YYYY-MM-DD


@dataclass
class MergedPR:  # pylint: disable=too-many-instance-attributes
    repo: str
    number: int
    title: str
    author: str
    merged_by: str
    url: str
    merged_at: dt.datetime
    # True when the Dependabot auto-merge workflow merged this PR. That
    # workflow uses a PAT belonging to the repo owner, so `merged_by` alone
    # cannot tell it apart from a merge the owner performed by hand.
    auto_merged: bool = False

    @property
    def merged_by_label(self) -> str:
        """`merged_by`, annotated when the merge was done by the workflow."""
        if self.auto_merged:
            return f"{self.merged_by} (auto-merge workflow)"
        return self.merged_by


@dataclass
class Alert:  # pylint: disable=too-many-instance-attributes
    repo: str
    number: int
    severity: str  # critical | high | moderate | low
    ecosystem: str
    package: str
    advisory_id: str  # GHSA id, falling back to CVE id, or ""
    summary: str
    url: str
    created_at: str  # YYYY-MM-DD


_SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3}


@dataclass
class DigestData:  # pylint: disable=too-many-instance-attributes
    owner: str
    generated_at: dt.datetime
    repos_checked: int
    open_prs: list[OpenPR] = field(default_factory=list)
    merged_prs: list[MergedPR] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    alerts_disabled_repos: list[str] = field(default_factory=list)
    merged_window_hours: int = 24

    @property
    def open_pr_count(self) -> int:
        return len(self.open_prs)

    @property
    def merged_pr_count(self) -> int:
        return len(self.merged_prs)

    @property
    def auto_merged_pr_count(self) -> int:
        return sum(1 for pr in self.merged_prs if pr.auto_merged)

    @property
    def alert_count(self) -> int:
        return len(self.alerts)

    @property
    def alerts_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self.alerts:
            counts[a.severity] = counts.get(a.severity, 0) + 1
        return counts

    def is_empty(self) -> bool:
        """True when there is nothing worth emailing about.

        Mirrors the old bash digest's behavior: a repo merely having
        Dependabot alerts disabled (a visibility gap, not activity) never by
        itself triggers a send.
        """
        return not (self.open_prs or self.merged_prs or self.alerts)

    def open_prs_by_repo(self) -> list[tuple[str, list[OpenPR]]]:
        order: list[str] = []
        grouped: dict[str, list[OpenPR]] = {}
        for pr in self.open_prs:
            grouped.setdefault(pr.repo, []).append(pr)
            if pr.repo not in order:
                order.append(pr.repo)
        return [(repo, grouped[repo]) for repo in order]

    def merged_prs_sorted(self) -> list[MergedPR]:
        return sorted(self.merged_prs, key=lambda pr: pr.merged_at, reverse=True)

    def alerts_by_repo(self) -> list[tuple[str, list[Alert]]]:
        order: list[str] = []
        grouped: dict[str, list[Alert]] = {}
        for alert in self.alerts:
            grouped.setdefault(alert.repo, []).append(alert)
            if alert.repo not in order:
                order.append(alert.repo)
        for alerts in grouped.values():
            alerts.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 99))
        return [(repo, grouped[repo]) for repo in order]
