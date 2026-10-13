"""DataClient.on_server (#44)."""

import pytest
from conftest import FakeAgent, write_config

from plasmasds_utility import (
    AlreadyOnServer,
    AuthError,
    DataClient,
    PathError,
    TransferError,
    _config,
    _https,
    _sftp,
)


@pytest.fixture
def servers(sftp_server, http_server, tmp_path):
    """Both test servers hold a.h5."""
    private = tmp_path / "server" / "private_html" / "renate-od"
    private.mkdir(parents=True)
    (private / "a.h5").write_bytes(b"real")
    http_server.serve("/~data/renate-od/a.h5", {"body": b"dummy"})
    settings = _config.settings()
    write_config(
        host="127.0.0.1",
        port=sftp_server.port,
        host_keys=settings["host_keys"],
        ssh_key=settings["ssh_key"],
        public_url=http_server.url("/~data"),
    )
    return sftp_server, http_server


@pytest.mark.parametrize("private", [True, False])
def test_present_and_missing(servers, private):
    client = DataClient("renate-od")
    assert client.on_server("a.h5", private=private) is True
    assert client.on_server("b.h5", private=private) is False


def test_downloads_nothing(servers):
    sftp, http = servers
    client = DataClient("renate-od")
    client.on_server("a.h5")
    client.on_server("a.h5", private=False)
    assert not client.local_path("a.h5").exists()
    assert not client.local_path("a.h5", private=False).exists()
    assert http.requests == {"HEAD /~data/renate-od/a.h5": 1}


def test_always_asks_the_server(servers, tmp_path):
    client = DataClient("renate-od")
    assert client.on_server("b.h5") is False
    (tmp_path / "server" / "private_html" / "renate-od" / "b.h5").write_bytes(b"new")
    assert client.on_server("b.h5") is True  # not the remembered "missing"


def test_cannot_tell_is_not_false(servers, monkeypatch):
    monkeypatch.setattr(_https.time, "sleep", lambda seconds: None)
    sftp, http = servers
    http.serve("/~data/renate-od/a.h5", {"body": b"", "status": 500})
    with pytest.raises(TransferError):
        DataClient("renate-od").on_server("a.h5", private=False)


def test_no_key_raises(servers):
    sftp, http = servers
    settings = _config.settings()
    write_config(
        host="127.0.0.1",
        port=sftp.port,
        host_keys=settings["host_keys"],
        ssh_key="",
        public_url=settings["public_url"],
    )
    with pytest.raises(AuthError, match="no SSH key found"):
        DataClient("renate-od").on_server("a.h5")


def test_server_access_modes(servers, monkeypatch):
    sftp, http = servers
    client = DataClient("renate-od")
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", "public")
    with pytest.raises(AuthError, match="server access is public"):
        client.on_server("a.h5")
    assert client.on_server("a.h5", private=False) is True
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", "local")
    with pytest.raises(TransferError, match="server access is local"):
        client.on_server("a.h5", private=False)
    assert sftp.connections == 0


@pytest.mark.parametrize(
    ("key", "private", "error"),
    [("../a.h5", True, PathError), ("a.h5", None, ValueError)],
)
def test_invalid_arguments_before_any_request(servers, key, private, error):
    sftp, http = servers
    with pytest.raises(error):
        DataClient("renate-od").on_server(key, private=private)
    assert sftp.connections == 0
    assert sum(http.requests.values()) == 0


def test_a_folder_is_not_a_file(servers, tmp_path):
    (tmp_path / "server" / "private_html" / "renate-od" / "folder").mkdir()
    assert DataClient("renate-od").on_server("folder") is False


def test_a_false_answer_is_not_remembered(servers, tmp_path):
    client = DataClient("renate-od")
    assert client.on_server("b.h5") is False
    (tmp_path / "server" / "private_html" / "renate-od" / "b.h5").write_bytes(b"new")
    # get() asks the server, rather than raising "not on the private server".
    assert client.get("b.h5", private=True).read_bytes() == b"new"


def test_an_unreachable_sftp_server_raises(servers, monkeypatch):
    monkeypatch.setattr(_sftp.time, "sleep", lambda seconds: None)
    sftp, http = servers
    sftp.fail_connections = 3
    with pytest.raises(TransferError, match="after 3 attempts"):
        DataClient("renate-od").on_server("a.h5")


def test_a_login_failure_is_remembered(servers, ssh_keys, monkeypatch):
    sftp, http = servers
    settings = _config.settings()
    write_config(
        host="127.0.0.1",
        port=sftp.port,
        host_keys=settings["host_keys"],
        ssh_key="",
        public_url=settings["public_url"],
    )
    client = DataClient("renate-od")
    with pytest.raises(AuthError):
        client.on_server("a.h5")
    monkeypatch.setattr(FakeAgent, "keys", [ssh_keys[1]])  # as after ssh-add
    with pytest.raises(AuthError, match="remembered"):
        client.on_server("a.h5")


def test_already_on_server_is_a_transfer_error():
    # Callers that catch TransferError keep working.
    assert issubclass(AlreadyOnServer, TransferError)
