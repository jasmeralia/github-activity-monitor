# GitHub Activity Monitor

A Dockerized polling service that watches GitHub repositories and posts activity updates to Discord.

```
GitHub API ──► polling loop ──► Discord webhook
                    │
                    └──► state.json (persisted across restarts)
```

## What It Monitors

| Event | Discord behavior |
|---|---|
| Stars / Watchers | Edits a pinned summary message; one notification per cycle |
| New Pull Requests | One batched message per cycle listing all new PRs |
| New Issues | One batched message per cycle listing all new issues |
| New Releases | One batched message per cycle listing all new releases |
| New GHCR versions | One batched message per cycle listing all new container image versions |
| Dependabot security alerts | One embed per new alert or state change (created/fixed/dismissed/reopened) |

## Prerequisites

- A GitHub personal access token (PAT)
- A Discord channel webhook URL
- Docker + Docker Compose (for deployment) or Python 3.12+ (for local dev)

---

## Getting a GitHub Token

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Click **Generate new token**
3. Set a descriptive name and expiration
4. Under **Repository access**, select the repositories you want to monitor
5. Under **Permissions**, grant:
   - **Contents** → Read-only (for releases)
   - **Issues** → Read-only
   - **Pull requests** → Read-only
   - **Metadata** → Read-only (required, auto-selected)
6. Under **Account permissions**, grant:
   - **Packages** → Read-only (for GHCR monitoring)
7. Click **Generate token** and copy the value immediately

> **Note:** Classic tokens also work. Required scopes: `repo` (read) and `read:packages`.

---

## Getting a Discord Webhook URL

1. Open your Discord server and navigate to the channel where you want notifications
2. Click the gear icon (⚙) → **Integrations** → **Webhooks**
3. Click **New Webhook**, give it a name and optionally an avatar
4. Click **Copy Webhook URL**

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | yes | — | GitHub PAT with repo read + read:packages |
| `DISCORD_WEBHOOK_URL` | yes | — | Discord webhook URL |
| `DISCORD_PINNED_MESSAGE_ID` | no | — | ID of the pinned star/watch summary message (see below) |
| `OWNERS` | one of | — | Comma-separated GitHub users/orgs; monitors all their non-fork, non-archived repos |
| `REPOSITORIES` | one of | — | Comma-separated `owner/repo` pairs to monitor explicitly |
| `GHCR_PACKAGES` | no | — | Comma-separated `owner/package` pairs for GHCR monitoring |
| `DISCORD_SECURITY_WEBHOOK_URL` | no | — | Second webhook for Dependabot security alerts; falls back to `DISCORD_WEBHOOK_URL` if unset |
| `ENABLED_EVENTS` | no | all | Comma-separated subset of: `stars,watches,prs,issues,releases,ghcr,alerts` |
| `POLL_INTERVAL_SECONDS` | no | `300` | How often to poll (seconds; minimum 30) |
| `STATE_FILE_PATH` | no | `/data/state.json` | Path to the persistence file |
| `LOG_LEVEL` | no | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

At least one of `OWNERS` or `REPOSITORIES` must be set. Both can be used together — repos are deduplicated.

---

## Owner-Based Monitoring

Set `OWNERS` to a comma-separated list of GitHub usernames or organization names. Each polling cycle the service calls the GitHub API to discover all non-fork, non-archived repositories under each owner and monitors them automatically. No manual `REPOSITORIES` list is needed.

```ini
# Monitor everything under a user or org
OWNERS=jasmeralia

# Mix owners and explicit repos (duplicates are ignored)
OWNERS=jasmeralia
REPOSITORIES=some-org/a-specific-repo
```

If a new repository is created under a monitored owner, it is picked up on the next polling cycle without a restart.

---

## Quick Start — Docker Compose

```bash
# 1. Copy and fill in your .env file
cp .env.example .env
$EDITOR .env

# 2. Create the data directory
mkdir -p data

# 3. Start
docker compose up -d

# 4. Tail logs
docker compose logs -f
```

---

## Quick Start — Local Development

```bash
make setup          # create .venv and install deps
make test           # run tests

# Run locally (reads .env from current directory)
STATE_FILE_PATH=./data/state.json .venv/bin/git-activity-monitor
```

---

## Pinned Star/Watch Summary Message

On the first run with `stars` or `watches` monitoring enabled, the service sends a new Discord message containing the current star/watch counts for all configured repositories. The message ID is printed prominently in the logs:

```
INFO: Pinned summary message created. Set DISCORD_PINNED_MESSAGE_ID=1234567890123456789 in .env
```

Set `DISCORD_PINNED_MESSAGE_ID=<that id>` in your `.env` file and restart. On subsequent runs the service will **edit** that message in place rather than posting a new one each cycle.

If the pinned message is deleted, the service will automatically create a new one and log the new ID.

---

## Event Types

### Stars / Watches

Monitors repository star and watcher counts. When counts change, the pinned summary message is updated. Stars and watches are always handled together in one API call regardless of whether you enable one or both.

Example pinned message:
```
**GitHub Repository Stats** — last updated 5 minutes ago

**owner/my-app**  Stars: 142 (+3)  Watchers: 8
**owner/other**   Stars: 3  Watchers: 1
```

### Pull Requests

Detects PRs created since the last poll (open or closed/merged — anything opened in the interval is reported). All new PRs across all configured repositories are batched into a single Discord message per polling cycle.

```
**New Pull Requests**

**owner/my-app**
• [#74 — Add dark mode](https://github.com/owner/my-app/pull/74) by `alice`
• [#75 — Fix null pointer](https://github.com/owner/my-app/pull/75) by `bob`
```

### Issues

Detects issues created since the last poll (open or closed — anything created in the interval is reported). Pull requests are excluded.

### Releases

Detects new GitHub releases. Draft releases are ignored. Release body text is included (truncated to 200 characters).

### GHCR Package Versions

Detects new container image versions in the GitHub Container Registry. Requires `GHCR_PACKAGES` to be configured.

### Dependabot Security Alerts

Polls each configured repo's Dependabot alerts (`GET /repos/{owner}/{repo}/dependabot/alerts`) each cycle and diffs against last-seen state, posting one Discord embed per new alert or state change (`created`, `fixed`, `dismissed`, `auto_dismissed`, `reopened`). Color-coded by severity for new/reopened alerts, green for fixed, gray for dismissed (with reason, if given).

Repos where Dependabot alerts aren't enabled (dependency graph off, or alerts specifically disabled) are simply skipped — the GitHub API returns 403 for those, which is treated as "no alerts" rather than an error. Run `git-activity-digest <owner> --dry-run` to check which repos need alerts enabled (see "Maintenance Scripts" below) — it lists them separately rather than silently showing zero alerts.

Note: an earlier version of this feature tried to trigger per-repo via a GitHub Actions workflow on a `dependabot_alert` event. That event exists for repository webhooks but was never a valid Actions `on:` trigger, so it silently never fired — this polling-based approach replaced it entirely, with no per-repo workflow files or secrets required.

---

## State File

The state file (default `/data/state.json`) persists:
- Current star and watcher counts per repository
- Highest seen PR number per repository
- Highest seen issue number per repository
- Highest seen release ID per repository
- Set of seen GHCR version tags per package
- Pinned Discord message ID

**To reset all state:** delete the file and restart. The service will re-initialize from current GitHub state without sending notifications for existing activity.

**To reset one repository:** edit the JSON file and remove or zero out that repository's entry.

If the state file is corrupt or has an invalid schema on startup, it is renamed to `state.json.corrupt` and the service starts fresh (no notifications for existing activity).

---

## Maintenance Scripts

### `scripts/dependabot-merge.sh`

Merges (or auto-merges) open Dependabot PRs on a given repo:

```bash
scripts/dependabot-merge.sh <owner/repo>
```

Requires the [`gh` CLI](https://cli.github.com/) (authenticated) and `jq`. Each open Dependabot PR is checked and acted on individually — status is re-fetched immediately before acting on each PR, since merging one PR can change the check/rebase status of the next.

- **Private repos**: PRs with passing checks are squash-merged directly. If a PR needs rebasing against its base branch (e.g. because an earlier PR in the same run was just merged), it's left alone — a `@dependabot rebase` comment is posted instead, and it's reported as needing a follow-up run once Dependabot finishes rebasing.
- **Public repos**: PRs with passing checks have auto-merge (squash) enabled. Dependabot rebases those PRs itself if a later merge makes it necessary, so no manual rebase step is needed.
- **PRs with failing checks are never touched** — they're surfaced in the final summary as needing manual review, along with any PRs blocked by branch protection (e.g. missing required review) or still pending (checks running, mergeability not yet computed).

The script exits non-zero if any PR needs manual review, so it's safe to use in a monitoring/cron context.

### `git-activity-digest` (console script)

Collects open PRs, PRs merged in the last 24h, and open Dependabot alerts across all of an owner's repos and emails a single HTML digest (with a plain-text fallback part) via Gmail SMTP:

```bash
git-activity-digest [owner] [--recipient EMAIL] [--merged-window-hours N] [--alert-skip-repos LIST] [--dry-run] [--html-out PATH]
```

Requires the [`gh` CLI](https://cli.github.com/) (authenticated) for data — no `GITHUB_TOKEN` needed. Mail delivery relays through Gmail SMTP (`smtp.gmail.com:587`, STARTTLS) rather than the local MTA, using credentials from `~/.env` (`GMAIL_SMTP_HOST`/`GMAIL_SMTP_PORT`/`GMAIL_SMTP_USER`/`GMAIL_SMTP_PASS`/`GMAIL_SMTP_FROM`) — see rincity-infra's AGENTS.md ("Odoo Task Deadline Digest") for where those live; this switched from local `sendmail` direct-to-MX delivery because Gmail was flagging mail from the bare EC2 hostname identity as spam. If `owner` is omitted, defaults to the authenticated `gh` user. Only non-fork, non-archived repos owned directly by that owner are considered.

The email has a summary stat row up top (open PR / merged / open alert counts), followed by open PRs, recently merged PRs, and security alerts, each grouped by repo; Dependabot alerts are sorted by severity within a repo. Repos with Dependabot alerts disabled (dependency graph off, or alerts specifically disabled) are listed separately rather than silently showing zero alerts — unless excluded via `--alert-skip-repos` (comma/whitespace-separated `owner/repo` list, defaults to the `SKIP_REPOS` env var) for repos where alerts are intentionally left off by design and the "not enabled" callout would just be noise. Skipped repos are still scanned normally for open/merged PRs. **Sends nothing at all** on a day with zero merged PRs, zero open PRs, and zero open alerts.

Merged PRs are additionally marked as **auto-merged** when the repo's Dependabot auto-merge workflow was what merged them, rather than a hand-clicked merge — see "Detecting Dependabot auto-merges" below.

`--dry-run` prints the plain-text digest to stdout instead of sending mail (handy to check what's currently open, or which repos need Dependabot alerts enabled, without waiting for the next scheduled send). `--html-out PATH` additionally writes the rendered HTML body to a file, whether or not the email is actually sent.

#### Detecting Dependabot auto-merges

`.github/workflows/dependabot-auto-merge.yml` merges Dependabot PRs using a PAT belonging to the repo owner (a `GITHUB_TOKEN` merge would not trigger the release workflow). Because of that, GitHub records the owner as `mergedBy` on those PRs, making them indistinguishable from merges the owner performed by hand on that field alone.

The digest resolves this by matching each merged PR's head SHA against the head SHAs of **successful** runs of that workflow in the same repo (`list_auto_merge_shas` in `gh_cli.py`, one extra API call per repo that had any merges). A match means the workflow is what merged the PR, which covers both ways it can do so:

- **Queued auto-merge** — `gh pr merge --auto` enables auto-merge, and GitHub merges later once CI goes green. Leaves an `auto_squash_enabled` timeline event.
- **Immediate merge** — when nothing is pending (no required status checks), the same command merges right away. Leaves *no* `auto_*_enabled` event at all, which is why the timeline event on its own is not a usable signal.

A PR whose head SHA has no successful run is reported as a manual merge. That correctly covers PRs merged before the workflow existed, and PRs whose last push came from someone other than Dependabot — the workflow's `if: github.actor == 'dependabot[bot]'` guard skips those, so a human had to merge them.

If the workflow is absent from a repo (404) or the lookup fails, every merge there is reported as manual; the annotation is best-effort and never blocks the rest of the digest.

Implementation lives under `src/git_activity_monitor/digest/` (`gh_cli.py` for the `gh` subprocess calls, `collect.py` to aggregate across repos, `render.py`/`templates/digest_email.html` for the Jinja2-rendered email, `mailer.py` for Gmail SMTP delivery, `cli.py` for the entry point) — replaces the older `scripts/list-open-prs.sh` + `scripts/list-open-alerts.sh` + `scripts/gelfling-daily-digest.sh` trio of plain-text bash scripts with one Python codepath.

### `scripts/gelfling-daily-digest.sh`

Thin wrapper that `exec`s `.venv/bin/git-activity-digest "$@"`. Kept under this name/path so the existing cron entry and the "resend today's digest" instructions in `~/git/rincity-infra/AGENTS.md` ("Git Activity Digest") keep working unchanged:

```bash
scripts/gelfling-daily-digest.sh [owner]
```

Deployed as a daily cron job on `gelfling` — see that AGENTS.md section for the cron schedule and deployment path, since `gh` there is already installed and authenticated as `jasmeralia`.

---

## Development

```bash
make setup          # create .venv, install all deps
make lintfix       # auto-fix formatting (ruff format + ruff check --fix)
make lint           # full lint: ruff + mypy + pylint
make test           # pytest with coverage (minimum 80%)
make all-checks     # lint + shellcheck + hadolint + test
```

All code changes must pass `make lintfix && make lint && make test` before committing. See [AGENTS.md](AGENTS.md).

---

## Contributing

This project uses [Conventional Commits](https://www.conventionalcommits.org/). Merge tags are auto-created from commit messages on push to `master`:

- `feat: ...` → minor version bump
- `fix: ...`, `chore: ...`, `docs: ...`, etc. → patch bump
- `BREAKING CHANGE:` in footer → major bump

All pull requests must be squash-merged.

---

## License

MIT
