"""Interactive one-time login. Creates the session file, nothing else.

Run it in your own terminal: it prompts for the phone number, the login code
and the 2FA password. ``--qr`` logs in by scanning a code with the Telegram app
instead, which sidesteps code delivery entirely.

The session file it writes is a full-power credential for your account: it is
created with mode 600; never copy it, never commit it.
"""
from __future__ import annotations

import asyncio
import getpass
import pathlib
import sys

from telethon import errors

from .config import Settings
from .guard import ReadOnlyClient
from .session import make_client

# Where Telegram sent the code: the thing that actually goes wrong. By default
# it goes to the Telegram app, not by SMS, into a service chat that is often
# archived and muted.
WHERE = {
    "SentCodeTypeApp":
        "in the Telegram app: a message from the official 'Telegram' service chat\n"
        "     (blue check, id 777000) on every device where you are logged in. That chat\n"
        "     is often in Archived Chats and muted: look there first.",
    "SentCodeTypeSms": "by SMS to the number you entered.",
    "SentCodeTypeCall": "by automated voice call.",
    "SentCodeTypeFlashCall": "as a flash call: the code is the last digits of the caller's number.",
    "SentCodeTypeMissedCall": "as a missed call: the code is the last digits of the caller's number.",
    "SentCodeTypeEmail": "to your recovery email address.",
    "SentCodeTypeFragmentSms": "via Fragment (fragment.com), for anonymous numbers.",
    "SentCodeTypeSetUpEmailRequired": "nowhere: Telegram wants you to set up email login in the app first.",
}


def _finish(me, session_path: str) -> None:
    print(f"\nLogged in as {me.first_name} (@{me.username}) id={me.id}")
    session_file = pathlib.Path(session_path + ".session")
    if session_file.exists():
        session_file.chmod(0o600)
        print(f"Session written to {session_file} (mode 600). Never copy or commit it.")


async def _qr_login(client, session_path: str) -> None:
    import qrcode

    qr = await client.qr_login()
    while True:
        img = qrcode.QRCode(border=1)
        img.add_data(qr.url)
        print("\n" + "=" * 60)
        img.print_ascii(invert=True)
        print("Telegram -> Settings -> Devices -> Link Desktop Device -> scan")
        print("=" * 60)
        try:
            await qr.wait(30)
            break
        except asyncio.TimeoutError:
            print("  (token expired, redrawing)")
            await qr.recreate()
        except errors.SessionPasswordNeededError:
            print("\nTwo-step verification is on. The password does not echo as you type.")
            await client.sign_in(password=getpass.getpass("2FA password: "))
            break
    _finish(await client.get_me(), session_path)


async def _code_login(client, session_path: str, phone: str | None) -> None:
    phone = phone or input("Phone, international format (e.g. +39...): ").strip()
    try:
        sent = await client.send_code_request(phone)
    except errors.FloodWaitError as exc:
        sys.exit(f"Telegram rate-limited the login: wait {exc.seconds}s "
                 f"({exc.seconds // 60} min). Do not retry in a loop.")
    except errors.PhoneNumberInvalidError:
        sys.exit("Telegram rejected that number. Check the country code.")

    def announce(sent_code):
        kind = type(sent_code.type).__name__
        print(f"\n  >> Code sent {WHERE.get(kind, f'via {kind}')}")
        if sent_code.next_type is not None:
            nxt = type(sent_code.next_type).__name__.replace("SentCodeType", "").upper()
            print(f"  >> Press ENTER on an empty prompt to resend via {nxt}.")
        print()

    announce(sent)
    # An empty line resends. It must happen in this process: Telethon escalates
    # to the next delivery method only while it holds the phone_code_hash.
    while True:
        code = input("Code (or ENTER to resend): ").strip()
        if code:
            break
        try:
            announce(await client.send_code_request(phone))
        except errors.FloodWaitError as exc:
            print(f"  !! rate-limited: wait {exc.seconds}s before resending")
    try:
        await client.sign_in(phone, code)
    except errors.SessionPasswordNeededError:
        print("\nTwo-step verification is on. The password does not echo as you type.")
        await client.sign_in(password=getpass.getpass("2FA password: "))
    except errors.PhoneCodeInvalidError:
        sys.exit("Wrong code. Run the login again to get a new one.")
    except errors.PhoneCodeExpiredError:
        sys.exit("That code expired. Run the login again to get a new one.")
    _finish(await client.get_me(), session_path)


async def run(cfg: Settings, drafts: bool, qr: bool, phone: str | None) -> None:
    # The drafts login is a second, separate device, so it can be terminated
    # without losing read access. Both logins run on ReadOnlyClient with only
    # the auth.* requests unlocked: logging in never needs SaveDraft.
    if drafts:
        session_path, device = cfg.draft_session, cfg.draft_device
    else:
        session_path, device = cfg.read_session, cfg.read_device
    pathlib.Path(session_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = make_client(ReadOnlyClient, cfg, session_path, device, "read-only")
    client.allow_auth = True          # only here
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Already logged in as {me.first_name} (@{me.username}) id={me.id}")
            return
        if qr:
            await _qr_login(client, session_path)
        else:
            await _code_login(client, session_path, phone)
    finally:
        await client.disconnect()
