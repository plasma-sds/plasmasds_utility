"""DataClient with PLASMASDS_SERVER_ACCESS set to public or local (#34)."""

import pytest
from conftest import write_config

import plasmasds_utility
import plasmasds_utility.client as client_module
from plasmasds_utility import AuthError, DataClient, TransferError, _config, _https


@pytest.fixture
def servers(sftp_server, http_server, tmp_path):
    """Both test servers hold a.h5: the private one b"real", the public b"dummy"."""
    private = tmp_path / "server" / "private_html" / "renate-od"
    private.mkdir(parents=True)
    (private / "a.h5").write_bytes(b"real")
    http_server.serve(
        "/~data/renate-od/a.h5",
        {"body": b"dummy", "last_modified": "Fri, 23 Feb 2018 21:48:01 GMT"},
    )
    settings = _config.settings()
    write_config(
        host="127.0.0.1",
        port=sftp_server.port,
        host_keys=settings["host_keys"],
        ssh_key=settings["ssh_key"],
        public_url=http_server.url("/~data"),
    )
    return sftp_server, http_server


def requests(http):
    return sum(http.requests.values())


def place(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", "public")


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", "local")


# public


def test_public_get_takes_public_data_without_sftp_and_says_so(servers, public):
    sftp, http = servers
    path = DataClient("renate-od").get("a.h5")
    assert path.read_bytes() == b"dummy"
    assert sftp.connections == 0
    # Public data instead of private data: announced, as any fallback is.
    assert client_module._fallbacks == {
        ("renate-od", "a.h5"): _config.server_access_note()
    }


def test_public_uses_a_local_private_copy_first(servers, public):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    assert client.get("a.h5").read_bytes() == b"mine"
    assert sftp.connections == 0
    assert requests(http) == 0


def test_ci_makes_get_public(servers, monkeypatch):
    sftp, http = servers
    monkeypatch.setenv("CI", "true")
    assert DataClient("renate-od").get("a.h5").read_bytes() == b"dummy"
    assert sftp.connections == 0


def test_public_refuses_private_only_without_connecting(servers, public):
    sftp, http = servers
    with pytest.raises(AuthError, match="PLASMASDS_SERVER_ACCESS=public"):
        DataClient("renate-od").get("a.h5", private=True)
    assert sftp.connections == 0


def test_public_refusal_is_not_remembered(servers, public, monkeypatch):
    sftp, http = servers
    with pytest.raises(AuthError):
        DataClient("renate-od").get("a.h5", private=True)
    monkeypatch.delenv("PLASMASDS_SERVER_ACCESS")
    assert DataClient("renate-od").get("a.h5", private=True).read_bytes() == b"real"


def test_public_uses_a_local_private_copy_asked_for(servers, public):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    assert client.get("a.h5", private=True).read_bytes() == b"mine"
    assert sftp.connections == 0


def test_public_refuses_uploads(servers, public):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("b.h5", private=False), b"new")
    with pytest.raises(AuthError, match="server access is public"):
        client.upload("b.h5", private=False)
    assert sftp.connections == 0


def test_public_check_updates_reports_private_data_without_failing(
    servers, public, capsys
):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    place(client.local_path("a.h5", private=False), b"dummy")
    assert client.check_updates() == []
    assert "1 private file(s) not checked (server access is public" in (
        capsys.readouterr().err
    )
    assert sftp.connections == 0
    assert http.requests["HEAD /~data/renate-od/a.h5"] == 1


def test_public_check_updates_of_public_data_only(servers, public):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5", private=False), b"dummy")
    assert client.check_updates() == []
    assert sftp.connections == 0


def test_public_notice_and_connection_check(servers, public, capsys):
    sftp, http = servers
    DataClient("renate-od").get("a.h5")
    assert "Here, though, the private server is not contacted" in (
        capsys.readouterr().err
    )
    plasmasds_utility.check_connection()
    out = capsys.readouterr().out
    assert "  server access: public (PLASMASDS_SERVER_ACCESS=public; unset it" in out
    assert "private data (SFTP): not tried (server access is public)" in out
    assert sftp.connections == 0


# local


def test_local_returns_local_copies_without_any_server(servers, local):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    place(client.local_path("b.h5", private=False), b"open")
    assert client.get("a.h5").read_bytes() == b"mine"
    assert client.get("b.h5").read_bytes() == b"open"
    assert client.get("b.h5", private=False).read_bytes() == b"open"
    assert sftp.connections == 0
    assert requests(http) == 0


def test_local_missing_file_raises_at_once(servers, local):
    sftp, http = servers
    with pytest.raises(TransferError, match="not on disk, and no server"):
        DataClient("renate-od").get("a.h5")
    assert sftp.connections == 0
    assert requests(http) == 0


def test_local_private_only_ignores_a_public_copy(servers, local):
    client = DataClient("renate-od")
    place(client.local_path("a.h5", private=False), b"open")
    with pytest.raises(TransferError, match="not on disk"):
        client.get("a.h5", private=True)


@pytest.mark.parametrize("update", ["if_newer", "force"])
def test_local_refuses_updates(servers, local, update):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    with pytest.raises(TransferError, match=f"update={update!r}"):
        client.get("a.h5", update=update)
    assert requests(http) == 0


def test_local_refuses_check_updates_and_uploads(servers, local):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    with pytest.raises(TransferError, match="no server is contacted"):
        client.check_updates()
    with pytest.raises(AuthError, match="server access is local"):
        client.upload("a.h5")
    assert sftp.connections == 0
    assert requests(http) == 0


def test_local_connection_check_tries_nothing(servers, local, capsys):
    sftp, http = servers
    plasmasds_utility.check_connection()
    out = capsys.readouterr().out
    assert "public data (HTTPS): not tried (server access is local)" in out
    assert "private data (SFTP): not tried (server access is local)" in out
    assert sftp.connections == 0
    assert requests(http) == 0


def test_local_notice_says_nothing_is_downloaded(servers, local, capsys):
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    client.get("a.h5")
    assert "no server is contacted, so a file not on disk raises" in (
        capsys.readouterr().err
    )


def test_public_missing_file_says_why_private_was_skipped(servers, public):
    sftp, http = servers
    with pytest.raises(TransferError) as error:
        DataClient("renate-od").get("b.h5")
    message = str(error.value)
    assert "not from the private server (server access is public" in message
    assert "not from the public server" in message
    assert client_module._fallbacks == {}
    assert sftp.connections == 0


def test_public_refusal_makes_no_folder(servers, public):
    client = DataClient("renate-od")
    with pytest.raises(AuthError):
        client.get("deep/inside/a.h5", private=True)
    assert not (client.client_dir() / "private").exists()


def test_local_transports_refuse_on_their_own(servers, local):
    # get() and the others decide first; the transports refuse anyway.
    sftp, http = servers
    url = http.url("/~data/renate-od/a.h5")
    with pytest.raises(TransferError, match="not contacting the public server"):
        _https.download(url, DataClient("renate-od").client_dir() / "x.h5")
    with pytest.raises(TransferError, match="not contacting the public server"):
        _https.head(url)
    assert requests(http) == 0


def test_public_update_of_a_private_copy_keeps_it_and_says_why(servers, public):
    sftp, http = servers
    client = DataClient("renate-od")
    place(client.local_path("a.h5"), b"mine")
    with pytest.raises(AuthError, match="kept the local copy .* server access is"):
        client.get("a.h5", private=True, update="if_newer")
    assert client.local_path("a.h5").read_bytes() == b"mine"
    assert sftp.connections == 0
