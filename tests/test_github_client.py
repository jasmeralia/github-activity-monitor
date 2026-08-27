from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx

from git_activity_monitor.github_client import AlertsDisabledError, GitHubClient

_API = "https://api.github.com"


@pytest.fixture
def gh() -> GitHubClient:
    return GitHubClient(token="test-token")


def _paginated(items: list[object]) -> list[httpx.Response]:
    """Return items on first page, empty list on second (stops pagination)."""
    return [
        httpx.Response(200, json=items),
        httpx.Response(200, json=[]),
    ]


@respx.mock
def test_get_repo_stats(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo").mock(
        return_value=httpx.Response(200, json={"stargazers_count": 42, "subscribers_count": 7})
    )
    stats = gh.get_repo_stats("owner", "repo")
    assert stats == {"stars": 42, "watches": 7, "archived": False}


@respx.mock
def test_get_repo_stats_includes_archived(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo").mock(
        return_value=httpx.Response(
            200,
            json={"stargazers_count": 1, "subscribers_count": 1, "archived": True},
        )
    )
    stats = gh.get_repo_stats("owner", "repo")
    assert stats["archived"] is True


@respx.mock
def test_get_new_pulls_filters_by_number(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/pulls").mock(
        side_effect=_paginated(
            [
                {"number": 10, "title": "Old", "html_url": "...", "user": {"login": "a"}},
                {"number": 11, "title": "New", "html_url": "...", "user": {"login": "b"}},
                {"number": 12, "title": "Newer", "html_url": "...", "user": {"login": "c"}},
            ]
        )
    )
    pulls = gh.get_new_pulls("owner", "repo", since_number=10)
    assert [p["number"] for p in pulls] == [11, 12]


@respx.mock
def test_get_new_issues_excludes_pull_requests(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/issues").mock(
        side_effect=_paginated(
            [
                {
                    "number": 5,
                    "title": "Real issue",
                    "html_url": "...",
                    "user": {"login": "a"},
                    "labels": [],
                },
                {
                    "number": 6,
                    "title": "A PR",
                    "html_url": "...",
                    "user": {"login": "b"},
                    "labels": [],
                    "pull_request": {},
                },
            ]
        )
    )
    issues = gh.get_new_issues("owner", "repo", since_number=4)
    assert len(issues) == 1
    assert issues[0]["number"] == 5


@respx.mock
def test_get_new_releases_stops_at_known_id(gh: GitHubClient) -> None:
    # Releases don't use _paginate's empty-page stop; they stop at since_id
    respx.get(f"{_API}/repos/owner/repo/releases").mock(
        side_effect=_paginated(
            [
                {
                    "id": 300,
                    "tag_name": "v3.0",
                    "name": "v3.0",
                    "html_url": "...",
                    "body": "",
                    "draft": False,
                },
                {
                    "id": 200,
                    "tag_name": "v2.0",
                    "name": "v2.0",
                    "html_url": "...",
                    "body": "",
                    "draft": False,
                },
                {
                    "id": 100,
                    "tag_name": "v1.0",
                    "name": "v1.0",
                    "html_url": "...",
                    "body": "",
                    "draft": False,
                },
            ]
        )
    )
    releases = gh.get_new_releases("owner", "repo", since_id=200)
    assert len(releases) == 1
    assert releases[0]["id"] == 300


@respx.mock
def test_get_new_releases_skips_drafts(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/releases").mock(
        side_effect=_paginated(
            [
                {
                    "id": 300,
                    "tag_name": "v3.0",
                    "name": "v3.0",
                    "html_url": "...",
                    "body": "",
                    "draft": True,
                },
                {
                    "id": 200,
                    "tag_name": "v2.0",
                    "name": "v2.0",
                    "html_url": "...",
                    "body": "",
                    "draft": False,
                },
            ]
        )
    )
    releases = gh.get_new_releases("owner", "repo", since_id=100)
    assert len(releases) == 1
    assert releases[0]["id"] == 200


@respx.mock
def test_get_new_package_versions_user_endpoint(gh: GitHubClient) -> None:
    respx.get(f"{_API}/users/owner/packages/container/pkg/versions").mock(
        side_effect=_paginated(
            [
                {"metadata": {"container": {"tags": ["1.0.0", "latest"]}}},
                {"metadata": {"container": {"tags": ["0.9.0"]}}},
            ]
        )
    )
    new = gh.get_new_package_versions("owner", "pkg", seen_versions=["0.9.0"])
    assert set(new) == {"1.0.0", "latest"}


@respx.mock
def test_get_new_package_versions_org_fallback(gh: GitHubClient) -> None:
    respx.get(f"{_API}/users/org/packages/container/pkg/versions").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    respx.get(f"{_API}/orgs/org/packages/container/pkg/versions").mock(
        side_effect=_paginated([{"metadata": {"container": {"tags": ["2.0.0"]}}}])
    )
    new = gh.get_new_package_versions("org", "pkg", seen_versions=[])
    assert new == ["2.0.0"]


@respx.mock
def test_get_owner_repos_includes_private(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user/repos").mock(
        side_effect=_paginated(
            [
                {
                    "full_name": "alice/public-repo",
                    "fork": False,
                    "archived": False,
                    "owner": {"login": "alice"},
                },
                {
                    "full_name": "alice/private-repo",
                    "fork": False,
                    "archived": False,
                    "owner": {"login": "alice"},
                },
            ]
        )
    )
    repos = gh.get_owner_repos("alice")
    assert set(repos) == {"alice/public-repo", "alice/private-repo"}


@respx.mock
def test_get_owner_repos_filters_forks_and_archived(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user/repos").mock(
        side_effect=_paginated(
            [
                {
                    "full_name": "alice/good",
                    "fork": False,
                    "archived": False,
                    "owner": {"login": "alice"},
                },
                {
                    "full_name": "alice/forked",
                    "fork": True,
                    "archived": False,
                    "owner": {"login": "alice"},
                },
                {
                    "full_name": "alice/old",
                    "fork": False,
                    "archived": True,
                    "owner": {"login": "alice"},
                },
            ]
        )
    )
    repos = gh.get_owner_repos("alice")
    assert repos == ["alice/good"]


@respx.mock
def test_get_owner_repos_org_fallback(gh: GitHubClient) -> None:
    # Token belongs to a different user — /user/repos returns nothing matching myorg
    respx.get(f"{_API}/user/repos").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_API}/orgs/myorg/repos").mock(
        side_effect=_paginated(
            [
                {"full_name": "myorg/repo-a", "fork": False, "archived": False},
            ]
        )
    )
    repos = gh.get_owner_repos("myorg")
    assert repos == ["myorg/repo-a"]


@respx.mock
def test_get_owner_packages_user_endpoint(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user/packages").mock(
        side_effect=_paginated(
            [
                {"name": "my-app", "owner": {"login": "alice"}},
                {"name": "other-app", "owner": {"login": "alice"}},
            ]
        )
    )
    pkgs = gh.get_owner_packages("alice")
    assert set(pkgs) == {"alice/my-app", "alice/other-app"}


@respx.mock
def test_get_owner_packages_org_fallback(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user/packages").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_API}/orgs/myorg/packages").mock(side_effect=_paginated([{"name": "org-app"}]))
    pkgs = gh.get_owner_packages("myorg")
    assert pkgs == ["myorg/org-app"]


@respx.mock
def test_get_owner_packages_public_fallback(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user/packages").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_API}/orgs/alice/packages").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    respx.get(f"{_API}/users/alice/packages").mock(side_effect=_paginated([{"name": "pub-app"}]))
    pkgs = gh.get_owner_packages("alice")
    assert pkgs == ["alice/pub-app"]


@respx.mock
def test_get_owner_repos_metadata_user_endpoint(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user/repos").mock(
        side_effect=_paginated(
            [
                {
                    "full_name": "alice/public-repo",
                    "fork": False,
                    "archived": False,
                    "private": False,
                    "description": "A public tool",
                    "owner": {"login": "alice"},
                },
                {
                    "full_name": "alice/private-repo",
                    "fork": False,
                    "archived": False,
                    "private": True,
                    "description": "",
                    "owner": {"login": "alice"},
                },
                {
                    "full_name": "alice/forked",
                    "fork": True,
                    "archived": False,
                    "private": False,
                    "description": "Forked",
                    "owner": {"login": "alice"},
                },
            ]
        )
    )
    meta = gh.get_owner_repos_metadata("alice")
    assert len(meta) == 2
    public = next(m for m in meta if m["full_name"] == "alice/public-repo")
    private = next(m for m in meta if m["full_name"] == "alice/private-repo")
    assert public == {
        "full_name": "alice/public-repo",
        "private": False,
        "description": "A public tool",
    }
    assert private == {"full_name": "alice/private-repo", "private": True, "description": ""}


@respx.mock
def test_get_owner_repos_metadata_filters_archived(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user/repos").mock(
        side_effect=_paginated(
            [
                {
                    "full_name": "alice/active",
                    "fork": False,
                    "archived": False,
                    "private": False,
                    "description": "Active",
                    "owner": {"login": "alice"},
                },
                {
                    "full_name": "alice/archived",
                    "fork": False,
                    "archived": True,
                    "private": False,
                    "description": "Old",
                    "owner": {"login": "alice"},
                },
            ]
        )
    )
    meta = gh.get_owner_repos_metadata("alice")
    assert [m["full_name"] for m in meta] == ["alice/active"]


@respx.mock
def test_get_repo_stats_retries_on_500(gh: GitHubClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch tenacity sleep so this test doesn't wait 2 seconds for the retry backoff
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    respx.get(f"{_API}/repos/owner/repo").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json={"stargazers_count": 1, "subscribers_count": 1}),
        ]
    )
    stats = gh.get_repo_stats("owner", "repo")
    assert stats["stars"] == 1


@respx.mock
def test_get_dependabot_alerts_single_page(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/dependabot/alerts").mock(
        return_value=httpx.Response(200, json=[{"number": 1, "state": "open"}])
    )
    alerts = gh.get_dependabot_alerts("owner", "repo")
    assert [a["number"] for a in alerts] == [1]


@respx.mock
def test_get_dependabot_alerts_follows_link_header(gh: GitHubClient) -> None:
    next_url = f"{_API}/repos/owner/repo/dependabot/alerts?per_page=100&after=cursor123"
    # Both pages hit the same path, so they must be one route's side_effect list —
    # two separately-registered routes on the same path/query-less match would let
    # the first (page-1) mock keep matching the second request forever.
    respx.get(f"{_API}/repos/owner/repo/dependabot/alerts").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"number": 1, "state": "open"}],
                headers={"Link": f'<{next_url}>; rel="next"'},
            ),
            httpx.Response(200, json=[{"number": 2, "state": "open"}]),
        ]
    )
    alerts = gh.get_dependabot_alerts("owner", "repo")
    assert [a["number"] for a in alerts] == [1, 2]


@respx.mock
def test_get_dependabot_alerts_disabled_returns_empty(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/dependabot/alerts").mock(
        return_value=httpx.Response(403, json={"message": "Dependabot alerts are disabled"})
    )
    assert gh.get_dependabot_alerts("owner", "repo") == []


@respx.mock
def test_get_dependabot_alerts_page_param_rejected_is_not_used(gh: GitHubClient) -> None:
    route = respx.get(f"{_API}/repos/owner/repo/dependabot/alerts").mock(
        return_value=httpx.Response(200, json=[])
    )
    gh.get_dependabot_alerts("owner", "repo")
    assert "page" not in route.calls.last.request.url.params


@respx.mock
def test_get_authenticated_user(gh: GitHubClient) -> None:
    respx.get(f"{_API}/user").mock(return_value=httpx.Response(200, json={"login": "jasmeralia"}))
    assert gh.get_authenticated_user() == "jasmeralia"


@respx.mock
def test_list_open_pull_requests(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/pulls").mock(
        side_effect=_paginated([{"number": 1, "title": "A"}])
    )
    prs = gh.list_open_pull_requests("owner", "repo")
    assert [p["number"] for p in prs] == [1]


@respx.mock
def test_list_open_pull_requests_uses_state_open(gh: GitHubClient) -> None:
    route = respx.get(f"{_API}/repos/owner/repo/pulls").mock(
        side_effect=_paginated([])
    )
    gh.list_open_pull_requests("owner", "repo")
    assert route.calls.last.request.url.params["state"] == "open"


@respx.mock
def test_list_merged_pull_requests_since_filters_by_merged_at(gh: GitHubClient) -> None:
    since = dt.datetime(2026, 7, 27, tzinfo=dt.UTC)
    # Second page is empty -- both fixture items are still in-window by
    # updated_at, so this is what actually ends pagination, not the
    # early-exit tested separately below.
    respx.get(f"{_API}/repos/owner/repo/pulls").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "updated_at": "2026-07-28T00:00:00Z",
                        "merged_at": "2026-07-28T00:00:00Z",
                    },
                    {
                        # Updated in-window but never merged -- not a merge event.
                        "number": 2,
                        "updated_at": "2026-07-27T12:00:00Z",
                        "merged_at": None,
                    },
                ],
            ),
            httpx.Response(200, json=[]),
        ]
    )
    prs = gh.list_merged_pull_requests_since("owner", "repo", since)
    assert [p["number"] for p in prs] == [1]


@respx.mock
def test_list_merged_pull_requests_since_stops_paginating_once_stale(gh: GitHubClient) -> None:
    since = dt.datetime(2026, 7, 27, tzinfo=dt.UTC)
    route = respx.get(f"{_API}/repos/owner/repo/pulls").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "updated_at": "2026-07-28T00:00:00Z",
                        "merged_at": "2026-07-28T00:00:00Z",
                    }
                ],
            ),
            httpx.Response(
                200,
                json=[
                    {
                        # Older than `since` -- triggers the early-exit; page 3
                        # (if requested) would be a test failure below.
                        "number": 2,
                        "updated_at": "2026-07-26T00:00:00Z",
                        "merged_at": "2026-07-26T00:00:00Z",
                    }
                ],
            ),
        ]
    )
    prs = gh.list_merged_pull_requests_since("owner", "repo", since)
    assert [p["number"] for p in prs] == [1]
    assert route.call_count == 2


@respx.mock
def test_list_workflow_run_head_shas_collects_across_pages(gh: GitHubClient) -> None:
    route = respx.get(f"{_API}/repos/owner/repo/actions/workflows/wf.yml/runs").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"workflow_runs": [{"head_sha": "aaa"}, {"head_sha": "bbb"}]},
            ),
            httpx.Response(200, json={"workflow_runs": [{"head_sha": "ccc"}]}),
            httpx.Response(200, json={"workflow_runs": []}),
        ]
    )
    shas = gh.list_workflow_run_head_shas("owner", "repo", "wf.yml", dt.date(2026, 6, 27))
    assert shas == {"aaa", "bbb", "ccc"}
    assert route.call_count == 3
    first_params = route.calls[0].request.url.params
    assert first_params["status"] == "success"
    assert first_params["created"] == ">=2026-06-27"


@respx.mock
def test_list_workflow_run_head_shas_returns_empty_when_workflow_absent(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/actions/workflows/wf.yml/runs").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    assert gh.list_workflow_run_head_shas("owner", "repo", "wf.yml", dt.date(2026, 6, 27)) == set()


@respx.mock
def test_list_workflow_run_head_shas_reraises_other_errors(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/actions/workflows/wf.yml/runs").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        gh.list_workflow_run_head_shas("owner", "repo", "wf.yml", dt.date(2026, 6, 27))


@respx.mock
def test_list_open_dependabot_alerts_uses_state_open(gh: GitHubClient) -> None:
    route = respx.get(f"{_API}/repos/owner/repo/dependabot/alerts").mock(
        return_value=httpx.Response(200, json=[{"number": 1}])
    )
    alerts = gh.list_open_dependabot_alerts("owner", "repo")
    assert [a["number"] for a in alerts] == [1]
    assert route.calls.last.request.url.params["state"] == "open"


@respx.mock
def test_list_open_dependabot_alerts_disabled_raises(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/dependabot/alerts").mock(
        return_value=httpx.Response(403, json={"message": "Dependabot alerts are disabled"})
    )
    with pytest.raises(AlertsDisabledError):
        gh.list_open_dependabot_alerts("owner", "repo")


@respx.mock
def test_list_open_dependabot_alerts_other_error_propagates(gh: GitHubClient) -> None:
    respx.get(f"{_API}/repos/owner/repo/dependabot/alerts").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        gh.list_open_dependabot_alerts("owner", "repo")
