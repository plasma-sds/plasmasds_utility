import os

import pytest

import plasmasds_utility.client as client_module
from plasmasds_utility import DataClient, PathError, TransferError

OLD = 1_500_000_000


@pytest.fixture
def server(sftp_server, tmp_path):
    """The test server, and its directory."""
    return sftp_server, tmp_path / "server"


def place(path, body, mtime=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    os.utime(path, (mtime, mtime))


def test_upload_sends_the_private_copy_to_the_private_tree(server):
    stub, root = server
    client = DataClient("renate-od")
    local = client.local_path("a/b.h5")
    place(local, b"mine")
    assert client.upload("a/b.h5") is None
    remote = root / "private_html" / "renate-od" / "a" / "b.h5"
    assert remote.read_bytes() == b"mine"
    assert not (root / "public_html").exists()


def test_upload_sends_the_public_copy_to_the_public_tree(server):
    stub, root = server
    client = DataClient("renate-od")
    place(client.local_path("a.h5", private=False), b"open")
    client.upload("a.h5", private=False)
    assert (root / "public_html" / "renate-od" / "a.h5").read_bytes() == b"open"
    assert not (root / "private_html").exists()


def test_local_copy_gets_the_upload_time(server):
    stub, root = server
    client = DataClient("renate-od")
    local = client.local_path("a.h5")
    place(local, b"mine")
    client.upload("a.h5")
    remote = root / "private_html" / "renate-od" / "a.h5"
    assert local.stat().st_mtime == int(remote.stat().st_mtime)
    assert local.stat().st_mtime != OLD
    # The uploader's own copy now counts as up to date: no download is tried.
    stub.denied_open.add("private_html/renate-od/a.h5")
    assert client.get("a.h5", update="if_newer").read_bytes() == b"mine"


def test_failure_to_set_the_local_time_only_warns(server, monkeypatch, caplog):
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")

    def refuse(*args):
        raise PermissionError("read-only")

    monkeypatch.setattr(client_module.os, "utime", refuse)
    client.upload("a.h5")
    assert "cannot give" in caplog.text
    assert "upload time" in caplog.text


def test_upload_archives_under_another_key(server):
    stub, root = server
    tree = root / "private_html" / "renate-od"
    place(tree / "actual" / "c.h5", b"old")
    client = DataClient("renate-od")
    place(client.local_path("actual/c.h5"), b"new")
    client.upload("actual/c.h5", archive_to="archive/1.3.1/c.h5")
    assert (tree / "actual" / "c.h5").read_bytes() == b"new"
    assert (tree / "archive" / "1.3.1" / "c.h5").read_bytes() == b"old"


def test_upload_refuses_to_replace_without_archiving(server):
    stub, root = server
    tree = root / "private_html" / "renate-od"
    place(tree / "a.h5", b"old")
    client = DataClient("renate-od")
    local = client.local_path("a.h5")
    place(local, b"new")
    with pytest.raises(TransferError, match="already on the server"):
        client.upload("a.h5")
    assert (tree / "a.h5").read_bytes() == b"old"
    assert local.stat().st_mtime == OLD


def test_missing_local_copy_is_refused_before_connecting(server):
    stub, root = server
    with pytest.raises(PathError, match=r"cannot upload 'a.h5': .* is not a file"):
        DataClient("renate-od").upload("a.h5")
    assert stub.connections == 0


@pytest.mark.parametrize(
    ("key", "archive_to", "message"),
    [
        ("../a.h5", None, "invalid data key '../a.h5'"),
        ("a.h5", "/abs.h5", "invalid data key '/abs.h5'"),
        ("a.h5", "a.h5", "under its own key"),
    ],
)
def test_invalid_keys_are_refused_before_connecting(server, key, archive_to, message):
    stub, root = server
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    with pytest.raises(PathError, match=message):
        client.upload(key, archive_to=archive_to)
    assert stub.connections == 0
