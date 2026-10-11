"""Tests against the real data server.

``network``: public downloads over HTTPS. They run by default, so CI checks public
access on every push (#6). Offline, run
``pixi run test -m "not private and not network"``.

``private``: downloads and uploads over SFTP with a real key; deselected by default. Run
``PLASMASDS_TEST_SSH_KEY=/path/to/key pixi run test -m private``. The test home has no
``known_hosts``, so these also check the host key shipped with the package.
"""

import os
import time
import uuid
from pathlib import PurePosixPath

import paramiko
import pytest

import plasmasds_utility
from plasmasds_utility import DataClient, TransferError, _config, _https, _paths, _sftp

# renate's own access-test files, unchanged since 2018.
PUBLIC_KEY = "test_dataset/access_tests/public_test.txt"
PRIVATE_KEY = "test_dataset/access_tests/private_test.txt"

# Expanded now, before the test home replaces HOME.
SSH_KEY = os.path.expanduser(os.environ.get("PLASMASDS_TEST_SSH_KEY", ""))


@pytest.mark.network
def test_public_download_from_the_data_server(home):
    client = DataClient("renate-od", working_dir=home / "data")
    path = client.get(PUBLIC_KEY, private=False)  # no SSH involved
    assert path == client.local_path(PUBLIC_KEY, private=False)
    assert path.read_bytes() == b"Data access test file"
    assert path.stat().st_mtime == 1519422481  # Last-Modified: 2018-02-23 21:48:01 UTC
    # Up to date: the HEAD check leaves it alone.
    assert client.get(PUBLIC_KEY, private=False, update="if_newer") == path
    assert path.stat().st_mtime == 1519422481
    # Out of date (older and a different size): downloaded again.
    path.write_bytes(b"stale")
    os.utime(path, (1_000_000_000, 1_000_000_000))
    client.get(PUBLIC_KEY, private=False, update="if_newer")
    assert path.read_bytes() == b"Data access test file"
    assert path.stat().st_mtime == 1519422481


@pytest.mark.private
def test_private_download_from_the_data_server(home):
    if not SSH_KEY:
        pytest.skip("set PLASMASDS_TEST_SSH_KEY to a key for the private data server")
    plasmasds_utility.set_ssh_key(SSH_KEY)
    client = DataClient("renate-od", working_dir=home / "data")
    path = client.get(PRIVATE_KEY, private=True)
    assert path == client.local_path(PRIVATE_KEY, private=True)
    assert path.stat().st_size == 21
    assert time.gmtime(path.stat().st_mtime)[:3] == (2018, 2, 23)
    assert not client.local_path(PRIVATE_KEY, private=False).exists()
    server_mtime = path.stat().st_mtime
    # Up to date: the SFTP stat check leaves it alone.
    client.get(PRIVATE_KEY, private=True, update="if_newer")
    assert path.stat().st_mtime == server_mtime
    # Out of date: check_updates downloads it again.
    path.write_bytes(b"stale")
    os.utime(path, (1_000_000_000, 1_000_000_000))
    assert client.check_updates() == [path]
    assert path.stat().st_size == 21
    assert path.stat().st_mtime == server_mtime


@pytest.fixture
def server_files():
    """Unique names in the test folders on the server, removed afterwards.

    Yields a function giving the server path of a name in one tree. The test
    folders (``plasmasds_utility/test`` in both trees) were made for these tests.
    Removal uses its own session; anything it cannot remove is reported, for an
    admin to delete.
    """
    if not SSH_KEY:
        pytest.skip("set PLASMASDS_TEST_SSH_KEY to a key for the private data server")
    tag = uuid.uuid4().hex[:12]
    settings = _config.settings()
    yield lambda tree, name: PurePosixPath(
        settings[f"{tree}_root"], "plasmasds_utility", "test", f"{tag}-{name}"
    )
    key = paramiko.PKey.from_path(SSH_KEY)
    client, sftp = _sftp._connect(settings, 30, key)
    left = []
    try:
        for tree in ("private", "public"):
            folder = PurePosixPath(
                settings[f"{tree}_root"], "plasmasds_utility", "test"
            )
            for name in sftp.listdir(str(folder)):
                if tag in name:
                    try:
                        sftp.remove(f"{folder}/{name}")
                    except OSError:
                        left.append(f"{folder}/{name}")
    finally:
        client.close()
    assert not left, f"cannot remove test files from the server: {left}"


@pytest.mark.private
def test_upload_to_the_data_server(home, server_files):
    plasmasds_utility.set_ssh_key(SSH_KEY)
    client = DataClient("plasmasds_utility", working_dir=home / "data")
    settings = _config.settings()
    key = f"test/{server_files('private', 'data.txt').name}"
    old = f"test/{server_files('private', 'data.old.txt').name}"
    local = client.local_path(key)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"first")
    client.upload(key)
    remote = server_files("private", "data.txt")
    size, mtime = _sftp.stat(settings, remote)
    assert size == 5
    assert local.stat().st_mtime == mtime
    with pytest.raises(TransferError, match="already on the server"):
        client.upload(key)
    local.write_bytes(b"second")
    client.upload(key, archive_to=old)
    assert _sftp.stat(settings, remote)[0] == 6
    assert _sftp.stat(settings, server_files("private", "data.old.txt"))[0] == 5
    # Public: Apache serves the uploaded file (mode 0644, in a 0755 folder).
    public = client.local_path(key, private=False)
    public.parent.mkdir(parents=True)
    public.write_bytes(b"public")
    client.upload(key, private=False)
    size, _ = _https.head(_paths.public_url(settings, client.prefix, key))
    assert size == 6
