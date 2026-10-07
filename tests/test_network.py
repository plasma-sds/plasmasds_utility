"""Tests against the real data server.

``network``: public downloads over HTTPS. They run by default, so CI checks public
access on every push (#6). Offline, run
``pixi run test -m "not private and not network"``.

``private``: downloads over SFTP with a real key; deselected by default. Run
``PLASMASDS_TEST_SSH_KEY=/path/to/key pixi run test -m private``. The test home has no
``known_hosts``, so these also check the host key shipped with the package.
"""

import os
import time

import pytest

import plasmasds_utility
from plasmasds_utility import DataClient

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
