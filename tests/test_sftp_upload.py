import io
import types
from pathlib import Path, PurePosixPath

import paramiko
import pytest
from paramiko.sftp import CMD_EXTENDED

from plasmasds_utility import PathError, TransferError, _config, _sftp


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


# Uploads.

REMOTE = PurePosixPath("private_html/renate-od/a/x.h5")
ARCHIVE = PurePosixPath("private_html/renate-od/archive/1.0/a/x.h5")
FAILED = (paramiko.SFTP_FAILURE, paramiko.SFTP_PERMISSION_DENIED)


@pytest.fixture
def sleeps(monkeypatch):
    """Record the backoff waits instead of sleeping."""
    calls = []
    monkeypatch.setattr(_sftp, "time", types.SimpleNamespace(sleep=calls.append))
    return calls


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "local" / "x.h5"
    path.parent.mkdir()
    path.write_bytes(b"new data")
    return path


@pytest.fixture
def server(sftp_server, tmp_path):
    """The test server, and a function giving the local path of a server path."""
    root = tmp_path / "server"
    return sftp_server, lambda remote: root.joinpath(*remote.parts)


@pytest.fixture
def existing(server):
    """REMOTE on the server, with old content."""
    stub, local = server
    local(REMOTE).parent.mkdir(parents=True)
    local(REMOTE).write_bytes(b"old data")
    return server


def upload(source, **kwargs):
    return _sftp.upload(_config.settings(), source, REMOTE, **kwargs)


def leftovers(server):
    stub, local = server
    return sorted(p.name for p in Path(stub.root).rglob("*.part"))


def test_upload_creates_directories_and_the_file(server, source, sleeps):
    stub, local = server
    mtime = upload(source)
    assert local(REMOTE).read_bytes() == b"new data"
    assert mtime == int(local(REMOTE).stat().st_mtime)  # SFTP sends whole seconds
    assert stub.modes == {
        "private_html": 0o755,
        "private_html/renate-od": 0o755,
        "private_html/renate-od/a": 0o755,
        **{name: 0o644 for name in stub.modes if name.endswith(".part")},
    }
    assert sorted(stub.modes.values()) == [0o644, 0o755, 0o755, 0o755]
    assert stub.operations["mkdir"] == 3  # private_html, renate-od, a
    assert stub.operations["rename"] == 1
    assert leftovers(server) == []
    assert sleeps == []


def test_upload_never_replaces_a_file_without_archiving(existing, source):
    stub, local = existing
    with pytest.raises(TransferError, match="already on the server.*archive_to"):
        upload(source)
    assert local(REMOTE).read_bytes() == b"old data"
    assert stub.operations["write"] == 0


def test_upload_archives_the_old_file_by_a_hard_link(existing, source):
    stub, local = existing
    upload(source, archive_to=ARCHIVE)
    assert local(REMOTE).read_bytes() == b"new data"
    assert local(ARCHIVE).read_bytes() == b"old data"
    assert stub.operations["hardlink"] == 1
    assert stub.operations["posix-rename"] == 1
    assert leftovers(existing) == []


def test_upload_refuses_an_existing_archive_name(existing, source):
    stub, local = existing
    local(ARCHIVE).parent.mkdir(parents=True)
    local(ARCHIVE).write_bytes(b"older data")
    with pytest.raises(TransferError, match=f"{ARCHIVE} is already on the server"):
        upload(source, archive_to=ARCHIVE)
    assert local(ARCHIVE).read_bytes() == b"older data"
    assert local(REMOTE).read_bytes() == b"old data"
    assert stub.operations["write"] == 0


def test_upload_with_nothing_to_archive_warns(server, source, caplog):
    stub, local = server
    upload(source, archive_to=ARCHIVE)
    assert local(REMOTE).read_bytes() == b"new data"
    assert not local(ARCHIVE).exists()
    assert "nothing to archive" in caplog.text
    assert stub.operations["hardlink"] == 0


def test_upload_makes_the_file_known_to_be_on_the_server(server, source):
    settings = _config.settings()
    with pytest.raises(_sftp.NotOnServer):
        _sftp.stat(settings, REMOTE)
    upload(source)
    assert _sftp.stat(settings, REMOTE)[0] == len(b"new data")


def test_missing_source_is_a_path_error(server, tmp_path):
    with pytest.raises(PathError, match="cannot read"):
        upload(tmp_path / "nowhere.h5")
    stub, local = server
    assert stub.operations == {}  # not even a directory was made


@pytest.mark.parametrize("operation", ["mkdir", "write", "chmod"])
def test_denied_upload_is_not_retried(server, source, sleeps, operation):
    stub, local = server
    stub.refused[operation] = paramiko.SFTP_PERMISSION_DENIED
    with pytest.raises(TransferError, match="cannot upload .* denied"):
        upload(source)
    assert stub.operations[operation] == 1
    assert sleeps == []
    assert leftovers(server) == []
    assert not local(REMOTE).exists()


def test_failing_upload_is_retried_then_reported(server, source, sleeps):
    stub, local = server
    local(REMOTE).parent.mkdir(parents=True)  # chmod: only the file's
    stub.refused["chmod"] = paramiko.SFTP_FAILURE
    with pytest.raises(TransferError, match="cannot upload .* after 3 attempts"):
        upload(source)
    assert stub.operations["write"] == 3
    assert sleeps == [1.0, 2.0]
    assert leftovers(server) == []  # removed after each attempt


@pytest.mark.parametrize("operation", ["write", "chmod"])
def test_dropped_upload_is_retried_and_cleaned_up(server, source, sleeps, operation):
    stub, local = server
    local(REMOTE).parent.mkdir(parents=True)  # chmod: only the file's
    stub.drop.add(operation)
    upload(source)
    assert local(REMOTE).read_bytes() == b"new data"
    assert stub.operations["write"] == 2
    assert leftovers(server) == []  # the first attempt's file, removed by the second


def test_temporary_file_left_by_a_lost_connection_is_named(server, source, sleeps):
    stub, local = server
    local(REMOTE).parent.mkdir(parents=True)  # chmod: only the file's
    stub.drop.add("chmod")
    with pytest.raises(TransferError, match=r"\.x\.h5\.\w+\.part may be left"):
        upload(source, attempts=1)
    assert len(leftovers(server)) == 1


def test_server_without_hard_links_is_reported(existing, source, sleeps):
    stub, local = existing
    stub.refused["hardlink"] = paramiko.SFTP_OP_UNSUPPORTED
    with pytest.raises(TransferError, match="does not support hard links") as error:
        upload(source, archive_to=ARCHIVE)
    assert "appear" not in str(error.value)
    assert "server file is unchanged, and the temporary file was removed" in str(
        error.value
    )
    assert local(REMOTE).read_bytes() == b"old data"
    assert leftovers(existing) == []
    assert stub.operations["hardlink"] == 1  # not retried


def test_failed_hard_link_says_where(existing, source, sleeps):
    stub, local = existing
    stub.refused["hardlink"] = paramiko.SFTP_FAILURE
    with pytest.raises(TransferError, match=f"archiving the old .* as {ARCHIVE}") as e:
        upload(source, archive_to=ARCHIVE)
    assert "may now be on the server" not in str(e.value)
    assert local(REMOTE).read_bytes() == b"old data"
    assert leftovers(existing) == []


def test_failed_replace_removes_the_archive_name(existing, source, sleeps):
    stub, local = existing
    stub.refused["posix-rename"] = paramiko.SFTP_FAILURE
    with pytest.raises(TransferError, match="but replacing .* failed") as error:
        upload(source, archive_to=ARCHIVE)
    assert "temporary file was removed" in str(error.value)
    assert local(REMOTE).read_bytes() == b"old data"
    assert not local(ARCHIVE).exists()
    assert leftovers(existing) == []
    assert stub.operations["posix-rename"] == 1  # not retried


def test_file_appearing_during_the_upload_is_not_replaced(server, source, sleeps):
    stub, local = server
    stub.refused["rename"] = paramiko.SFTP_FAILURE  # as OpenSSH answers then
    with pytest.raises(TransferError, match="appear on the server meanwhile"):
        upload(source)
    assert leftovers(server) == []
    assert stub.operations["rename"] == 1


def test_lost_connection_while_replacing_leaves_the_files(existing, source, sleeps):
    stub, local = existing
    stub.drop.add("posix-rename")
    with pytest.raises(TransferError, match="not known whether .* was replaced") as e:
        upload(source, archive_to=ARCHIVE)
    assert "left on the server: private_html/renate-od/a/.x.h5." in str(e.value)
    assert str(ARCHIVE) in str(e.value).split("left on the server")[1]
    assert local(REMOTE).read_bytes() == b"old data"
    assert local(ARCHIVE).read_bytes() == b"old data"  # kept: it may be the only copy
    assert len(leftovers(existing)) == 1


def test_replace_done_but_its_answer_lost_keeps_the_archive(
    existing, source, monkeypatch, caplog
):
    stub, local = existing
    posix_rename = paramiko.SFTPClient.posix_rename

    def rename_then_time_out(sftp, old, new):
        posix_rename(sftp, old, new)
        raise TimeoutError("timed out")

    monkeypatch.setattr(paramiko.SFTPClient, "posix_rename", rename_then_time_out)
    assert upload(source, archive_to=ARCHIVE) is not None
    assert local(REMOTE).read_bytes() == b"new data"
    assert local(ARCHIVE).read_bytes() == b"old data"  # the only copy: never removed
    assert "but the file is in place" in caplog.text
    assert leftovers(existing) == []


def test_rename_done_but_its_answer_lost_counts_as_done(
    server, source, monkeypatch, caplog
):
    stub, local = server
    rename = paramiko.SFTPClient.rename

    def rename_then_time_out(sftp, old, new):
        rename(sftp, old, new)
        raise TimeoutError("timed out")

    monkeypatch.setattr(paramiko.SFTPClient, "rename", rename_then_time_out)
    upload(source)
    assert local(REMOTE).read_bytes() == b"new data"
    assert "but the file is in place" in caplog.text


def test_hard_link_made_but_its_answer_lost_is_kept(existing, source, monkeypatch):
    stub, local = existing
    hardlink = _sftp._hardlink

    def link_then_time_out(sftp, old, new):
        hardlink(sftp, old, new)
        raise TimeoutError("timed out")

    monkeypatch.setattr(_sftp, "_hardlink", link_then_time_out)
    with pytest.raises(TransferError, match="server file is unchanged") as error:
        upload(source, archive_to=ARCHIVE)
    assert f"{ARCHIVE} may now be on the server" in str(error.value)
    assert local(REMOTE).read_bytes() == b"old data"
    assert local(ARCHIVE).read_bytes() == b"old data"  # a second name: harmless
    assert leftovers(existing) == []


def test_interrupted_upload_names_what_may_be_left(
    existing, source, monkeypatch, caplog
):
    stub, local = existing

    def interrupt(sftp, old, new):
        raise KeyboardInterrupt

    monkeypatch.setattr(paramiko.SFTPClient, "posix_rename", interrupt)
    with pytest.raises(KeyboardInterrupt):
        upload(source, archive_to=ARCHIVE)
    assert "was interrupted; this may be left on the server" in caplog.text
    assert ".x.h5." in caplog.text
    assert str(ARCHIVE) in caplog.text
    assert local(REMOTE).read_bytes() == b"old data"


def test_interrupted_request_replaces_the_session(server, source, monkeypatch):
    stub, local = server
    putfo = paramiko.SFTPClient.putfo
    calls = []

    def interrupt_once(sftp, *args, **kwargs):
        if not calls:
            calls.append(1)
            raise KeyboardInterrupt
        return putfo(sftp, *args, **kwargs)

    monkeypatch.setattr(paramiko.SFTPClient, "putfo", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        upload(source)
    assert _sftp._sessions == {}
    upload(source)
    assert stub.connections == 2
    assert local(REMOTE).read_bytes() == b"new data"


def test_folder_at_the_key_is_refused_before_sending(server, source, sleeps):
    stub, local = server
    local(REMOTE).mkdir(parents=True)
    with pytest.raises(TransferError, match="it is a folder"):
        upload(source, archive_to=ARCHIVE)
    assert stub.operations["write"] == 0


def test_file_in_the_way_of_the_archive_folder_is_refused_before_sending(
    existing, source, sleeps
):
    stub, local = existing
    inside_the_file = REMOTE / "old.h5"  # OpenSSH: "no such file", not ENOTDIR
    with pytest.raises(TransferError, match=f"below {REMOTE}: it is a file"):
        upload(source, archive_to=inside_the_file)
    assert stub.operations["write"] == 0
    assert local(REMOTE).read_bytes() == b"old data"


def test_denied_archive_folder_is_refused_before_sending(existing, source, sleeps):
    stub, local = existing
    stub.refused["mkdir"] = paramiko.SFTP_PERMISSION_DENIED
    with pytest.raises(TransferError, match="denied"):
        upload(source, archive_to=ARCHIVE)
    assert stub.operations["write"] == 0
    assert stub.operations["mkdir"] == 1


def test_source_changed_during_the_upload_is_refused(server, source, monkeypatch):
    stub, local = server
    putfo = paramiko.SFTPClient.putfo

    def putfo_while_writing(sftp, *args, **kwargs):
        result = putfo(sftp, *args, **kwargs)
        with open(source, "ab") as file:
            file.write(b" and more")
        return result

    monkeypatch.setattr(paramiko.SFTPClient, "putfo", putfo_while_writing)
    with pytest.raises(PathError, match="changed during the upload"):
        upload(source)
    assert stub.operations["write"] == 1  # not retried
    assert leftovers(server) == []
    assert not local(REMOTE).exists()


def test_local_read_errors_are_path_errors():
    class Failing:
        def read(self, size):
            raise PermissionError(13, "Permission denied")

    with pytest.raises(PathError, match="cannot read x.h5: .*Permission denied"):
        _sftp._Source(Failing(), "x.h5").read(10)


def test_lost_connection_while_archiving_leaves_the_file_unchanged(
    existing, source, sleeps
):
    stub, local = existing
    stub.drop.add("hardlink")
    with pytest.raises(TransferError, match="server file is unchanged") as error:
        upload(source, archive_to=ARCHIVE)
    assert "not known" not in str(error.value)
    assert f"{ARCHIVE} may now be on the server" in str(error.value)
    assert "left on the server: private_html/renate-od/a/.x.h5." in str(error.value)
    assert local(REMOTE).read_bytes() == b"old data"


def test_lost_connection_while_creating_says_so(server, source, sleeps):
    stub, local = server
    stub.drop.add("rename")
    with pytest.raises(TransferError, match="not known whether .* was created"):
        upload(source)


def test_replacing_with_the_same_size_warns(existing, source, caplog):
    stub, local = existing
    source.write_bytes(b"NEW DATA")  # as long as the old data: 8 bytes
    upload(source, archive_to=ARCHIVE)
    assert local(REMOTE).read_bytes() == b"NEW DATA"
    assert "same size (8 bytes)" in caplog.text


def test_replacing_with_another_size_does_not_warn(existing, source, caplog):
    stub, local = existing
    source.write_bytes(b"newer, longer data")
    upload(source, archive_to=ARCHIVE)
    assert "same size" not in caplog.text
