"""Where tg-guard keeps its files, and the settings you can change.

Everything lives in one directory, ``TG_GUARD_HOME`` (default
``$XDG_DATA_HOME/tg-guard``, i.e. ``~/.local/share/tg-guard``). Settings come
from the environment, then from ``$TG_GUARD_HOME/.env``; the environment wins.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib


def home() -> pathlib.Path:
    if os.environ.get("TG_GUARD_HOME"):
        return pathlib.Path(os.environ["TG_GUARD_HOME"]).expanduser()
    data = os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share"
    return pathlib.Path(data) / "tg-guard"


def load_env(path: pathlib.Path | None = None) -> None:
    """Read KEY=VALUE lines into os.environ without overriding what is set."""
    path = path or home() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


@dataclasses.dataclass(frozen=True)
class Settings:
    home: pathlib.Path
    api_id: str | None
    api_hash: str | None
    read_session: str        # path without the ".session" suffix, as Telethon wants it
    draft_session: str
    read_device: str         # the name shown under Telegram -> Settings -> Devices
    draft_device: str
    downloads: pathlib.Path
    download_days: int
    drafts_log: pathlib.Path
    models: pathlib.Path
    whisper_model: str


def settings() -> Settings:
    load_env()
    h = home()
    env = os.environ.get
    return Settings(
        home=h,
        api_id=env("TG_API_ID"),
        api_hash=env("TG_API_HASH"),
        read_session=env("TG_SESSION") or str(h / "read"),
        draft_session=env("TG_DRAFT_SESSION") or str(h / "draft"),
        read_device=env("TG_READ_DEVICE") or "tg-guard",
        draft_device=env("TG_DRAFT_DEVICE") or "tg-guard-drafts",
        downloads=pathlib.Path(env("TG_DOWNLOADS") or h / "downloads"),
        download_days=int(env("TG_DOWNLOAD_DAYS") or 7),
        drafts_log=pathlib.Path(env("TG_DRAFTS_LOG") or h / "drafts.log"),
        models=pathlib.Path(env("TG_MODELS") or h / "models"),
        whisper_model=env("TG_WHISPER_MODEL") or "large-v3-turbo",
    )
