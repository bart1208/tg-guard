"""tg-guard: read your Telegram from the command line. Reads only.

    tg-guard chats [--query alice]                list chats: id, type, last message, name
    tg-guard read 123456789 --since 2026-10-28    messages of one chat, oldest first
    tg-guard search "invoice" [--chat …]          server-side search, all history
    tg-guard download 123456789 4242              one file -> downloads/<chat>/<msg>.<ext>
    tg-guard transcribe 123456789 4242 [4243 …]   voice/audio -> text, locally
    tg-guard transcribe --file note.ogg           a local audio file, no Telegram
    tg-guard draft --chat 123456789 --text "…"    an unsent draft; you press send
    tg-guard login [--qr] [--drafts]              one-time interactive login

A chat is its id, "me" (Saved Messages) or a unique part of its name; names
repeat, so prefer the id from `chats`. --json prints one JSON object per line.

Nothing runs in the background and nothing is archived: each command connects,
answers and disconnects. Downloads are deleted after TG_DOWNLOAD_DAYS (default
7) by the next run. Transcription runs on this machine; the audio never leaves
it. Secret Chats are end-to-end encrypted and device-local: never visible here.
Settings and files: $TG_GUARD_HOME (default ~/.local/share/tg-guard).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import sys
import time

from telethon import utils
from telethon.tl.types import Channel, Chat, User

from . import __version__, draft, login
from .config import Settings, settings
from .guard import ReadOnlyClient
from .session import dialog_id, open_client, resolve_chat
from .transcribe import transcribe

AUDIO_KINDS = ("voice", "audio", "video")


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

def chat_type(entity) -> str:
    if isinstance(entity, User):
        return "bot" if entity.bot else "user"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        return "group" if entity.megagroup else "channel"
    return "unknown"


def media_kind(msg) -> str | None:
    if msg.voice is not None:
        return "voice"
    if msg.audio is not None:
        return "audio"
    if msg.photo is not None:
        return "photo"
    if msg.sticker is not None:
        return "sticker"
    if msg.video is not None:
        return "video"
    if msg.document is not None:
        return "image" if (msg.document.mime_type or "").startswith("image/") else "document"
    if msg.media is not None:
        return type(msg.media).__name__.replace("MessageMedia", "").lower()
    return None


def local_time(date) -> str:
    return date.astimezone().strftime("%Y-%m-%d %H:%M")


def message_record(msg, chat: str | None = None) -> dict:
    sender = msg.sender
    text = msg.message or ""
    if not text and msg.action is not None:
        text = f"[{type(msg.action).__name__.replace('MessageAction', '')}]"
    rec = {
        "chat_id": utils.get_peer_id(msg.peer_id),
        "msg_id": msg.id,
        "ts": int(msg.date.timestamp()),
        "time": local_time(msg.date),
        "from_me": bool(msg.out),
        "sender": utils.get_display_name(sender) if sender is not None else None,
        "media": media_kind(msg),
        "text": text,
    }
    if chat is not None:
        rec["chat"] = chat
    if msg.file is not None and rec["media"] not in (None, "photo"):
        rec["file_name"], rec["file_size"] = msg.file.name, msg.file.size
    if msg.fwd_from is not None:
        rec["forwarded"] = True
    if msg.reply_to_msg_id:
        rec["reply_to"] = msg.reply_to_msg_id
    return rec


def format_record(rec: dict) -> str:
    who = "me" if rec["from_me"] else (rec["sender"] or "?")
    where = f"[{rec['chat']}] " if "chat" in rec else ""
    media = ""
    if rec["media"]:
        media = f"<{rec['media']}"
        if rec.get("file_name"):
            media += f" {rec['file_name']}"
        if rec.get("file_size"):
            media += f" {rec['file_size']:,}B"
        media += "> "
    fwd = "(fwd) " if rec.get("forwarded") else ""
    return f"{rec['msg_id']:>8}  {rec['time']}  {where}{who}: {fwd}{media}{rec['text']}"


def print_record(rec: dict, as_json: bool) -> None:
    print(json.dumps(rec, ensure_ascii=False) if as_json else format_record(rec))


def parse_day(value: str) -> dt.datetime:
    """YYYY-MM-DD in local time -> aware datetime at midnight."""
    return dt.datetime.strptime(value, "%Y-%m-%d").astimezone()


def purge_downloads(folder: pathlib.Path, days: int, now: float | None = None) -> None:
    cutoff = (now or time.time()) - days * 86400
    for path in folder.glob("*/*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
    for sub in folder.glob("*"):
        if sub.is_dir() and not any(sub.iterdir()):
            sub.rmdir()


# --------------------------------------------------------------------------
# commands (all on ReadOnlyClient)
# --------------------------------------------------------------------------

async def cmd_chats(client, args, cfg):
    q = (args.query or "").casefold()
    shown = 0
    async for d in client.iter_dialogs():
        if q and q not in (d.name or "").casefold():
            continue
        rec = {"chat_id": dialog_id(d), "type": chat_type(d.entity), "name": d.name,
               "last": local_time(d.date) if d.date else None, "unread": d.unread_count}
        if args.json:
            print(json.dumps(rec, ensure_ascii=False))
        else:
            unread = f" ({rec['unread']} unread)" if rec["unread"] else ""
            print(f"{rec['chat_id']:>15}  {rec['type']:<7}  {rec['last'] or '':<16}  {rec['name']}{unread}")
        shown += 1
        if args.limit and shown >= args.limit:
            break


async def cmd_read(client, args, cfg):
    dialog = await resolve_chat(client, args.chat)
    since = parse_day(args.since) if args.since else None
    until = parse_day(args.until) + dt.timedelta(days=1) if args.until else None
    msgs = []
    # newest first from `until`, stop past `since`; printed oldest first
    async for msg in client.iter_messages(dialog.entity, offset_date=until, limit=None):
        if since and msg.date < since:
            break
        msgs.append(msg)
        if args.limit and len(msgs) >= args.limit:
            break
    if not args.json:
        print(f"# {dialog.name} ({dialog_id(dialog)}): {len(msgs)} messages")
    for msg in reversed(msgs):
        print_record(message_record(msg), args.json)


async def cmd_search(client, args, cfg):
    entity = (await resolve_chat(client, args.chat)).entity if args.chat else None
    async for msg in client.iter_messages(entity, search=args.query, limit=args.limit):
        chat = None
        if entity is None:
            c = msg.chat
            chat = utils.get_display_name(c) if c is not None else str(utils.get_peer_id(msg.peer_id))
        print_record(message_record(msg, chat), args.json)


async def fetch(client, dialog, msg_id: int, folder: pathlib.Path, max_mb: int):
    """Download the file of one message (once) -> (path, message)."""
    msg = await client.get_messages(dialog.entity, ids=msg_id)
    if msg is None:
        sys.exit(f"message {msg_id} not found in {dialog.name}")
    if msg.media is None or msg.file is None:
        sys.exit(f"message {msg_id} in {dialog.name} has no file")
    size = msg.file.size or 0
    if size > max_mb * 1024 * 1024:
        sys.exit(f"{size:,} bytes is over --max-mb {max_mb}: not downloaded")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    ext = msg.file.ext or utils.get_extension(msg.media) or ""
    target = folder / f"{msg.id}{ext}"
    if not target.exists():
        part = target.with_name(target.name + ".part")
        try:
            await client.download_media(msg, file=str(part))
            part.chmod(0o600)
            part.rename(target)
        finally:
            part.unlink(missing_ok=True)
    return target, msg


async def cmd_download(client, args, cfg):
    dialog = await resolve_chat(client, args.chat)
    folder = pathlib.Path(args.out) if args.out else cfg.downloads / str(dialog_id(dialog))
    target, msg = await fetch(client, dialog, args.msg, folder, args.max_mb)
    print(f"{target}\t{media_kind(msg)}\t{msg.file.name or '-'}\t{target.stat().st_size:,}B")


async def cmd_transcribe(client, args, cfg):
    """Download only; Whisper runs after the client has disconnected."""
    dialog = await resolve_chat(client, args.chat)
    folder = cfg.downloads / str(dialog_id(dialog))
    for msg_id in args.msgs:
        target, msg = await fetch(client, dialog, msg_id, folder, args.max_mb)
        if media_kind(msg) not in AUDIO_KINDS:
            sys.exit(f"message {msg_id} is {media_kind(msg)}, not audio")
        sender = "me" if msg.out else (utils.get_display_name(msg.sender) if msg.sender else "?")
        args.jobs.append((f"{msg_id}  {local_time(msg.date)}  {sender}", target))


async def run_read_only(args, cfg: Settings):
    cfg.downloads.mkdir(parents=True, exist_ok=True, mode=0o700)
    purge_downloads(cfg.downloads, cfg.download_days)
    async with open_client(ReadOnlyClient, cfg, cfg.read_session, cfg.read_device,
                           "read-only") as client:
        await args.func(client, args, cfg)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tg-guard", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"tg-guard {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    chat_help = 'chat id, "me", or a unique part of the chat name'

    c = sub.add_parser("chats", help="list chats, most recent first")
    c.add_argument("--query", help="only chats whose name contains this")
    c.add_argument("--limit", type=int, default=50, help="at most N chats (default 50)")
    c.add_argument("--json", action="store_true", help="one JSON object per line")
    c.set_defaults(func=cmd_chats)

    r = sub.add_parser("read", help="messages of one chat, oldest first")
    r.add_argument("chat", help=chat_help)
    r.add_argument("--since", help="YYYY-MM-DD, local time, inclusive")
    r.add_argument("--until", help="YYYY-MM-DD, local time, inclusive")
    r.add_argument("--limit", type=int,
                   help="at most N messages, the newest (default 50 without --since)")
    r.add_argument("--json", action="store_true", help="one JSON object per line")
    r.set_defaults(func=cmd_read)

    s = sub.add_parser("search", help="search on Telegram's servers, all history")
    s.add_argument("query", help="text to search for")
    s.add_argument("--chat", help=f"only this chat ({chat_help}); default all chats")
    s.add_argument("--limit", type=int, default=30, help="at most N results (default 30)")
    s.add_argument("--json", action="store_true", help="one JSON object per line")
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("download", help="download the file of one message")
    d.add_argument("chat", help=chat_help)
    d.add_argument("msg", type=int, help="message id, as `read` prints it")
    d.add_argument("--out", help="directory (default $TG_GUARD_HOME/downloads/<chat id>)")
    d.add_argument("--max-mb", type=int, default=50, help="refuse files larger than this (default 50)")
    d.set_defaults(func=cmd_download)

    t = sub.add_parser("transcribe", help="transcribe voice/audio messages locally")
    t.add_argument("chat", nargs="?", help=chat_help)
    t.add_argument("msgs", type=int, nargs="*", metavar="msg", help="message ids")
    t.add_argument("--file", action="append", default=[],
                   help="a local audio file instead of a message (repeatable)")
    t.add_argument("--language", help="it, es, en …; default: detected per file")
    t.add_argument("--max-mb", type=int, default=50, help="refuse files larger than this (default 50)")
    t.add_argument("--json", action="store_true", help="one JSON object per line")
    t.set_defaults(func=cmd_transcribe)

    dr = sub.add_parser("draft", help="save an unsent draft in a chat (separate login)")
    dr.add_argument("--chat", required=True, help=chat_help)
    dr.add_argument("--text", help="message text; read from stdin when omitted")
    dr.add_argument("--replace", action="store_true", help="overwrite an existing draft")
    dr.add_argument("--show", action="store_true", help="print the current draft and exit")

    lg = sub.add_parser("login", help="one-time interactive login")
    lg.add_argument("--qr", action="store_true", help="scan a QR code instead of receiving a code")
    lg.add_argument("--drafts", action="store_true", help="log in the separate drafts session")
    lg.add_argument("--phone", help="skip the prompt (ends up in your shell history)")
    return p


def main(argv: list[str] | None = None) -> None:
    p = build_parser()
    args = p.parse_args(argv)
    cfg = settings()

    if args.command == "draft":
        asyncio.run(draft.run(cfg, args.chat, args.text, args.replace, args.show))
        return
    if args.command == "login":
        try:
            asyncio.run(login.run(cfg, args.drafts, args.qr, args.phone))
        except KeyboardInterrupt:
            print("\naborted")
        return
    if args.command == "read" and not args.since and not args.limit:
        args.limit = 50
    if args.command == "transcribe":
        if args.file and (args.chat or args.msgs):
            p.error("transcribe: give either --file or CHAT MSG..., not both")
        if not args.file and not (args.chat and args.msgs):
            p.error("transcribe: give CHAT and at least one MSG, or --file")
        args.jobs = [(f, pathlib.Path(f)) for f in args.file]
        for _, path in args.jobs:
            if not path.is_file():
                p.error(f"no such file: {path}")
        if not args.file:
            asyncio.run(run_read_only(args, cfg))
        transcribe(args.jobs, cfg.whisper_model, cfg.models, args.language, args.json)
        return
    asyncio.run(run_read_only(args, cfg))


if __name__ == "__main__":
    main()
