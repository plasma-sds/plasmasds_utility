import io

import paramiko
import pytest
from paramiko.sftp import CMD_EXTENDED

from plasmasds_utility import _config, _sftp


@pytest.fixture
def raw(sftp_server, tmp_path):
    """A plain paramiko SFTP client on the test server, and the server's root."""
    key = paramiko.PKey.from_path(str(sftp_server.key_file))
    client, sftp = _sftp._connect(_config.settings(), 10, key)
    yield sftp, tmp_path / "server"
    client.close()


def hardlink(sftp, old, new):
    sftp._request(CMD_EXTENDED, "hardlink@openssh.com", old, new)


# The test server behaves like OpenSSH's, as checked on the real server on 2026-10-11.


def test_server_rename_refuses_an_existing_target(raw):
    sftp, root = raw
    sftp.putfo(io.BytesIO(b"one"), "a")
    sftp.putfo(io.BytesIO(b"two"), "b")
    with pytest.raises(OSError):
        sftp.rename("b", "a")
    sftp.rename("b", "c")
    assert (root / "a").read_bytes() == b"one"
    assert (root / "c").read_bytes() == b"two"
    assert not (root / "b").exists()


def test_server_posix_rename_replaces_the_target(raw):
    sftp, root = raw
    sftp.putfo(io.BytesIO(b"one"), "a")
    sftp.putfo(io.BytesIO(b"two"), "b")
    sftp.posix_rename("b", "a")
    assert (root / "a").read_bytes() == b"two"
    assert not (root / "b").exists()


def test_server_hardlink_keeps_the_old_content_and_refuses_an_existing_name(raw):
    sftp, root = raw
    sftp.putfo(io.BytesIO(b"one"), "a")
    hardlink(sftp, "a", "old")
    with pytest.raises(OSError):
        hardlink(sftp, "a", "old")
    sftp.putfo(io.BytesIO(b"two"), "b")
    sftp.posix_rename("b", "a")
    assert (root / "old").read_bytes() == b"one"
    assert (root / "a").read_bytes() == b"two"


def test_server_records_modes_and_operations(raw, sftp_server):
    sftp, root = raw
    sftp.mkdir("d")
    sftp.putfo(io.BytesIO(b"x"), "d/f")
    sftp.chmod("d/f", 0o644)
    sftp.remove("d/f")
    assert sftp_server.modes == {"d/f": 0o644}
    assert sftp_server.operations == {"mkdir": 1, "write": 1, "chmod": 1, "remove": 1}
    assert list((root / "d").iterdir()) == []


def test_server_refuses_and_drops_on_request(raw, sftp_server):
    sftp, root = raw
    sftp_server.refused["hardlink"] = paramiko.SFTP_OP_UNSUPPORTED
    sftp.putfo(io.BytesIO(b"x"), "a")
    with pytest.raises(OSError, match="unsupported"):
        hardlink(sftp, "a", "b")
    sftp_server.drop.add("remove")
    with pytest.raises((OSError, EOFError, paramiko.SSHException)):
        sftp.remove("a")
    assert sftp_server.drop == set()
    assert (root / "a").exists()
