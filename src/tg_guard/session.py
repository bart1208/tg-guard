"""Connecting on demand, and turning a chat id or name into one dialog."""
from __future__ import annotations

import contextlib
import fcntl
import pathlib
import sys

from telethon import utils

from . import __version__
from .config import Settings


def credentials(cfg: Settings) -> tuple[int, str]:
    if not cfg.api_id or not cfg.api_hash:
        sys.exit(f"TG_API_ID / TG_API_HASH not set: get them at https://my.telegram.org "
                 f"and put them in {cfg.home / '.env'}")
    return int(cfg.api_id), cfg.api_hash


def make_client(cls, cfg: Settings, session_path: str, device: str, system: str):
    api_id, api_hash = credentials(cfg)
    return cls(session_path, api_id, api_hash, device_model=device, system_version=system,
               app_version=f"tg-guard {__version__}", flood_sleep_threshold=60)


@contextlib.asynccontextmanager
async def open_client(cls, cfg: Settings, session_path: str, device: str, system: str,
                      login_hint: str = "tg-guard login"):
    """A connected, authorised client; the session file is held exclusively.

    Telethon keeps the session in SQLite: two processes on one file lock each
    other out or corrupt it, so a second run waits for the first.
    """
    if not pathlib.Path(session_path + ".session").exists():
        sys.exit(f"no session at {session_path}.session: run `{login_hint}` once")
    with open(session_path + ".lock", "w") as lock:  # noqa: ASYNC230 (an empty lock file)
        fcntl.flock(lock, fcntl.LOCK_EX)
        client = make_client(cls, cfg, session_path, device, system)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                sys.exit(f"{session_path}.session is not authorised: run `{login_hint}`")
            yield client
        finally:
            await client.disconnect()


def dialog_id(dialog) -> int:
    return utils.get_peer_id(dialog.entity)


async def resolve_chat(client, query):
    """The one dialog matching a chat id, "me", or a unique part of its name."""
    if str(query).lower() in ("me", "saved"):
        query = str((await client.get_me()).id)
    dialogs = await client.get_dialogs()
    if str(query).lstrip("-").isdigit():
        found = [d for d in dialogs if dialog_id(d) == int(query)]
    else:
        q = str(query).casefold()
        found = [d for d in dialogs if q in (d.name or "").casefold()]
    if not found:
        sys.exit(f"no chat matches {query!r}")
    if len(found) > 1:
        listing = "\n".join(f"  {dialog_id(d)}  {d.name}" for d in found)
        sys.exit(f"{query!r} matches {len(found)} chats; pass the chat id:\n{listing}")
    return found[0]
