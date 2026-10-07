import logging
import os

import pytest

from plasmasds_utility import PathError, _files


def leftovers(directory):
    return [p.name for p in directory.iterdir() if p.name.endswith(".part")]


def test_writing_moves_the_file_into_place(home):
    target = home / "a.bin"
    with _files.writing(target) as partial:
        partial.file.write(b"data")
        assert not target.exists()  # not visible until complete
    assert target.read_bytes() == b"data"
    assert leftovers(home) == []


def test_writing_sets_the_mtime(home):
    target = home / "a.bin"
    with _files.writing(target) as partial:
        partial.file.write(b"x")
        partial.mtime = 1519422481
    assert target.stat().st_mtime == 1519422481


def test_writing_replaces_an_existing_file(home):
    target = home / "a.bin"
    target.write_bytes(b"old")
    with _files.writing(target) as partial:
        partial.file.write(b"new")
    assert target.read_bytes() == b"new"


def test_failure_keeps_the_old_file_and_no_temporary(home):
    target = home / "a.bin"
    target.write_bytes(b"old")
    with pytest.raises(RuntimeError), _files.writing(target) as partial:
        partial.file.write(b"half")
        raise RuntimeError("interrupted")
    assert target.read_bytes() == b"old"
    assert leftovers(home) == []


class FullDisk:
    """A file object whose writes fail like a full disk."""

    def __init__(self, fd, mode):
        os.close(fd)

    def write(self, data):
        raise OSError(28, "No space left on device")

    def tell(self):
        return 0

    def flush(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_write_failure_is_a_path_error_and_leaves_nothing(home, monkeypatch):
    monkeypatch.setattr(_files.os, "fdopen", FullDisk)
    target = home / "a.bin"
    target.write_bytes(b"old")
    with pytest.raises(PathError, match=r"cannot write .*No space left"):
        with _files.writing(target) as partial:
            partial.file.write(b"data")
    assert target.read_bytes() == b"old"
    assert leftovers(home) == []


def test_failed_move_is_a_path_error_and_leaves_nothing(home, monkeypatch):
    def refuse(*args):
        raise PermissionError("target is locked")

    monkeypatch.setattr(_files.os, "replace", refuse)
    target = home / "a.bin"
    with pytest.raises(PathError, match="target is locked"):
        with _files.writing(target) as partial:
            partial.file.write(b"data")
    assert leftovers(home) == []


def test_failed_create_is_a_path_error(home, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError("read-only directory")

    monkeypatch.setattr(_files.tempfile, "mkstemp", refuse)
    with pytest.raises(PathError, match="read-only directory"):
        with _files.writing(home / "a.bin"):
            pass


def test_failing_utime_only_warns(home, monkeypatch, caplog):
    def refuse(*args, **kwargs):
        raise PermissionError("not allowed here")

    monkeypatch.setattr(_files.os, "utime", refuse)
    target = home / "a.bin"
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        with _files.writing(target) as partial:
            partial.file.write(b"x")
            partial.mtime = 1519422481
    assert target.read_bytes() == b"x"
    assert "cannot set the modification time" in caplog.text


def test_make_parent_creates_directories(home):
    target = home / "a" / "b" / "c.bin"
    _files.make_parent(target)
    assert target.parent.is_dir()


def test_make_parent_reports_a_file_in_the_way(home):
    (home / "file").write_text("x", encoding="utf-8")
    with pytest.raises(PathError, match="cannot create the directory"):
        _files.make_parent(home / "file" / "c.bin")
