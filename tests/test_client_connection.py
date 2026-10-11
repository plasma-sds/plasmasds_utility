import paramiko
import pytest
from conftest import write_config

import plasmasds_utility
from plasmasds_utility import ConfigError, _config, client

TEXT = client._CONNECTION_TEXT
PATH = "/~data/plasmasds_utility/test/connection_test.txt"


@pytest.fixture
def servers(sftp_server, http_server, tmp_path):
    """Both test servers, holding the connection-test file; returns a reconfigure."""
    private = tmp_path / "server" / "private_html" / "plasmasds_utility" / "test"
    private.mkdir(parents=True)
    (private / "connection_test.txt").write_bytes(TEXT)
    http_server.serve(PATH, {"body": TEXT})
    settings = _config.settings()

    def configure(**changes):
        write_config(
            **{
                "host": "127.0.0.1",
                "port": sftp_server.port,
                "host_keys": settings["host_keys"],
                "ssh_key": settings["ssh_key"],
                "public_url": http_server.url("/~data"),
                **changes,
            }
        )

    configure()
    return sftp_server, http_server, configure


@pytest.fixture
def no_waiting(monkeypatch):
    monkeypatch.setattr(plasmasds_utility._https.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(plasmasds_utility._sftp.time, "sleep", lambda seconds: None)


def test_both_ways_work(servers, home, capsys):
    sftp, http, configure = servers
    assert plasmasds_utility.check_connection() == {"public": None, "private": None}
    out = capsys.readouterr().out
    assert "public data (HTTPS): ok" in out
    assert "private data (SFTP): ok" in out
    assert f"server: data@127.0.0.1, port {sftp.port}" in out
    assert f"SSH key: {sftp.key_file}" in out
    assert f"data folder: {_config.default_data_dir()}" in out
    # No client folder appeared. (On Windows the data folder also holds the
    # configuration and the log, so the folder itself exists.)
    assert not (_config.default_data_dir() / client._CONNECTION_TEST[0]).exists()
    assert list(home.rglob("*.part")) == []


def test_report_is_logged(servers, home):
    plasmasds_utility.check_connection()
    log = (_config.log_dir() / _config.LOG_FILE).read_text(encoding="utf-8")
    assert "private data (SFTP): ok" in log


def test_settings_in_the_report(servers, home, monkeypatch, capsys):
    sftp, http, configure = servers
    configure(ssh_key="", working_dirs={"renate-od": str(home / "renate")})
    monkeypatch.setenv("PLASMASDS_DATA_DIR", str(home / "data"))
    plasmasds_utility.check_connection()
    out = capsys.readouterr().out
    assert "SSH key: none saved (the SSH agent and ~/.ssh/id_* keys)" in out
    assert f"data folder: {home / 'data'}" in out
    assert f"working folder of renate-od: {home / 'renate'}" in out


def test_missing_public_file_is_reported(servers, home, no_waiting, capsys):
    sftp, http, configure = servers
    http.routes.clear()
    result = plasmasds_utility.check_connection()
    assert result["private"] is None
    assert "not on the public server" in result["public"]
    assert "public data (HTTPS): failed: " in capsys.readouterr().out


def test_unexpected_content_is_reported(servers, home):
    sftp, http, configure = servers
    http.serve(PATH, {"body": b"something else"})
    result = plasmasds_utility.check_connection()
    assert "unexpected content" in result["public"]
    assert result["private"] is None


def test_no_key_is_reported(servers, home, capsys):
    sftp, http, configure = servers
    configure(ssh_key="")
    result = plasmasds_utility.check_connection()
    assert result["public"] is None
    assert "no SSH key found" in result["private"]
    assert "private data (SFTP): failed: no SSH key found" in capsys.readouterr().out


def test_missing_key_file_is_reported_not_raised(servers, home):
    sftp, http, configure = servers
    configure(ssh_key=str(home / "gone"))
    result = plasmasds_utility.check_connection()
    assert "does not exist" in result["private"]


def test_rejected_key_is_reported(servers, home):
    sftp, http, configure = servers
    other = home / ".ssh" / "other_key"
    paramiko.RSAKey.generate(1024).write_private_key_file(str(other))
    configure(ssh_key=str(other))
    assert "SSH login as data@" in plasmasds_utility.check_connection()["private"]


def test_wrong_host_key_is_reported(servers, home):
    sftp, http, configure = servers
    wrong = paramiko.RSAKey.generate(1024)
    configure(host_keys=[f"{wrong.get_name()} {wrong.get_base64()}"])
    assert "does not match" in plasmasds_utility.check_connection()["private"]


def test_each_way_is_tried_once(servers, home):
    sftp, http, configure = servers
    http.serve(PATH, {"body": b"", "status": 500})
    sftp.fail_connections = 1
    result = plasmasds_utility.check_connection()
    assert "after 1 attempts" in result["public"]
    assert "after 1 attempts" in result["private"]
    assert http.requests[PATH] == 1
    assert sftp.connections == 1


def test_invalid_configuration_raises(home):
    path = _config.config_dir() / _config.CONFIG_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    _config._settings = None
    with pytest.raises(ConfigError):
        plasmasds_utility.check_connection()
