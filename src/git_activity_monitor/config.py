from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_VALID_EVENTS: frozenset[str] = frozenset(
    {"stars", "watches", "prs", "issues", "releases", "ghcr", "alerts"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="/app/.env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )

    github_token: str
    discord_webhook_url: str
    discord_pinned_message_id: str | None = None
    discord_releases_webhook_url: str | None = None
    discord_releases_pinned_message_id: str | None = None
    discord_security_webhook_url: str | None = None

    poll_interval_seconds: int = 300
    run_once: bool = False
    owners: list[str] = []
    repositories: list[str] = []
    ghcr_packages: list[str] = []
    enabled_events: list[str] = list(_VALID_EVENTS)
    # Runtime-only: populated by main.py via model_copy; not read from env in practice.
    public_repositories: list[str] = []

    state_file_path: Path = Path("/data/state.json")
    log_level: str = "INFO"

    @field_validator("owners", "repositories", "ghcr_packages", "enabled_events", mode="before")
    @classmethod
    def _split_comma(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("poll_interval_seconds", mode="after")
    @classmethod
    def _validate_poll_interval(cls, v: int) -> int:
        if v < 30:
            raise ValueError(f"POLL_INTERVAL_SECONDS must be at least 30, got {v}")
        return v

    @field_validator("repositories", mode="after")
    @classmethod
    def _validate_repo_format(cls, v: list[str]) -> list[str]:
        for repo in v:
            if repo.count("/") != 1:
                raise ValueError(f"Repository must be 'owner/repo', got: {repo!r}")
        return v

    @field_validator("ghcr_packages", mode="after")
    @classmethod
    def _validate_ghcr_package_format(cls, v: list[str]) -> list[str]:
        for pkg in v:
            if pkg.count("/") != 1:
                raise ValueError(f"GHCR package must be 'owner/package', got: {pkg!r}")
        return v

    @field_validator("enabled_events", mode="after")
    @classmethod
    def _validate_event_names(cls, v: list[str]) -> list[str]:
        unknown = set(v) - _VALID_EVENTS
        if unknown:
            raise ValueError(f"Unknown event types: {unknown!r}")
        return v

    @field_validator("log_level", mode="after")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        v = v.upper()
        if not hasattr(logging, v):
            raise ValueError(f"Invalid log level: {v!r}")
        return v

    @model_validator(mode="after")
    def _require_owners_or_repositories(self) -> Settings:
        if not self.owners and not self.repositories:
            raise ValueError("At least one of OWNERS or REPOSITORIES must be set.")
        return self

    @model_validator(mode="after")
    def _warn_ghcr_no_packages(self) -> Settings:
        if "ghcr" in self.enabled_events and not self.ghcr_packages and not self.owners:
            logging.getLogger(__name__).warning(
                "'ghcr' is in ENABLED_EVENTS but GHCR_PACKAGES is empty and OWNERS is not set; "
                "ghcr monitor will be a no-op."
            )
        return self

    @classmethod
    def settings_customise_sources(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        for src in (env_settings, dotenv_settings):
            _patch_comma_split(src)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)


def _patch_comma_split(src: PydanticBaseSettingsSource) -> None:
    # pydantic-settings v2 calls json.loads() on list fields before validators run,
    # so OWNERS=jasmeralia fails. Patch the source to return the raw string on JSON
    # failure so our _split_comma validator can handle comma-separated values.
    _orig = src.prepare_field_value

    def _prepare(
        field_name: str,
        field: FieldInfo,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        if (
            value is not None
            and isinstance(value, str)
            and field_name in {"owners", "repositories", "ghcr_packages", "enabled_events"}
        ):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value
        return _orig(field_name, field, value, value_is_complex)

    src.prepare_field_value = _prepare  # type: ignore[method-assign]
