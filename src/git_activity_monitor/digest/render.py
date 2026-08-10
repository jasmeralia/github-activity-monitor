"""Render a DigestData into an HTML email body (plus a plain-text fallback)."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from git_activity_monitor.digest.models import DigestData

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def build_html(data: DigestData) -> str:
    template = _env.get_template("digest_email.html")
    return template.render(
        owner=data.owner,
        generated_at=data.generated_at,
        repos_checked=data.repos_checked,
        merged_window_hours=data.merged_window_hours,
        merged_pr_count=data.merged_pr_count,
        auto_merged_pr_count=data.auto_merged_pr_count,
        open_pr_count=data.open_pr_count,
        alert_count=data.alert_count,
        merged_prs=data.merged_prs_sorted(),
        open_prs_by_repo=data.open_prs_by_repo(),
        alerts_by_repo=data.alerts_by_repo(),
        alerts_disabled_repos=data.alerts_disabled_repos,
    )


def build_text(data: DigestData) -> str:
    """Plain-text fallback for non-HTML mail clients."""
    lines = [
        f"Git Activity Digest: {data.owner}",
        f"{data.generated_at.strftime('%Y-%m-%d %H:%M UTC')} "
        f"· {data.repos_checked} repo(s) checked",
        "",
        f"{data.open_pr_count} open PR(s) · "
        f"{data.merged_pr_count} merged in the last {data.merged_window_hours}h"
        + (f" ({data.auto_merged_pr_count} auto-merged)" if data.auto_merged_pr_count else "")
        + f" · {data.alert_count} open alert(s)",
        "",
    ]

    if data.open_prs:
        lines.append("=== Open Pull Requests ===")
        for repo, prs in data.open_prs_by_repo():
            lines.append(f"{repo} ({len(prs)} open PR{'s' if len(prs) != 1 else ''})")
            for open_pr in prs:
                lines.append(
                    f"  #{open_pr.number} by {open_pr.author}, assigned: {open_pr.assignees}, "
                    f"opened {open_pr.created_at}: {open_pr.title}"
                )
                lines.append(f"    {open_pr.url}")
        lines.append("")

    if data.merged_prs:
        lines.append(f"=== Merged in the last {data.merged_window_hours}h ===")
        for merged_pr in data.merged_prs_sorted():
            lines.append(
                f"{merged_pr.repo} #{merged_pr.number} created by {merged_pr.author}, "
                f"merged by {merged_pr.merged_by_label}: {merged_pr.title}"
            )
            lines.append(f"  {merged_pr.url}")
        lines.append("")

    if data.alerts:
        lines.append("=== Open Security Alerts ===")
        for repo, alerts in data.alerts_by_repo():
            lines.append(f"{repo} ({len(alerts)} open alert{'s' if len(alerts) != 1 else ''})")
            for a in alerts:
                lines.append(
                    f"  [{a.severity}] {a.package} ({a.ecosystem}) {a.advisory_id}, "
                    f"opened {a.created_at}: {a.summary}"
                )
                lines.append(f"    {a.url}")
        lines.append("")

    if data.alerts_disabled_repos:
        lines.append("Dependabot alerts not enabled (no visibility) on:")
        lines.extend(f"  - {repo}" for repo in data.alerts_disabled_repos)
        lines.append("")

    return "\n".join(lines)
