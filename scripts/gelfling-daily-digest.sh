#!/usr/bin/env bash
# Thin wrapper around the git-activity-digest console script (see
# src/git_activity_monitor/digest/), which collects open PRs, PRs merged in
# the last 24h, and open Dependabot alerts across an owner's repos and emails
# one HTML digest via the local MTA -- or sends nothing at all on a quiet day.
#
# Kept as a wrapper (rather than pointing cron straight at the console
# script) so the existing cron entry and the "resend today's digest"
# instructions in ~/git/rincity-infra/AGENTS.md ("Git Activity Digest")
# keep working unchanged.
#
# Usage: scripts/gelfling-daily-digest.sh [owner]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

exec "$REPO_ROOT/.venv/bin/git-activity-digest" "$@"
