from __future__ import annotations

import argparse
import contextlib
import logging
import signal
import time
import types
from collections.abc import Callable

import httpx

from git_activity_monitor.config import Settings
from git_activity_monitor.discord_client import DiscordClient
from git_activity_monitor.github_client import GitHubClient
from git_activity_monitor.monitors import (
    ALL_MONITORS,
    MonitorFn,
    run_dependabot_alerts,
    run_ghcr,
    run_releases,
    run_releases_pinned,
)
from git_activity_monitor.state import StateStore

logger = logging.getLogger(__name__)


def _resolve_monitors(enabled_events: list[str]) -> list[MonitorFn]:
    """Return deduplicated standard monitor functions in the order they first appear.

    Events not in ALL_MONITORS (releases, ghcr) are dispatched explicitly in _run_cycle.
    """
    seen: set[Callable[..., None]] = set()
    result: list[MonitorFn] = []
    for event in enabled_events:
        fn = ALL_MONITORS.get(event)
        if fn is None:
            continue
        if fn not in seen:
            seen.add(fn)
            result.append(fn)
    return result


def _effective_repositories(
    settings: Settings, gh_client: GitHubClient
) -> tuple[list[str], list[str]]:
    """Merge owner-discovered repos with explicitly listed repos, preserving order.

    Returns (all_repos, public_repos). Owner-discovered repos carry privacy metadata;
    explicitly listed repos are assumed public.
    """
    seen: set[str] = set()
    repos: list[str] = []
    public_repos: list[str] = []
    for owner in settings.owners:
        try:
            metadata = gh_client.get_owner_repos_metadata(owner)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to list repos for owner %s; skipping", owner)
            metadata = []
        for item in metadata:
            repo = item["full_name"]
            if repo not in seen:
                seen.add(repo)
                repos.append(repo)
                if not item["private"]:
                    public_repos.append(repo)
        logger.debug("Owner %s: discovered %d repo(s)", owner, len(metadata))
    for repo in settings.repositories:
        if repo not in seen:
            seen.add(repo)
            repos.append(repo)
            public_repos.append(repo)
    return repos, public_repos


def _effective_ghcr_packages(settings: Settings, gh_client: GitHubClient) -> list[str]:
    """Merge owner-discovered container packages with explicitly listed packages."""
    seen: set[str] = set()
    packages: list[str] = []
    for owner in settings.owners:
        try:
            discovered = gh_client.get_owner_packages(owner)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to list packages for owner %s; skipping", owner)
            discovered = []
        for pkg in discovered:
            if pkg not in seen:
                seen.add(pkg)
                packages.append(pkg)
        logger.debug("Owner %s: discovered %d package(s)", owner, len(discovered))
    for pkg in settings.ghcr_packages:
        if pkg not in seen:
            seen.add(pkg)
            packages.append(pkg)
    return packages


def _run_cycle(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    settings: Settings,
    state_store: StateStore,
    gh_client: GitHubClient,
    discord_client: DiscordClient,
    monitor_fns: list[MonitorFn],
    releases_discord_client: DiscordClient | None = None,
    alerts_discord_client: DiscordClient | None = None,
) -> None:
    effective, public = _effective_repositories(settings, gh_client)
    if not effective:
        logger.warning("No repositories to monitor this cycle.")
        return
    effective_packages = (
        _effective_ghcr_packages(settings, gh_client)
        if "ghcr" in settings.enabled_events
        else settings.ghcr_packages
    )
    effective_settings = settings.model_copy(
        update={
            "repositories": effective,
            "ghcr_packages": effective_packages,
            "public_repositories": public,
        }
    )

    def _call(fn: Callable[..., None], *args: object, **kwargs: object) -> None:
        try:
            fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 404}:
                logger.critical(
                    "Monitor %s: permanent webhook/API failure (HTTP %d) — "
                    "verify DISCORD_WEBHOOK_URL and GITHUB_TOKEN are correct",
                    getattr(fn, "__name__", fn),
                    exc.response.status_code,
                )
            else:
                logger.exception(
                    "Monitor %s failed; continuing with next monitor",
                    getattr(fn, "__name__", fn),
                )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Monitor %s failed; continuing with next monitor",
                getattr(fn, "__name__", fn),
            )

    for fn in monitor_fns:
        _call(fn, effective_settings, state_store, gh_client, discord_client)

    if "releases" in settings.enabled_events:
        _call(
            run_releases,
            effective_settings,
            state_store,
            gh_client,
            discord_client,
            releases_discord_client=releases_discord_client,
        )

    if "ghcr" in settings.enabled_events:
        _call(
            run_ghcr,
            effective_settings,
            state_store,
            gh_client,
            discord_client,
            releases_discord_client=releases_discord_client,
        )

    if "alerts" in settings.enabled_events:
        _call(
            run_dependabot_alerts,
            effective_settings,
            state_store,
            gh_client,
            discord_client,
            alerts_discord_client=alerts_discord_client,
        )

    if releases_discord_client and settings.owners:
        _call(
            run_releases_pinned,
            effective_settings,
            state_store,
            gh_client,
            releases_discord_client,
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="git-activity-monitor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single monitoring cycle and exit instead of polling forever "
        "(same as setting RUN_ONCE=1).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = Settings()
    run_once = args.once or settings.run_once
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger.info("Starting GitHub activity monitor")

    state_store = StateStore(settings.state_file_path)
    state_store.load()

    monitor_fns = _resolve_monitors(settings.enabled_events)
    logger.info(
        "Enabled monitors: %s",
        ", ".join(fn.__module__.split(".")[-1] for fn in monitor_fns),
    )

    shutdown = False

    def _handle_signal(sig: int, _frame: types.FrameType | None) -> None:
        nonlocal shutdown
        logger.info("Received signal %d; shutting down after current cycle", sig)
        shutdown = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    releases_ctx: contextlib.AbstractContextManager[DiscordClient | None] = (
        DiscordClient(settings.discord_releases_webhook_url)
        if settings.discord_releases_webhook_url
        else contextlib.nullcontext()
    )
    alerts_ctx: contextlib.AbstractContextManager[DiscordClient | None] = (
        DiscordClient(settings.discord_security_webhook_url)
        if settings.discord_security_webhook_url
        else contextlib.nullcontext()
    )
    with (
        GitHubClient(settings.github_token) as gh_client,
        DiscordClient(settings.discord_webhook_url) as discord_client,
        releases_ctx as releases_discord_client,
        alerts_ctx as alerts_discord_client,
    ):
        if run_once:
            logger.info("RUN_ONCE set; running a single cycle and exiting")
            _run_cycle(
                settings,
                state_store,
                gh_client,
                discord_client,
                monitor_fns,
                releases_discord_client=releases_discord_client,
                alerts_discord_client=alerts_discord_client,
            )
        else:
            while not shutdown:
                cycle_start = time.monotonic()
                _run_cycle(
                    settings,
                    state_store,
                    gh_client,
                    discord_client,
                    monitor_fns,
                    releases_discord_client=releases_discord_client,
                    alerts_discord_client=alerts_discord_client,
                )
                elapsed = time.monotonic() - cycle_start
                sleep_for = int(max(0.0, settings.poll_interval_seconds - elapsed))
                logger.debug("Cycle complete in %.1fs; sleeping %ds", elapsed, sleep_for)
                for _ in range(sleep_for):
                    if shutdown:
                        break
                    time.sleep(1)

    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
