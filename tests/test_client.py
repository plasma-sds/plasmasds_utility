import itertools
import json
import logging
import os
import subprocess
import sys
import types

import pytest
from conftest import write_config

import plasmasds_utility
import plasmasds_utility.client as client_module
from plasmasds_utility import (
    AuthError,
    ConfigError,
    DataClient,
    PathError,
    TransferError,
    _config,
    _sftp,
)

logger = logging.getLogger("plasmasds_utility")


@pytest.mark.parametrize("prefix", ["renate-od", "neuro_bes", "synref", "a", "v1.2"])
def test_valid_prefixes(prefix):
    assert DataClient(prefix).prefix == prefix


@pytest.mark.parametrize(
    "prefix",
    ["", ".", "..", ".hidden", "-x", "_x", "a/b", "a\\b", "C:", "/abs", "a b", "é"],
)
def test_invalid_prefixes(prefix):
    with pytest.raises(PathError, match="invalid client prefix"):
        DataClient(prefix)


@pytest.mark.parametrize("prefix", [None, 5, b"renate-od"])
def test_prefix_must_be_a_string(prefix):
    with pytest.raises(PathError, match="invalid client prefix"):
        DataClient(prefix)


@pytest.mark.parametrize("prefix", ["config.json", "CONFIG.JSON", "plasmasds.log"])
def test_reserved_prefixes(prefix):
    with pytest.raises(PathError, match="reserved") as error:
        DataClient(prefix)
    assert "config.json" in str(error.value)
    assert "plasmasds.log" in str(error.value)


@pytest.mark.parametrize(
    ("prefix", "reason"),
    [
        ("renate.", "ends in a dot or a space"),
        ("con", "reserved device name"),
        ("NUL", "reserved device name"),
        ("com1.data", "reserved device name"),
    ],
)
def test_prefixes_windows_would_mangle(prefix, reason):
    with pytest.raises(PathError, match=f"invalid client prefix {prefix!r}"):
        DataClient(prefix)
    with pytest.raises(PathError, match=reason):
        DataClient(prefix)


def test_construction_reads_and_writes_nothing(home):
    DataClient("renate-od", working_dir=home / "data")
    assert list(home.iterdir()) == []
    assert logger.handlers == []


def test_import_and_construction_in_a_fresh_process_write_nothing(home):
    code = "import plasmasds_utility; plasmasds_utility.DataClient('renate-od')"
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ.copy())
    assert list(home.iterdir()) == []


def test_first_use_attaches_logging():
    DataClient("renate-od").client_dir()
    assert len(logger.handlers) == 2


def test_default_client_dir(home):
    expected = _config.default_data_dir() / "renate-od"
    assert DataClient("renate-od").client_dir() == expected
    assert not expected.exists()


@pytest.mark.parametrize(
    ("explicit", "env", "saved"), list(itertools.product([False, True], repeat=3))
)
def test_client_dir_precedence(home, monkeypatch, explicit, env, saved):
    if saved:
        _config.save_working_dir("renate-od", str(home / "saved"))
    if env:
        monkeypatch.setenv("PLASMASDS_DATA_DIR", str(home / "env"))
    client = DataClient(
        "renate-od", working_dir=home / "explicit" if explicit else None
    )
    if explicit:
        expected = home / "explicit"
    elif env:
        expected = home / "env" / "renate-od"
    elif saved:
        expected = home / "saved"
    else:
        expected = _config.default_data_dir() / "renate-od"
    assert client.client_dir() == expected


def test_saved_dir_belongs_to_its_prefix_only(home):
    _config.save_working_dir("synref", str(home / "synref"))
    assert DataClient("renate-od").client_dir() == (
        _config.default_data_dir() / "renate-od"
    )


def test_relative_working_dir_is_taken_from_the_current_directory(home, monkeypatch):
    monkeypatch.chdir(home)
    client = DataClient("renate-od", working_dir="data")
    monkeypatch.chdir(home.parent)
    assert client.client_dir() == home / "data"


def test_working_dir_expands_user(home):
    assert DataClient("renate-od", working_dir="~/data").client_dir() == home / "data"


def test_relative_env_dir_is_a_config_error(monkeypatch):
    monkeypatch.setenv("PLASMASDS_DATA_DIR", "relative")
    with pytest.raises(ConfigError, match="PLASMASDS_DATA_DIR"):
        DataClient("renate-od").client_dir()


def test_set_working_dir_creates_saves_and_returns_the_directory(home):
    result = DataClient("renate-od").set_working_dir(home / "new" / "data")
    assert result == home / "new" / "data"
    assert result.is_dir()
    assert DataClient("renate-od").client_dir() == home / "new" / "data"


def test_set_working_dir_is_seen_by_a_new_process(home):
    DataClient("renate-od").set_working_dir(home / "data")
    code = (
        "import plasmasds_utility as p; print(p.DataClient('renate-od').client_dir())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.stdout.strip() == str(home / "data")


def test_set_working_dir_resolves_relative_paths_and_user(home, monkeypatch):
    monkeypatch.chdir(home)
    assert DataClient("renate-od").set_working_dir("rel") == home / "rel"
    assert DataClient("synref").set_working_dir("~/tilde") == home / "tilde"


def test_set_working_dir_accepts_an_existing_directory(home):
    (home / "data").mkdir()
    assert DataClient("renate-od").set_working_dir(home / "data") == home / "data"


def test_set_working_dir_on_a_file_is_a_path_error(home):
    (home / "file").write_text("x", encoding="utf-8")
    with pytest.raises(PathError, match="exists and is not a directory"):
        DataClient("renate-od").set_working_dir(home / "file")
    assert not (_config.config_dir() / _config.CONFIG_FILE).exists()


def test_set_working_dir_below_a_file_is_a_path_error(home):
    (home / "file").write_text("x", encoding="utf-8")
    with pytest.raises(PathError):
        DataClient("renate-od").set_working_dir(home / "file" / "data")


def test_set_working_dir_none_is_a_path_error(home):
    with pytest.raises(PathError, match=r"use clear_working_dir\(\)"):
        DataClient("renate-od").set_working_dir(None)
    assert list(home.iterdir()) == []  # rejected before any I/O


def test_clear_working_dir_restores_the_default(home):
    client = DataClient("renate-od")
    client.set_working_dir(home / "data")
    assert client.clear_working_dir() is None
    assert DataClient("renate-od").client_dir() == (
        _config.default_data_dir() / "renate-od"
    )
    assert (home / "data").is_dir()  # the directory itself is left alone


def test_clear_working_dir_keeps_other_clients(home):
    DataClient("renate-od").set_working_dir(home / "renate")
    DataClient("synref").set_working_dir(home / "synref")
    DataClient("renate-od").clear_working_dir()
    assert DataClient("synref").client_dir() == home / "synref"


def test_clear_working_dir_without_a_saved_one_writes_nothing(caplog):
    with caplog.at_level(logging.INFO, logger="plasmasds_utility"):
        DataClient("renate-od").clear_working_dir()
    assert not (_config.config_dir() / _config.CONFIG_FILE).exists()
    assert "removed" not in caplog.text


def test_clear_working_dir_logs_the_change(home):
    client = DataClient("renate-od")
    client.set_working_dir(home / "data")
    client.clear_working_dir()
    text = (_config.log_dir() / _config.LOG_FILE).read_text("utf-8")
    assert "removed the working directory for 'renate-od'" in text


def test_set_working_dir_logs_the_change(home):
    DataClient("renate-od").set_working_dir(home / "data")
    text = (_config.log_dir() / _config.LOG_FILE).read_text("utf-8")
    assert f"working directory for 'renate-od' set to {home / 'data'}" in text


def test_set_working_dir_warns_when_env_takes_precedence(home, monkeypatch, caplog):
    monkeypatch.setenv("PLASMASDS_DATA_DIR", str(home / "env"))
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        DataClient("renate-od").set_working_dir(home / "data")
    assert "PLASMASDS_DATA_DIR is set and takes precedence" in caplog.text


def test_set_working_dir_warns_when_explicit_dir_takes_precedence(home, caplog):
    client = DataClient("renate-od", working_dir=home / "explicit")
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        client.set_working_dir(home / "data")
    assert "given when it was created" in caplog.text
    assert client.client_dir() == home / "explicit"


def test_set_working_dir_without_precedence_conflict_does_not_warn(home, caplog):
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        DataClient("renate-od").set_working_dir(home / "data")
    assert caplog.records == []


def test_saved_file_has_the_expected_content(home):
    DataClient("renate-od").set_working_dir(home / "data")
    path = _config.config_dir() / _config.CONFIG_FILE
    assert json.loads(path.read_text("utf-8")) == {
        "working_dirs": {"renate-od": str(home / "data")}
    }


def test_local_path_private_by_default():
    client = DataClient("renate-od")
    assert client.local_path("atomic_data/Na/rates.h5") == (
        client.client_dir() / "private" / "atomic_data" / "Na" / "rates.h5"
    )


def test_local_path_public():
    client = DataClient("renate-od")
    assert client.local_path("a/b.h5", private=False) == (
        client.client_dir() / "public" / "a" / "b.h5"
    )


def test_local_path_follows_the_client_dir(home):
    client = DataClient("renate-od", working_dir=home / "wd")
    assert client.local_path("a.h5") == home / "wd" / "private" / "a.h5"


def test_local_path_creates_nothing_below_the_client_dir():
    client = DataClient("renate-od")
    client.local_path("a/b.h5")
    assert not client.client_dir().exists()


def test_local_path_rejects_an_invalid_key_before_any_io(home):
    with pytest.raises(PathError, match=r"invalid data key '\.\./x': '\.\.'"):
        DataClient("renate-od").local_path("../x")
    assert list(home.iterdir()) == []
    assert logger.handlers == []


@pytest.fixture
def public_server(http_server):
    """The local HTTP server as the public data server; no private access.

    The private server is marked unavailable, as for a user without an SSH key,
    so no SSH connection is attempted.
    """
    write_config(host="127.0.0.1", port=9, public_url=http_server.url("/~data"))
    _sftp._unavailable[("127.0.0.1", 9, "data")] = AuthError("no key in this test")
    return http_server


LAST_MODIFIED = "Fri, 23 Feb 2018 21:48:01 GMT"


def test_get_downloads_a_missing_public_file(public_server):
    public_server.serve(
        "/~data/renate-od/atomic_data/Na/x.h5",
        {"body": b"data", "last_modified": LAST_MODIFIED},
    )
    client = DataClient("renate-od")
    path = client.get("atomic_data/Na/x.h5")
    assert path == client.local_path("atomic_data/Na/x.h5", private=False)
    assert path.read_bytes() == b"data"
    assert path.stat().st_mtime == 1519422481


def test_get_returns_a_present_file_without_contacting_the_server(public_server):
    public_server.serve("/~data/renate-od/a.h5", {"body": b"data"})
    client = DataClient("renate-od")
    client.get("a.h5")
    client.get("a.h5")
    assert public_server.requests["/~data/renate-od/a.h5"] == 1


def test_get_uses_a_file_placed_by_hand(public_server):
    client = DataClient("renate-od")
    path = client.local_path("a.h5", private=False)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"mine")
    assert client.get("a.h5").read_bytes() == b"mine"
    assert sum(public_server.requests.values()) == 0


def test_get_quotes_the_key_in_the_url(public_server):
    public_server.serve("/~data/renate-od/a%20b/%C3%BC.h5", {"body": b"x"})
    assert DataClient("renate-od").get("a b/ü.h5").read_bytes() == b"x"


def test_get_reports_a_missing_file(public_server):
    client = DataClient("renate-od")
    with pytest.raises(TransferError, match="not on the public server"):
        client.get("missing.h5")
    assert not client.local_path("missing.h5", private=False).exists()


def test_get_refuses_a_directory_in_the_way(public_server):
    client = DataClient("renate-od")
    client.local_path("a.h5", private=False).mkdir(parents=True)
    with pytest.raises(PathError, match="it is not a file"):
        client.get("a.h5")
    assert sum(public_server.requests.values()) == 0


def test_get_rejects_an_invalid_key_before_any_io(home):
    with pytest.raises(PathError, match="invalid data key"):
        DataClient("renate-od").get("/abs")
    assert list(home.iterdir()) == []


@pytest.fixture
def key_file(home):
    key = home / ".ssh" / "plasmasds_test"
    key.parent.mkdir()
    key.write_text("not a real key", encoding="utf-8")
    return key


def test_set_ssh_key_saves_the_absolute_path(key_file):
    assert plasmasds_utility.set_ssh_key(key_file) == key_file
    assert _config.ssh_key() == key_file


def test_set_ssh_key_expands_user_and_relative_paths(key_file, home, monkeypatch):
    assert plasmasds_utility.set_ssh_key("~/.ssh/plasmasds_test") == key_file
    monkeypatch.chdir(home / ".ssh")
    assert plasmasds_utility.set_ssh_key("plasmasds_test") == key_file


def test_set_ssh_key_rejects_a_missing_file(home):
    with pytest.raises(PathError, match="no such file"):
        plasmasds_utility.set_ssh_key(home / "missing")
    assert not (_config.config_dir() / _config.CONFIG_FILE).exists()


def test_set_ssh_key_none_clears_it(key_file):
    plasmasds_utility.set_ssh_key(key_file)
    assert plasmasds_utility.set_ssh_key(None) is None
    assert _config.ssh_key() is None


def test_set_ssh_key_logs_the_change(key_file):
    plasmasds_utility.set_ssh_key(key_file)
    text = (_config.log_dir() / _config.LOG_FILE).read_text("utf-8")
    assert f"SSH key set to {key_file}" in text


class Servers:
    """The local SFTP and HTTP servers as the private and public data servers."""

    def __init__(self, sftp, http, root):
        self.sftp = sftp
        self.http = http
        self.root = root

    def put_private(self, key, body=b"private"):
        path = self.root.joinpath("private_html", "renate-od", *key.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def put_public(self, key, body=b"public"):
        self.http.serve(f"/~data/renate-od/{key}", {"body": body})

    def public_requests(self):
        return sum(self.http.requests.values())


@pytest.fixture
def servers(sftp_server, http_server, tmp_path):
    settings = _config.settings()
    write_config(
        host="127.0.0.1",
        port=sftp_server.port,
        host_keys=settings["host_keys"],
        ssh_key=settings["ssh_key"],
        public_url=http_server.url("/~data"),
    )
    return Servers(sftp_server, http_server, tmp_path / "server")


def place(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def test_get_returns_the_local_private_copy_without_any_server(servers):
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    assert client.get("a.h5").read_bytes() == b"mine"
    assert servers.sftp.connections == 0
    assert servers.public_requests() == 0


def test_get_downloads_private_data_first(servers):
    servers.put_private("a/b.h5", b"real")
    servers.put_public("a/b.h5", b"dummy")
    client = DataClient("renate-od")
    path = client.get("a/b.h5")
    assert path == client.local_path("a/b.h5", private=True)
    assert path.read_bytes() == b"real"
    assert servers.public_requests() == 0


def test_a_local_public_copy_does_not_block_a_private_download(servers):
    servers.put_private("a.h5", b"real")
    client = DataClient("renate-od")
    place(client.local_path("a.h5", private=False), b"dummy")
    assert client.get("a.h5").read_bytes() == b"real"


def test_get_falls_back_to_the_local_public_copy(servers, caplog):
    client = DataClient("renate-od")
    place(client.local_path("a.h5", private=False), b"dummy")
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        path = client.get("a.h5")
    assert path == client.local_path("a.h5", private=False)
    assert "using public data instead of private data for 'a.h5'" in caplog.text
    assert "is not on the private server" in caplog.text
    assert servers.public_requests() == 0


def test_local_public_copy_asks_the_private_server_once(servers):
    client = DataClient("renate-od")
    place(client.local_path("a.h5", private=False), b"dummy")
    for _ in range(3):
        assert client.get("a.h5").read_bytes() == b"dummy"
    assert servers.sftp.stats["private_html/renate-od/a.h5"] == 1


def test_get_falls_back_to_a_public_download(servers):
    servers.put_public("a.h5", b"dummy")
    client = DataClient("renate-od")
    assert client.get("a.h5").read_bytes() == b"dummy"
    assert servers.public_requests() == 1


def test_only_the_first_fallback_warns(servers, caplog):
    for key in ("a.h5", "b.h5", "c.h5"):
        servers.put_public(key)
    client = DataClient("renate-od")
    with caplog.at_level(logging.INFO, logger="plasmasds_utility"):
        for key in ("a.h5", "b.h5", "c.h5", "a.h5"):
            client.get(key)
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "instead of private" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "show_public_fallbacks()" in warnings[0].getMessage()
    assert str(_config.log_dir() / _config.LOG_FILE) in warnings[0].getMessage()
    logged = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    for key in ("a.h5", "b.h5", "c.h5"):
        assert sum(f"using the public copy of {key!r}" in m for m in logged) == 1


def test_every_fallback_is_in_the_log_file(servers):
    servers.put_public("a.h5")
    servers.put_public("b.h5")
    client = DataClient("renate-od")
    client.get("a.h5")
    client.get("b.h5")
    text = (_config.log_dir() / _config.LOG_FILE).read_text("utf-8")
    assert "using the public copy of 'a.h5' for renate-od" in text
    assert "using the public copy of 'b.h5' for renate-od" in text


def test_show_public_fallbacks_lists_them(servers, capsys):
    servers.put_public("a.h5")
    servers.put_public("dir/b.h5")
    client = DataClient("renate-od")
    client.get("a.h5")
    client.get("dir/b.h5")
    capsys.readouterr()
    assert plasmasds_utility.show_public_fallbacks() is None
    out = capsys.readouterr().out
    assert "2 file(s) came from the public server instead of the private one" in out
    assert "  renate-od: a.h5 (" in out
    assert "  renate-od: dir/b.h5 (" in out
    assert "is not on the private server" in out


def test_show_public_fallbacks_without_any(capsys):
    plasmasds_utility.show_public_fallbacks()
    assert "no public fallbacks" in capsys.readouterr().out


def test_summary_at_exit_only_with_fallbacks(servers, capsys):
    client_module._summary_at_exit()
    assert capsys.readouterr().err == ""
    servers.put_public("a.h5")
    DataClient("renate-od").get("a.h5")
    capsys.readouterr()
    client_module._summary_at_exit()
    err = capsys.readouterr().err
    assert err.startswith("plasmasds_utility: 1 file(s) came from the public server")
    assert "renate-od: a.h5" in err


def test_summary_at_exit_in_a_real_process(home, http_server):
    http_server.serve("/~data/renate-od/a.h5", {"body": b"dummy"})
    code = (
        "import plasmasds_utility as p, plasmasds_utility._sftp as s;"
        "from plasmasds_utility import _config as c;"
        "st = c.settings();"
        "s._unavailable[(st['host'], st['port'], st['user'])] = p.AuthError('no key');"
        "p.DataClient('renate-od').get('a.h5')"
    )
    write_config(host="127.0.0.1", port=9, public_url=http_server.url("/~data"))
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=True,
    )
    assert "plasmasds_utility: 1 file(s) came from the public server" in result.stderr
    assert "renate-od: a.h5 (no key)" in result.stderr


def test_private_true_never_uses_public_data(servers):
    servers.put_public("a.h5", b"dummy")
    client = DataClient("renate-od")
    with pytest.raises(TransferError, match="is not on the private server"):
        client.get("a.h5", private=True)
    assert servers.public_requests() == 0
    assert not client.local_path("a.h5", private=False).exists()


def test_private_true_raises_without_access(servers):
    write_config(
        host="127.0.0.1",
        port=servers.sftp.port,
        host_keys=_config.settings()["host_keys"],
        public_url=servers.http.url("/~data"),
    )
    servers.put_public("a.h5")
    with pytest.raises(AuthError, match="no SSH key found"):
        DataClient("renate-od").get("a.h5", private=True)
    assert servers.public_requests() == 0


def test_private_true_downloads_private_data(servers):
    servers.put_private("a.h5", b"real")
    assert DataClient("renate-od").get("a.h5", private=True).read_bytes() == b"real"


def test_private_false_never_contacts_the_private_server(servers, capsys):
    servers.put_private("a.h5", b"real")
    servers.put_public("a.h5", b"dummy")
    client = DataClient("renate-od")
    path = client.get("a.h5", private=False)
    assert path == client.local_path("a.h5", private=False)
    assert path.read_bytes() == b"dummy"
    assert servers.sftp.connections == 0
    plasmasds_utility.show_public_fallbacks()
    assert "no public fallbacks" in capsys.readouterr().out  # asked for, not a fallback


def test_private_false_ignores_a_local_private_copy(servers):
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"real")
    servers.put_public("a.h5", b"dummy")
    assert client.get("a.h5", private=False).read_bytes() == b"dummy"


def test_private_false_reports_a_missing_public_file(servers):
    with pytest.raises(TransferError, match="not on the public server") as error:
        DataClient("renate-od").get("a.h5", private=False)
    assert "private server" not in str(error.value)


def test_without_a_key_public_data_is_used_and_ssh_not_retried(servers, caplog):
    write_config(
        host="127.0.0.1",
        port=servers.sftp.port,
        host_keys=_config.settings()["host_keys"],
        public_url=servers.http.url("/~data"),
    )
    servers.put_private("a.h5")
    servers.put_public("a.h5", b"dummy")
    servers.put_public("b.h5", b"dummy")
    client = DataClient("renate-od")
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        assert client.get("a.h5").read_bytes() == b"dummy"
        assert client.get("b.h5").read_bytes() == b"dummy"
    assert servers.sftp.connections == 1  # the failure is remembered
    assert "no SSH key found" in caplog.text


def test_private_server_failure_is_not_hidden_by_public_data(servers, monkeypatch):
    monkeypatch.setattr(_sftp, "time", types.SimpleNamespace(sleep=lambda s: None))
    servers.sftp.fail_connections = 3
    servers.put_public("a.h5", b"dummy")
    with pytest.raises(TransferError, match="after 3 attempts"):
        DataClient("renate-od").get("a.h5")
    assert servers.public_requests() == 0


def test_get_reports_a_file_on_neither_server(servers):
    with pytest.raises(TransferError, match="cannot get 'a.h5'") as error:
        DataClient("renate-od").get("a.h5")
    assert "not on the private server" in str(error.value)
    assert "not on the public server" in str(error.value)


def test_get_refuses_a_directory_in_the_private_copy(servers):
    client = DataClient("renate-od")
    client.local_path("a.h5").mkdir(parents=True)
    with pytest.raises(PathError, match="it is not a file"):
        client.get("a.h5")
    assert servers.sftp.connections == 0
