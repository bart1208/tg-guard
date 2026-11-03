"""Put a message in the input box of one of your chats, unsent.

The draft appears on all your devices (the chat list marks it "Draft"); the
other party sees nothing until you open the chat and press send. This runs on
DraftOnlyClient, a separate login that allows reads plus messages.SaveDraft and
refuses every other write, so it cannot send.
"""
from __future__ import annotations

import json
import sys
import time

from telethon.tl.functions.messages import SaveDraftRequest

from .config import Settings
from .guard import DraftOnlyClient
from .session import dialog_id, open_client, resolve_chat


def _draft_text(dialog) -> str:
    return (dialog.draft.text or "") if dialog.draft else ""


async def run(cfg: Settings, chat: str, text: str | None, replace: bool, show: bool) -> None:
    if not show:
        text = (text if text is not None else sys.stdin.read()).strip()
        if not text:
            sys.exit("empty text: nothing to draft")

    async with open_client(DraftOnlyClient, cfg, cfg.draft_session, cfg.draft_device,
                           "drafts-only", login_hint="tg-guard login --drafts") as client:
        dialog = await resolve_chat(client, chat)
        chat_id, name = dialog_id(dialog), dialog.name
        current = _draft_text(dialog)

        if show:
            print(f"{name} ({chat_id}): " + (repr(current) if current else "no draft"))
            return
        if current and current != text and not replace:
            sys.exit(f"{name} already has a draft ({len(current)} chars), maybe yours: "
                     f"{current[:80]!r}\nnot overwritten; pass --replace to overwrite it")

        await client(SaveDraftRequest(peer=dialog.input_entity, message=text))

        # Read it back: the draft is the deliverable, so confirm it landed.
        saved = _draft_text(await resolve_chat(client, str(chat_id)))
        if saved != text:
            sys.exit(f"draft not confirmed: Telegram reports {saved!r}")

    cfg.drafts_log.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.drafts_log, "a") as log:  # noqa: ASYNC230 (one short line, after disconnect)
        log.write(json.dumps({"ts": int(time.time()), "chat_id": chat_id, "chat": name,
                              "replaced": bool(current), "text": text},
                             ensure_ascii=False) + "\n")
    cfg.drafts_log.chmod(0o600)
    print(f"Draft saved in «{name}» ({len(text)} chars), not sent: "
          "open the chat on Telegram and press send.")
