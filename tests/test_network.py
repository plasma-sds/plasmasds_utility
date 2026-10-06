"""Tests against the real public data server (marker ``network``).

They run by default, so CI checks public access on every push (#6). Offline, run
``pixi run test -m "not private and not network"``.
"""

import pytest

from plasmasds_utility import DataClient

# renate's own access-test file, unchanged since 2018.
KEY = "test_dataset/access_tests/public_test.txt"


@pytest.mark.network
def test_public_download_from_the_data_server(home):
    client = DataClient("renate-od", working_dir=home / "data")
    path = client.get(KEY)
    assert path == home / "data" / "public" / "test_dataset" / "access_tests" / (
        "public_test.txt"
    )
    assert path.read_bytes() == b"Data access test file"
    assert path.stat().st_mtime == 1519422481  # Last-Modified: 2018-02-23 21:48:01 UTC
