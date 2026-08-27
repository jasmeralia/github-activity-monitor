from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_RATE_LIMIT_WARN_THRESHOLD = 50


class AlertsDisabledError(Exception):
    """Dependabot alerts (or the dependency graph) aren't enabled for a repo."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        # GitHub uses 403 for secondary rate limits; distinguish by Retry-After header
        return code in {429, 500, 502, 503, 504} or (
            code == 403 and "Retry-After" in exc.response.headers
        )
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def _filter_repos(items: list[dict[str, Any]]) -> list[str]:
    return [
        item["full_name"]
        for item in items
        if not item.get("fork", False) and not item.get("archived", False)
    ]


def _filter_repos_metadata(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "full_name": item["full_name"],
            "private": bool(item.get("private", False)),
            "description": (item.get("description") or "").strip(),
        }
        for item in items
        if not item.get("fork", False) and not item.get("archived", False)
    ]


class GitHubClient:
    _BASE = "https://api.github.com"

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=self._BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get_response(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        resp = self._client.get(path, params=params)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) < _RATE_LIMIT_WARN_THRESHOLD:
            logger.warning("GitHub rate limit low: %s remaining", remaining)
        resp.raise_for_status()
        return resp

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._get_response(path, params=params).json()

    def _paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            p: dict[str, Any] = {**(params or {}), "per_page": 100, "page": page}
            items: list[dict[str, Any]] = self._get(path, params=p)
            if not items:
                break
            yield from items
            page += 1

    def _paginate_by_link(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Paginate endpoints that only support Link-header cursors, not ?page=N.

        (e.g. /repos/{owner}/{repo}/dependabot/alerts rejects ?page= with a 400.)
        """
        next_url: str | None = path
        next_params: dict[str, Any] | None = {**(params or {}), "per_page": 100}
        while next_url:
            resp = self._get_response(next_url, params=next_params)
            items: list[dict[str, Any]] = resp.json()
            yield from items
            next_link = resp.links.get("next")
            next_url = next_link["url"] if next_link else None
            next_params = None

    def get_repo_stats(self, owner: str, repo: str) -> dict[str, Any]:
        data = self._get(f"/repos/{owner}/{repo}")
        return {
            "stars": data["stargazers_count"],
            "watches": data["subscribers_count"],
            "archived": bool(data.get("archived", False)),
        }

    def get_new_pulls(self, owner: str, repo: str, since_number: int) -> list[dict[str, Any]]:
        results = []
        for pr in self._paginate(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "all", "sort": "created", "direction": "asc"},
        ):
            if pr["number"] > since_number:
                results.append(pr)
        return results

    def get_new_issues(self, owner: str, repo: str, since_number: int) -> list[dict[str, Any]]:
        results = []
        for item in self._paginate(
            f"/repos/{owner}/{repo}/issues",
            params={"state": "all", "sort": "created", "direction": "asc"},
        ):
            if "pull_request" in item:
                continue
            if item["number"] > since_number:
                results.append(item)
        return results

    def get_new_releases(self, owner: str, repo: str, since_id: int) -> list[dict[str, Any]]:
        results = []
        for release in self._paginate(f"/repos/{owner}/{repo}/releases"):
            if release["id"] <= since_id:
                break
            if not release.get("draft", False):
                results.append(release)
        return results

    def get_new_package_versions(
        self, owner: str, package_name: str, seen_versions: list[str]
    ) -> list[str]:
        versions = self._fetch_package_versions(owner, package_name)
        seen = set(seen_versions)
        return [v for v in versions if v not in seen]

    def get_dependabot_alerts(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """Return all Dependabot alerts (any state) for a repo, or [] if alerts aren't enabled."""
        try:
            return list(self._paginate_by_link(f"/repos/{owner}/{repo}/dependabot/alerts"))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                return []
            raise

    def list_open_dependabot_alerts(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """Return open Dependabot alerts for a repo.

        Unlike get_dependabot_alerts (which treats a disabled dependency graph
        as "no alerts", used by the Discord monitor), this raises
        AlertsDisabledError so callers that need to report "disabled" as its
        own state -- distinct from "zero alerts" -- can do so.
        """
        try:
            return list(
                self._paginate_by_link(
                    f"/repos/{owner}/{repo}/dependabot/alerts", params={"state": "open"}
                )
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise AlertsDisabledError(f"{owner}/{repo}") from exc
            raise

    def get_authenticated_user(self) -> str:
        return str(self._get("/user")["login"])

    def list_open_pull_requests(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """Raw open-PR dicts (REST /pulls shape: user, created_at, html_url, ...)."""
        return list(
            self._paginate(f"/repos/{owner}/{repo}/pulls", params={"state": "open"})
        )

    def list_merged_pull_requests_since(
        self, owner: str, repo: str, since: dt.datetime
    ) -> list[dict[str, Any]]:
        """Raw merged-PR dicts merged at or after `since` (UTC).

        Paginates closed PRs sorted by most-recently-updated first, so a
        merge (which always bumps updated_at) merged at/after `since` is
        guaranteed to appear before we hit a page that's entirely older --
        that's the early-exit condition below.
        """
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            items: list[dict[str, Any]] = self._get(
                f"/repos/{owner}/{repo}/pulls",
                params={
                    "state": "closed",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                    "page": page,
                },
            )
            if not items:
                break
            stop = False
            for item in items:
                updated_at = dt.datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
                if updated_at < since:
                    stop = True
                    break
                merged_at = item.get("merged_at")
                if merged_at and dt.datetime.fromisoformat(
                    merged_at.replace("Z", "+00:00")
                ) >= since:
                    results.append(item)
            if stop:
                break
            page += 1
        return results

    def list_workflow_run_head_shas(
        self, owner: str, repo: str, workflow_file: str, created_since: dt.date
    ) -> set[str]:
        """Head SHAs of successful runs of `workflow_file` created at/after `created_since`.

        Returns an empty set if the repo has no such workflow (404).
        """
        shas: set[str] = set()
        page = 1
        while True:
            try:
                data = self._get(
                    f"/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs",
                    params={
                        "status": "success",
                        "created": f">={created_since.isoformat()}",
                        "per_page": 100,
                        "page": page,
                    },
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return set()
                raise
            runs = data.get("workflow_runs", [])
            if not runs:
                break
            shas.update(run["head_sha"] for run in runs if run.get("head_sha"))
            page += 1
        return shas

    def get_owner_repos(self, owner: str) -> list[str]:
        """Return 'owner/repo' strings for all non-fork, non-archived repos under owner.

        Tries the authenticated /user/repos endpoint first so private repositories
        are included when the token belongs to owner. Falls back to the org endpoint
        (for org accounts), then to the public-only /users/{owner}/repos endpoint.
        """
        # Authenticated endpoint returns private repos when token belongs to owner.
        # Filter by owner.login so this is a no-op when the token belongs to someone else.
        user_items = [
            r
            for r in self._paginate("/user/repos", params={"affiliation": "owner"})
            if r.get("owner", {}).get("login") == owner
        ]
        if user_items:
            return _filter_repos(user_items)

        # Org endpoint: covers org accounts and org-owned private repos.
        try:
            return _filter_repos(
                list(self._paginate(f"/orgs/{owner}/repos", params={"type": "all"}))
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

        # Public-only fallback for users the token does not own.
        return _filter_repos(
            list(self._paginate(f"/users/{owner}/repos", params={"type": "owner"}))
        )

    def get_owner_repos_metadata(self, owner: str) -> list[dict[str, Any]]:
        """Return metadata dicts for all non-fork, non-archived repos under owner.

        Each dict contains: full_name, private, description.
        Uses the same three-tier strategy as get_owner_repos.
        """
        user_items = [
            r
            for r in self._paginate("/user/repos", params={"affiliation": "owner"})
            if r.get("owner", {}).get("login") == owner
        ]
        if user_items:
            return _filter_repos_metadata(user_items)

        try:
            return _filter_repos_metadata(
                list(self._paginate(f"/orgs/{owner}/repos", params={"type": "all"}))
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

        return _filter_repos_metadata(
            list(self._paginate(f"/users/{owner}/repos", params={"type": "owner"}))
        )

    def get_owner_packages(self, owner: str) -> list[str]:
        """Return 'owner/package' strings for all container packages under owner.

        Uses the same three-tier strategy as get_owner_repos: authenticated
        /user/packages first (includes private), then org endpoint, then public fallback.
        """
        user_items = [
            p
            for p in self._paginate("/user/packages", params={"package_type": "container"})
            if p.get("owner", {}).get("login") == owner
        ]
        if user_items:
            return [f"{owner}/{p['name']}" for p in user_items]

        try:
            org_items = list(
                self._paginate(f"/orgs/{owner}/packages", params={"package_type": "container"})
            )
            return [f"{owner}/{p['name']}" for p in org_items]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

        pub_items = list(
            self._paginate(f"/users/{owner}/packages", params={"package_type": "container"})
        )
        return [f"{owner}/{p['name']}" for p in pub_items]

    def _fetch_package_versions(self, owner: str, package_name: str) -> list[str]:
        try:
            versions = list(
                self._paginate(f"/users/{owner}/packages/container/{package_name}/versions")
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            versions = list(
                self._paginate(f"/orgs/{owner}/packages/container/{package_name}/versions")
            )
        tags: list[str] = []
        for version in versions:
            tags.extend(version.get("metadata", {}).get("container", {}).get("tags", []))
        return tags
