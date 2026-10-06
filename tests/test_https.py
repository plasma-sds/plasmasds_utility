import logging
import socket
import time
import types

import pytest

from plasmasds_utility import PathError, TransferError, _https

LAST_MODIFIED = "Fri, 23 Feb 2018 21:48:01 GMT"
TIMESTAMP = 1519422481


@pytest.fixture
def sleeps(monkeypatch):
    """Record the backoff waits instead of sleeping."""
    calls = []
    monkeypatch.setattr(_https, "time", types.SimpleNamespace(sleep=calls.append))
    return calls


def leftovers(directory):
    return [p.name for p in directory.iterdir() if p.name.endswith(".part")]


def test_download_writes_the_file_with_the_server_mtime(http_server, home, sleeps):
    http_server.serve("/a.txt", {"body": b"hello", "last_modified": LAST_MODIFIED})
    target = home / "a.txt"
    assert _https.download(http_server.url("/a.txt"), target) == target
    assert target.read_bytes() == b"hello"
    assert target.stat().st_mtime == TIMESTAMP
    assert http_server.requests["/a.txt"] == 1
    assert leftovers(home) == []
    assert sleeps == []


def test_download_creates_missing_directories(http_server, home):
    http_server.serve("/a.txt", {"body": b"x"})
    target = home / "deep" / "er" / "a.txt"
    _https.download(http_server.url("/a.txt"), target)
    assert target.read_bytes() == b"x"


def test_download_replaces_an_existing_file(http_server, home):
    http_server.serve("/a.txt", {"body": b"new"})
    target = home / "a.txt"
    target.write_bytes(b"old")
    _https.download(http_server.url("/a.txt"), target)
    assert target.read_bytes() == b"new"


def test_large_file_is_streamed_intact(http_server, home):
    body = bytes(range(256)) * (3 * 4096 + 7)  # about 3 MB, several chunks
    http_server.serve("/big.bin", {"body": body})
    target = home / "big.bin"
    _https.download(http_server.url("/big.bin"), target)
    assert target.read_bytes() == body


def test_404_is_not_retried(http_server, home, sleeps):
    target = home / "missing.txt"
    with pytest.raises(TransferError, match=r"not on the public server \(HTTP 404\)"):
        _https.download(http_server.url("/missing.txt"), target)
    assert http_server.requests["/missing.txt"] == 1
    assert not target.exists()
    assert leftovers(home) == []
    assert sleeps == []


def test_other_client_errors_are_not_retried(http_server, home, sleeps):
    http_server.serve("/secret.txt", {"status": 403})
    with pytest.raises(TransferError, match="HTTP 403") as error:
        _https.download(http_server.url("/secret.txt"), home / "s.txt")
    assert http_server.requests["/secret.txt"] == 1
    assert error.value.__cause__ is not None
    assert sleeps == []


def test_server_errors_are_retried_with_backoff(http_server, home, sleeps):
    http_server.serve("/a.txt", {"status": 500}, {"status": 503}, {"body": b"ok"})
    target = home / "a.txt"
    _https.download(http_server.url("/a.txt"), target)
    assert target.read_bytes() == b"ok"
    assert http_server.requests["/a.txt"] == 3
    assert sleeps == [1.0, 2.0]


def test_retries_are_bounded(http_server, home, sleeps):
    http_server.serve("/a.txt", {"status": 500})
    with pytest.raises(TransferError, match="after 3 attempts") as error:
        _https.download(http_server.url("/a.txt"), home / "a.txt")
    assert http_server.url("/a.txt") in str(error.value)
    assert http_server.requests["/a.txt"] == 3
    assert sleeps == [1.0, 2.0]
    assert leftovers(home) == []


def test_attempts_and_backoff_are_parameters(http_server, home, sleeps):
    http_server.serve("/a.txt", {"status": 500})
    with pytest.raises(TransferError, match="after 4 attempts"):
        _https.download(
            http_server.url("/a.txt"), home / "a.txt", attempts=4, backoff=0.5
        )
    assert sleeps == [0.5, 1.0, 2.0]


def test_timeout_is_retried_then_reported(http_server, home, sleeps):
    http_server.serve("/slow.txt", {"body": b"late", "stall": 0.5})
    start = time.monotonic()
    with pytest.raises(TransferError, match="after 2 attempts"):
        _https.download(
            http_server.url("/slow.txt"), home / "slow.txt", timeout=0.1, attempts=2
        )
    assert time.monotonic() - start < 5
    assert http_server.requests["/slow.txt"] == 2
    assert not (home / "slow.txt").exists()
    assert leftovers(home) == []


def test_truncated_transfer_leaves_no_partial_file(http_server, home, sleeps):
    http_server.serve("/a.txt", {"body": b"0123456789", "length": 20})
    target = home / "a.txt"
    target.write_bytes(b"previous")
    with pytest.raises(TransferError, match="cut short: received 10 of 20 bytes"):
        _https.download(http_server.url("/a.txt"), target)
    assert target.read_bytes() == b"previous"
    assert leftovers(home) == []
    assert http_server.requests["/a.txt"] == 3


def test_truncated_transfer_succeeds_when_a_retry_is_complete(
    http_server, home, sleeps
):
    http_server.serve(
        "/a.txt", {"body": b"01234", "length": 10}, {"body": b"0123456789"}
    )
    target = home / "a.txt"
    _https.download(http_server.url("/a.txt"), target)
    assert target.read_bytes() == b"0123456789"


def test_refused_connection_is_reported(home, sleeps):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(TransferError, match="after 3 attempts"):
        _https.download(f"http://127.0.0.1:{port}/a.txt", home / "a.txt")
    assert leftovers(home) == []


@pytest.mark.parametrize("header", [None, "not a date"])
def test_unusable_last_modified_keeps_the_download_time(
    http_server, home, caplog, header
):
    http_server.serve("/a.txt", {"body": b"x", "last_modified": header})
    target = home / "a.txt"
    before = time.time()
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        _https.download(http_server.url("/a.txt"), target)
    assert target.stat().st_mtime >= before - 2
    assert "no usable Last-Modified header" in caplog.text


def test_failing_utime_keeps_the_download(
    http_server, home, caplog, monkeypatch, sleeps
):
    http_server.serve("/a.txt", {"body": b"x", "last_modified": LAST_MODIFIED})

    def refuse(*args, **kwargs):
        raise PermissionError("not allowed here")

    monkeypatch.setattr(_https.os, "utime", refuse)
    target = home / "a.txt"
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        _https.download(http_server.url("/a.txt"), target)
    assert target.read_bytes() == b"x"
    assert http_server.requests["/a.txt"] == 1
    assert "cannot set the modification time" in caplog.text


def test_malformed_content_length_skips_the_size_check(http_server, home, caplog):
    http_server.serve("/a.txt", {"body": b"hello", "length": "five"})
    target = home / "a.txt"
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        _https.download(http_server.url("/a.txt"), target)
    assert target.read_bytes() == b"hello"
    assert "malformed Content-Length ('five')" in caplog.text


def test_unencrypted_url_logs_a_warning(http_server, home, caplog):
    http_server.serve("/a.txt", {"body": b"x"})
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        _https.download(http_server.url("/a.txt"), home / "a.txt")
    assert "unencrypted connection" in caplog.text


def test_unusable_target_directory_is_a_path_error(home):
    (home / "file").write_text("x", encoding="utf-8")
    with pytest.raises(PathError, match="cannot create the directory"):
        _https.download("https://example.invalid/a", home / "file" / "a")
