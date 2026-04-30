from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_env_file() -> str:
    return os.getenv("ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_default_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: Literal["dev", "prod", "test"] = "dev"
    LOG_LEVEL: str = "INFO"

    CODEX_BIN: str = Field(default="/opt/homebrew/bin/codex")
    APP_ROOT_PATH: str = Field(default="~/.cic")
    CODEX_ALLOWED_CWD_ROOTS: str = "[]"
    CODEX_MODEL: str | None = None
    CODEX_DEFAULT_REASONING_EFFORT: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "high"
    CODEX_APPROVAL_POLICY: Literal["untrusted", "on-failure", "on-request", "never"] = "on-request"
    CODEX_RETRY_MAX_ATTEMPTS: int = 3
    CODEX_RETRY_INITIAL_DELAY_S: float = 0.5
    CODEX_RETRY_MAX_DELAY_S: float = 5.0
    CODEX_COMPACT_TIMEOUT_S: float = 300.0
    STREAM_PATCH_INTERVAL_S: float = 2.0

    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_REACTION_EMOJI: str = "OK"

    @property
    def app_root_path(self) -> Path:
        return Path(self.APP_ROOT_PATH).expanduser().resolve()

    @property
    def codex_cwd_path(self) -> Path:
        return self.app_root_path / "workspace"

    @property
    def allowed_cwd_roots(self) -> list[Path]:
        roots: list[Path] = []
        raw = self.CODEX_ALLOWED_CWD_ROOTS.strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("CODEX_ALLOWED_CWD_ROOTS must be a JSON array of paths") from exc
            if not isinstance(parsed, list):
                raise ValueError("CODEX_ALLOWED_CWD_ROOTS must be a JSON array of paths")
            for item in parsed:
                if not isinstance(item, str):
                    raise ValueError("CODEX_ALLOWED_CWD_ROOTS must contain only string paths")
                roots.append(Path(item).expanduser().resolve())
        roots.append(self.codex_cwd_path)
        deduped: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            if root in seen:
                continue
            seen.add(root)
            deduped.append(root)
        return deduped

    @property
    def sqlite_file_path(self) -> Path:
        return self.app_root_path / "data" / "codex_im_connector.db"

    @property
    def sqlite_url_resolved(self) -> str:
        return f"sqlite+aiosqlite:///{self.sqlite_file_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
