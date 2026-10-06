import os
import socket
import threading
import types
from pathlib import Path, PurePosixPath

import paramiko
import pytest
from conftest import write_config

from plasmasds_utility import AuthError, ConfigError, TransferError, _config, _sftp

REMOTE = PurePosixPath("private_html/renate-od/a/x.h5")
TIMESTAMP = 1519422481


@pytest.fixture
def sleeps(monkeypatch):
    """Record the backoff waits instead of sleeping."""
    calls = []
    monkeypatch.setattr(_sftp, "time", types.SimpleNamespace(sleep=calls.append))
    return calls


@pytest.fixture
def served(sftp_server, tmp_path):
    """Put REMOTE on the server, with a known content and mtime."""
    path = tmp_path / "server" / Path(*REMOTE.parts)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"private data")
    os.utime(path, (TIMESTAMP, TIMESTAMP))
    return sftp_server


def download(target, **kwargs):
    return _sftp.download(_config.settings(), REMOTE, target, **kwargs)


def leftovers(directory):
    return [p.name for p in directory.rglob("*.part")]


def test_download_writes_the_file_with_the_server_mtime(served, home, sleeps):
    target = home / "out" / "x.h5"
    assert download(target) == target
    assert target.read_bytes() == b"private data"
    assert target.stat().st_mtime == TIMESTAMP
    assert leftovers(home) == []
    assert sleeps == []


def test_session_is_reused(served, home):
    download(home / "one.h5")
    download(home / "two.h5")
    assert served.connections == 1


def test_large_file_is_streamed_intact(sftp_server, tmp_path, home):
    body = os.urandom(3 * 1024 * 1024 + 11)
    path = tmp_path / "server" / Path(*REMOTE.parts)
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    target = home / "x.h5"
    download(target)
    assert target.read_bytes() == body


def test_missing_file_is_not_retried(sftp_server, home, sleeps):
    with pytest.raises(TransferError, match="is not on the private server"):
        download(home / "x.h5")
    assert not (home / "x.h5").exists()
    assert sleeps == []


def test_missing_file_is_not_remembered(served, tmp_path, home):
    with pytest.raises(TransferError):
        _sftp.download(_config.settings(), REMOTE.with_name("y.h5"), home / "y.h5")
    download(home / "x.h5")  # the server is still usable


def test_denied_file_is_not_retried(served, home, sleeps):
    served.denied.add(str(REMOTE))
    with pytest.raises(TransferError, match="access to .* is denied"):
        download(home / "x.h5")
    assert sleeps == []


def test_truncated_transfer_is_retried_then_reported(served, home, sleeps):
    served.extra_size[str(REMOTE)] = 10
    target = home / "x.h5"
    target.write_bytes(b"previous")
    with pytest.raises(TransferError, match="cut short") as error:
        download(target)
    assert "after 3 attempts" in str(error.value)
    assert target.read_bytes() == b"previous"
    assert leftovers(home) == []
    assert sleeps == [1.0, 2.0]
    assert served.connections == 3  # a new session for each attempt


def test_dropped_connection_is_retried(served, home, sleeps):
    served.fail_connections = 1
    download(home / "x.h5")
    assert (home / "x.h5").read_bytes() == b"private data"
    assert served.connections == 2
    assert sleeps == [1.0]


def test_silent_server_times_out(home, sleeps):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    held = []
    threading.Thread(target=lambda: held.append(listener.accept()), daemon=True).start()
    write_config(host="127.0.0.1", port=listener.getsockname()[1])
    try:
        with pytest.raises(TransferError, match="after 2 attempts"):
            download(home / "x.h5", timeout=0.2, attempts=2)
    finally:
        listener.close()
    assert sleeps == [1.0]


def test_unknown_host_is_rejected(served, home, sleeps):
    write_config(
        host="127.0.0.1", port=served.port, ssh_key=str(served.key_file), host_keys=[]
    )
    with pytest.raises(AuthError, match="is not a known host"):
        download(home / "x.h5")
    assert not (home / "x.h5").exists()
    assert sleeps == []


def test_wrong_shipped_host_key_is_rejected(served, home, sleeps):
    other = paramiko.RSAKey.generate(1024)
    write_config(
        host="127.0.0.1",
        port=served.port,
        ssh_key=str(served.key_file),
        host_keys=[f"ssh-rsa {other.get_base64()}"],
    )
    with pytest.raises(AuthError, match="does not match"):
        download(home / "x.h5")
    assert sleeps == []


def test_known_hosts_entry_is_used(served, home, ssh_keys):
    host_key, _ = ssh_keys
    known_hosts = home / ".ssh" / "known_hosts"
    known_hosts.write_text(
        f"[127.0.0.1]:{served.port} ssh-rsa {host_key.get_base64()}\n", "utf-8"
    )
    write_config(
        host="127.0.0.1", port=served.port, ssh_key=str(served.key_file), host_keys=[]
    )
    download(home / "x.h5")
    assert (home / "x.h5").read_bytes() == b"private data"


def test_stale_known_hosts_entry_wins_over_the_shipped_key(served, home):
    other = paramiko.RSAKey.generate(1024)
    known_hosts = home / ".ssh" / "known_hosts"
    known_hosts.write_text(
        f"[127.0.0.1]:{served.port} ssh-rsa {other.get_base64()}\n", "utf-8"
    )
    with pytest.raises(AuthError, match=r"remove its line from ~/\.ssh/known_hosts"):
        download(home / "x.h5")


def test_rejected_key_is_reported_and_remembered(served, home, sleeps):
    other = paramiko.RSAKey.generate(1024)
    other_file = home / ".ssh" / "other_key"
    other.write_private_key_file(str(other_file))
    settings = _config.settings()
    write_config(
        host="127.0.0.1",
        port=served.port,
        host_keys=settings["host_keys"],
        ssh_key=str(other_file),
    )
    with pytest.raises(AuthError, match="SSH login as data@"):
        download(home / "x.h5")
    connections = served.connections
    with pytest.raises(AuthError, match="SSH login as data@"):
        download(home / "x.h5")
    assert served.connections == connections  # remembered: no new attempt
    assert sleeps == []


def test_no_key_at_all_fails_at_once(served, home, sleeps):
    write_config(
        host="127.0.0.1",
        port=served.port,
        host_keys=_config.settings()["host_keys"],
    )
    with pytest.raises(AuthError, match="no SSH key found"):
        download(home / "x.h5")
    assert served.connections == 1
    assert sleeps == []


def test_key_with_a_passphrase_is_not_prompted_for(served, home, sleeps):
    locked = home / ".ssh" / "locked_key"
    paramiko.RSAKey.generate(1024).write_private_key_file(str(locked), password="pw")
    write_config(
        host="127.0.0.1",
        port=served.port,
        host_keys=_config.settings()["host_keys"],
        ssh_key=str(locked),
    )
    with pytest.raises(AuthError, match="has a passphrase; load it into the SSH agent"):
        download(home / "x.h5")
    assert served.connections == 0
    assert sleeps == []


def test_key_with_a_passphrase_is_remembered(served, home):
    locked = home / ".ssh" / "locked_key"
    paramiko.RSAKey.generate(1024).write_private_key_file(str(locked), password="pw")
    host_keys = _config.settings()["host_keys"]
    write_config(
        host="127.0.0.1", port=served.port, host_keys=host_keys, ssh_key=str(locked)
    )
    with pytest.raises(AuthError):
        download(home / "x.h5")
    # A usable key now would work, but the failure is remembered for the process.
    write_config(
        host="127.0.0.1",
        port=served.port,
        host_keys=host_keys,
        ssh_key=str(served.key_file),
    )
    with pytest.raises(AuthError, match="has a passphrase"):
        download(home / "x.h5")


def test_remembered_failure_does_not_grow_its_traceback(served, home):
    write_config(
        host="127.0.0.1", port=served.port, host_keys=_config.settings()["host_keys"]
    )
    depths = []
    for _ in range(3):
        with pytest.raises(AuthError) as error:
            download(home / "x.h5")
        depths.append(len(error.traceback))
    assert depths[1] == depths[2]


def test_missing_configured_key_is_a_config_error(served, home):
    write_config(
        host="127.0.0.1",
        port=served.port,
        host_keys=_config.settings()["host_keys"],
        ssh_key=str(home / "gone"),
    )
    with pytest.raises(ConfigError, match="does not exist"):
        download(home / "x.h5")


@pytest.mark.parametrize("line", ["not a key", "ssh-rsa", "ssh-rsa !!!notbase64"])
def test_invalid_host_key_setting_is_a_config_error(served, home, line):
    write_config(
        host="127.0.0.1",
        port=served.port,
        ssh_key=str(served.key_file),
        host_keys=[line],
    )
    with pytest.raises(ConfigError, match="invalid entry in host_keys"):
        download(home / "x.h5")


def test_unreadable_key_file_is_a_config_error(served, home):
    bad = home / ".ssh" / "bad_key"
    bad.write_text("garbage", encoding="utf-8")
    write_config(
        host="127.0.0.1",
        port=served.port,
        host_keys=_config.settings()["host_keys"],
        ssh_key=str(bad),
    )
    with pytest.raises(ConfigError, match="cannot read .* as an SSH private key"):
        download(home / "x.h5")
    assert served.connections == 0


def test_close_all_forgets_sessions_and_failures(served, home):
    download(home / "x.h5")
    _sftp.close_all()
    download(home / "y.h5")
    assert served.connections == 2


def test_shipped_host_key_parses_for_a_non_standard_port():
    (line,) = _config._defaults()["host_keys"]
    name = _sftp._host_name("deep.reak.bme.hu", 72)
    assert name == "[deep.reak.bme.hu]:72"
    entry = paramiko.hostkeys.HostKeyEntry.from_line(f"{name} {line}")
    assert entry.key.get_name() == "ssh-ed25519"
    assert _sftp._host_name("example.org", 22) == "example.org"
