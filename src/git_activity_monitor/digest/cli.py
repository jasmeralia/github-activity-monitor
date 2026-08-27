"""Console-script entry point: git-activity-digest [owner].

Collects open PRs, PRs merged in the last N hours, and open Dependabot
alerts across an owner's repos and emails one HTML digest -- or sends
nothing at all if there's nothing to report, matching the old bash digest's
"quiet day, quiet inbox" behavior.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path

from git_activity_monitor.digest import github_source, mailer, render
from git_activity_monitor.digest.collect import collect_digest
from git_activity_monitor.github_client import GitHubClient

logger = logging.getLogger(__name__)

DEFAULT_RECIPIENT = "morgan@windsofstorm.net"


def _parse_repo_list(raw: str) -> frozenset[str]:
    """Comma- or whitespace-separated 'owner/repo' list, matching the
    SKIP_REPOS convention from the old list-open-alerts.sh script."""
    return frozenset(item for item in re.split(r"[,\s]+", raw.strip()) if item)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "owner",
        nargs="?",
        default=None,
        help="GitHub owner to scan (default: the authenticated gh user)",
    )
    parser.add_argument(
        "--recipient",
        default=DEFAULT_RECIPIENT,
        help=f"Email recipient (default: {DEFAULT_RECIPIENT})",
    )
    parser.add_argument(
        "--merged-window-hours",
        type=int,
        default=24,
        help="How far back to look for merged PRs (default: 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and render but do not send the email",
    )
    parser.add_argument(
        "--html-out",
        type=Path,
        default=None,
        help="Also write the rendered HTML body to this path",
    )
    parser.add_argument(
        "--alert-skip-repos",
        default=os.environ.get("SKIP_REPOS", ""),
        help=(
            "Comma/whitespace-separated 'owner/repo' list to exclude from "
            "Dependabot alert checks entirely (e.g. repos where alerts are "
            "intentionally left disabled). Defaults to the SKIP_REPOS env var."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required (git-activity-monitor's own .env has one)")

    with GitHubClient(token) as client:
        owner = args.owner or github_source.get_authenticated_user(client)
        data = collect_digest(
            client,
            owner,
            merged_window_hours=args.merged_window_hours,
            alert_skip_repos=_parse_repo_list(args.alert_skip_repos),
        )

        if args.html_out is not None:
            args.html_out.write_text(render.build_html(data), encoding="utf-8")
            logger.info("Wrote rendered HTML to %s", args.html_out)

        if data.is_empty():
            print(
                f"No open PRs, no PRs merged in the last {args.merged_window_hours}h, "
                "and no open alerts -- skipping email."
            )
            return 0

        subject = f"[{owner}] Git Activity Digest - {data.generated_at.strftime('%Y-%m-%d')}"

        if args.dry_run:
            print(render.build_text(data))
            return 0

        mailer.send_digest_email(
            subject=subject,
            html_body=render.build_html(data),
            text_body=render.build_text(data),
            recipient=args.recipient,
        )
        print(
            f"Sent digest: {data.open_pr_count} open PR(s), "
            f"{data.merged_pr_count} merged PR(s) in the last {args.merged_window_hours}h, "
            f"{data.alert_count} open alert(s)."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
