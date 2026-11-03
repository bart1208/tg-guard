"""The guard is the product: these tests run offline and never touch Telegram."""
import asyncio
import importlib
import inspect
import pathlib
import pkgutil

import pytest
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions
from telethon.tl.functions import (
    InitConnectionRequest,
    InvokeWithLayerRequest,
    account,
    auth,
    contacts,
    messages,
)
from telethon.tl.tlobject import TLRequest
from telethon.tl.types import InputPeerEmpty, InputPeerSelf

from tg_guard.guard import (
    ALLOWED_EXACT,
    AUTH_ONLY,
    DENIED,
    WRAPPERS,
    DraftOnlyClient,
    GuardViolation,
    ReadOnlyClient,
    check,
    name_allowed,
)

SNAPSHOT = pathlib.Path(__file__).with_name("allowed_requests.txt")


def all_requests():
    """Every request class in the installed Telethon layer, as module.Name."""
    found = {}
    for info in pkgutil.walk_packages(functions.__path__, functions.__name__ + "."):
        mod = importlib.import_module(info.name)
        for name, cls in vars(mod).items():
            if inspect.isclass(cls) and issubclass(cls, TLRequest) and cls.__module__ == mod.__name__:
                found[f"{mod.__name__.removeprefix('telethon.tl.functions.')}.{name}"] = cls
    for name, cls in vars(functions).items():
        if inspect.isclass(cls) and issubclass(cls, TLRequest) and cls.__module__ == functions.__name__:
            found[name] = cls
    return found


# -- writes that must never pass -------------------------------------------

WRITES = [
    messages.SendMessageRequest(peer=InputPeerSelf(), message="hi"),
    messages.ReadHistoryRequest(peer=InputPeerSelf(), max_id=0),          # read receipts
    messages.SetTypingRequest(peer=InputPeerSelf(), action=None),          # "typing…"
    account.UpdateStatusRequest(offline=False),                            # online presence
    messages.DeleteMessagesRequest(id=[1]),
    messages.ForwardMessagesRequest(from_peer=InputPeerSelf(), id=[1], to_peer=InputPeerSelf()),
    messages.SaveDraftRequest(peer=InputPeerSelf(), message="draft"),
    messages.GetBotCallbackAnswerRequest(peer=InputPeerSelf(), msg_id=1),
    contacts.GetLocatedRequest(geo_point=None),
    messages.GetMessagesViewsRequest(peer=InputPeerSelf(), id=[1], increment=True),
]


@pytest.mark.parametrize("request_", WRITES, ids=lambda r: type(r).__name__)
def test_read_only_blocks_writes(request_):
    with pytest.raises(GuardViolation):
        check(request_)


@pytest.mark.parametrize("request_", [r for r in WRITES if type(r).__name__ != "SaveDraftRequest"],
                         ids=lambda r: type(r).__name__)
def test_draft_only_still_blocks_other_writes(request_):
    with pytest.raises(GuardViolation):
        check(request_, extra=DraftOnlyClient.extra_allowed)


def test_draft_only_allows_save_draft():
    check(messages.SaveDraftRequest(peer=InputPeerSelf(), message="draft"),
          extra=DraftOnlyClient.extra_allowed)


def test_reads_pass():
    check(messages.GetHistoryRequest(peer=InputPeerSelf(), offset_id=0, offset_date=None,
                                     add_offset=0, limit=10, max_id=0, min_id=0, hash=0))
    check(messages.GetDialogsRequest(offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
                                     limit=10, hash=0))
    check(messages.GetMessagesViewsRequest(peer=InputPeerSelf(), id=[1], increment=False))


# -- wrappers and auth -----------------------------------------------------

def test_write_inside_a_wrapper_is_blocked():
    smuggled = InvokeWithLayerRequest(layer=1, query=messages.SendMessageRequest(
        peer=InputPeerSelf(), message="hi"))
    with pytest.raises(GuardViolation):
        check(smuggled)
    nested = InvokeWithLayerRequest(layer=1, query=InitConnectionRequest(
        api_id=1, device_model="d", system_version="s", app_version="a", system_lang_code="en",
        lang_pack="", lang_code="en", query=account.UpdateStatusRequest(offline=False)))
    with pytest.raises(GuardViolation):
        check(nested)


def test_read_inside_a_wrapper_passes():
    check(InvokeWithLayerRequest(layer=1, query=account.GetPasswordRequest()))


def test_unlisted_wrappers_are_blocked():
    assert not name_allowed("InvokeWebViewCustomMethodRequest")
    assert not name_allowed("InvokeWithBusinessConnectionRequest")


def test_auth_only_needs_allow_auth():
    req = auth.CheckPasswordRequest(password=None)       # starts with "Check"
    with pytest.raises(GuardViolation):
        check(req)
    check(req, allow_auth=True)
    with pytest.raises(GuardViolation):
        check(auth.SendCodeRequest(phone_number="+1", api_id=1, api_hash="x", settings=None))


# -- the client actually refuses before the network --------------------------

def test_client_call_refuses_before_sending(monkeypatch):
    sent = []

    async def fake_call(self, request, ordered=False, flood_sleep_threshold=None):
        sent.append(request)

    monkeypatch.setattr(TelegramClient, "__call__", fake_call)
    client = ReadOnlyClient(StringSession(), 1, "x")
    read = account.GetPasswordRequest()
    asyncio.run(client(read))
    assert sent == [read]
    with pytest.raises(GuardViolation):
        asyncio.run(client([read, messages.SendMessageRequest(peer=InputPeerSelf(), message="hi")]))
    assert sent == [read], "a batch with one write must send nothing"


# -- keeping the lists honest ----------------------------------------------

def test_named_requests_exist():
    names = {key.rsplit(".", 1)[-1] for key in all_requests()}
    for listed in ALLOWED_EXACT | AUTH_ONLY | WRAPPERS | set(DENIED) | DraftOnlyClient.extra_allowed:
        assert listed in names, f"{listed} is not in this Telethon layer: stale entry"


def test_allowlist_snapshot():
    """Fails when a Telethon upgrade adds or removes an allowed request.

    A new layer can bring a new Get* request with side effects. When this fails,
    read the new names in the diff, add any with side effects to DENIED, then
    regenerate: python -m tests.test_guard > tests/allowed_requests.txt
    """
    current = sorted(k for k, cls in all_requests().items() if name_allowed(cls.__name__))
    expected = SNAPSHOT.read_text().split()
    added, removed = sorted(set(current) - set(expected)), sorted(set(expected) - set(current))
    assert not added and not removed, f"review the allowlist: added {added}, removed {removed}"


if __name__ == "__main__":
    for key, cls in sorted(all_requests().items()):
        if name_allowed(cls.__name__):
            print(key)
