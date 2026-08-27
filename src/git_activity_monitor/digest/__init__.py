"""Daily git activity digest: open PRs, PRs merged in the last 24h, and open
Dependabot alerts across an owner's repos, delivered as one HTML email.

Replaces the older scripts/list-open-prs.sh + scripts/list-open-alerts.sh +
scripts/gelfling-daily-digest.sh trio with a single console-script entry
point (git-activity-digest), built on the GitHub REST API via httpx
(github_source.py / github_client.py) rather than the `gh` CLI.
"""
