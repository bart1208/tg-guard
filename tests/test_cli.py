import os
import time

import pytest

from tg_guard import cli, config


def test_parser_has_every_command():
    p = cli.build_parser()
    for argv in (["chats"], ["read", "123"], ["search", "x"], ["download", "123", "4"],
                 ["transcribe", "123", "4", "5"], ["transcribe", "--file", "a.ogg"],
                 ["draft", "--chat", "123", "--text", "hi"], ["login", "--qr"]):
        assert p.parse_args(argv).command == argv[0]


@pytest.mark.parametrize("argv", [["transcribe"], ["transcribe", "123"],
                                  ["transcribe", "123", "4", "--file", "a.ogg"]])
def test_transcribe_needs_one_source(argv, monkeypatch, tmp_path):
    monkeypatch.setenv("TG_GUARD_HOME", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(argv)


def test_purge_downloads(tmp_path):
    old = tmp_path / "123" / "1.oga"
    new = tmp_path / "123" / "2.oga"
    gone = tmp_path / "456" / "3.pdf"
    for f in (old, new, gone):
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(b"x")
    week_ago = time.time() - 8 * 86400
    os.utime(old, (week_ago, week_ago))
    os.utime(gone, (week_ago, week_ago))
    cli.purge_downloads(tmp_path, days=7)
    assert new.exists() and not old.exists()
    assert not gone.parent.exists(), "empty chat folders are removed"


def test_settings_from_env_file(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("TG_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("TG_GUARD_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("TG_API_ID=42\nTG_API_HASH='abc'\nTG_DOWNLOAD_DAYS=3\n")
    monkeypatch.setenv("TG_DOWNLOAD_DAYS", "9")          # the environment wins
    cfg = config.settings()
    assert (cfg.api_id, cfg.api_hash, cfg.download_days) == ("42", "abc", 9)
    assert cfg.read_session == str(tmp_path / "read")
    assert cfg.downloads == tmp_path / "downloads"
