"""The guard: a Telegram client that can read and cannot write.

Telegram has no read-only credential. A session file authenticates as you, with
every power you have: send, delete, leave groups, mark chats as read, show you
online. "Read-only" is therefore a property of the code, and this module is
where that property lives.

Every request goes through ``TelegramClient.__call__``. ``GuardedClient``
overrides it and checks each request against an **allowlist**, before anything
reaches the network. An unknown request is refused rather than permitted: the
failure mode of a missing entry is a loud ``GuardViolation``; the failure mode
of a missing blocklist entry would be a message sent from your account.

What it protects against: bugs, a careless agent, a command you did not mean.
What it does not: a hostile process that can read the session file, because
that process can load the session into an unguarded client. Keep the session
file private (mode 600) and treat it as the account itself.
"""
from __future__ import annotations

from telethon import TelegramClient


class GuardViolation(RuntimeError):
    """Raised instead of sending a request that is not on the allowlist."""


# Name prefixes that are reads in the Telegram API. The few that are not are
# listed in DENIED below.
ALLOWED_PREFIXES = ("Get", "Resolve", "Search", "Check", "Ping")

# Protocol plumbing that Telethon issues itself.
ALLOWED_EXACT = frozenset({
    "ExportAuthorizationRequest",   # download media from another data centre
    "ImportAuthorizationRequest",
    "DestroySessionRequest",
})

# Wrappers that carry another request in ``.query``. The wrapper is allowed
# only if what it carries is: otherwise any write could be smuggled inside one.
WRAPPERS = frozenset({
    "InitConnectionRequest",
    "InvokeWithLayerRequest",
    "InvokeWithoutUpdatesRequest",
    "InvokeAfterMsgRequest",
    "InvokeAfterMsgsRequest",
    "InvokeWithMessagesRangeRequest",
    "InvokeWithTakeoutRequest",
})

# Reads by name, writes in effect.
DENIED = {
    "GetBotCallbackAnswerRequest": "presses an inline button: the bot sees it and acts",
    "GetInlineBotResultsRequest": "sends your query to a bot",
    "GetLocatedRequest": "can make you visible to people nearby",
    "GetTmpPasswordRequest": "creates a temporary payment password",
    "GetCallbackAnswerRequest": "presses an inline button on an ephemeral message",
    "GetStarGiftWithdrawalUrlRequest": "starts a withdrawal",
    "GetStarsRevenueWithdrawalUrlRequest": "starts a withdrawal of Stars revenue",
    "GetStarsRevenueAdsAccountUrlRequest": "opens the ads account linked to Stars revenue",
    "ResolveStarGiftOfferRequest": "accepts or declines a gift offer",
    "CheckRecoveryPasswordRequest": "part of the password recovery flow",
    "CheckPaidAuthRequest": "part of the login flow",
    "CheckUrlAuthMatchCodeRequest": "part of authorising a website login",
}

# Only the interactive login may send these (``allow_auth=True``).
AUTH_ONLY = frozenset({
    "SendCodeRequest",
    "ResendCodeRequest",
    "SignInRequest",
    "CheckPasswordRequest",         # starts with "Check": must be decided first
    "LogOutRequest",
    "ExportLoginTokenRequest",      # QR login
    "ImportLoginTokenRequest",      # QR login, token on another data centre
})


def name_allowed(name: str, allow_auth: bool = False, extra: frozenset = frozenset()) -> bool:
    """Whether a request class name passes the guard (wrappers excluded)."""
    if name in AUTH_ONLY:
        return allow_auth
    if name in extra:
        return True
    if name in DENIED:
        return False
    return name in ALLOWED_EXACT or name.startswith(ALLOWED_PREFIXES)


def check(request, allow_auth: bool = False, extra: frozenset = frozenset()) -> None:
    """Raise GuardViolation unless ``request`` (an instance) may be sent."""
    name = type(request).__name__
    if name in WRAPPERS:
        check(request.query, allow_auth, extra)
        return
    if name == "GetMessagesViewsRequest" and getattr(request, "increment", False):
        raise GuardViolation("blocked GetMessagesViewsRequest(increment=True): "
                             "it increments the view counters")
    if not name_allowed(name, allow_auth, extra):
        reason = DENIED.get(name, "not on the read allowlist")
        raise GuardViolation(f"blocked {type(request).__module__}.{name}: {reason}")


class GuardedClient(TelegramClient):
    """A TelegramClient that sends only allowlisted requests.

    ``allow_auth`` is set only by the interactive login. ``extra_allowed``
    names the writes a subclass permits on top of the reads.
    """

    allow_auth = False
    extra_allowed: frozenset = frozenset()

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        for req in (request if isinstance(request, (list, tuple)) else [request]):
            check(req, self.allow_auth, self.extra_allowed)
        return await super().__call__(
            request, ordered=ordered, flood_sleep_threshold=flood_sleep_threshold
        )


class ReadOnlyClient(GuardedClient):
    """Reads only. The other party never sees a read receipt, a typing
    indicator or you coming online because of it."""


class DraftOnlyClient(GuardedClient):
    """Reads plus ``messages.SaveDraft``: text in a chat's input box on your
    own devices. Sending stays blocked, so a draft becomes a message only when
    you press send."""

    extra_allowed = frozenset({"SaveDraftRequest"})
