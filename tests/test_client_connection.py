import paramiko
import pytest
from conftest import FakeAgent, write_config

import plasmasds_utility
from plasmasds_utility import AuthError, ConfigError, _config, _sftp, client

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


def check(capsys):
    """Run the check; return its report as a dict of line starts to the rest."""
    assert plasmasds_utility.check_connection() is None
    out = capsys.readouterr().out
    return out, {
        line.split(": ", 1)[0].strip(): line.split(": ", 1)[1]
        for line in out.splitlines()[1:]
    }


def test_both_ways_work(servers, home, capsys):
    sftp, http, configure = servers
    out, report = check(capsys)
    assert out.startswith(
        f"plasmasds_utility {plasmasds_utility.__version__} connection check"
    )
    assert report["public data (HTTPS)"] == "ok"
    assert report["private data (SFTP)"] == "ok"
    assert report["server"].startswith(f"data@127.0.0.1, port {sftp.port}")
    assert report["SSH key"] == str(sftp.key_file)
    assert report["data folder"] == str(_config.default_data_dir())
    assert report["log file"] == str(_config.log_dir() / _config.LOG_FILE)
    # No client folder appeared. (On Windows the data folder also holds the
    # configuration and the log, so the folder itself exists.)
    assert not (_config.default_data_dir() / client._CONNECTION_TEST[0]).exists()
    assert list(home.rglob("*.part")) == []


def test_report_is_logged(servers, home, capsys):
    check(capsys)
    log = (_config.log_dir() / _config.LOG_FILE).read_text(encoding="utf-8")
    assert "private data (SFTP): ok" in log


def test_settings_in_the_report(servers, home, capsys):
    sftp, http, configure = servers
    configure(ssh_key="", working_dirs={"renate-od": str(home / "renate")})
    out, report = check(capsys)
    assert report["SSH key"] == "none saved (the SSH agent and ~/.ssh/id_* keys)"
    assert report["working folder of renate-od"] == str(home / "renate")


def test_data_dir_variable_hides_working_folders(servers, home, monkeypatch, capsys):
    sftp, http, configure = servers
    configure(working_dirs={"renate-od": str(home / "renate")})
    monkeypatch.setenv("PLASMASDS_DATA_DIR", str(home / "data"))
    out, report = check(capsys)
    assert report["data folder"] == f"{home / 'data'} (from PLASMASDS_DATA_DIR)"
    assert report["working folder of renate-od"] == (
        f"{home / 'renate'} (not used: PLASMASDS_DATA_DIR is set)"
    )


def test_missing_test_file_is_not_a_connection_failure(servers, home, capsys):
    sftp, http, configure = servers
    http.routes.clear()
    private = home.parent / "server" / "private_html" / "plasmasds_utility" / "test"
    (private / "connection_test.txt").unlink()
    out, report = check(capsys)
    for way in ("public data (HTTPS)", "private data (SFTP)"):
        assert report[way].startswith("the server was reached, but the test file")
        assert "tell the server's admins" in report[way]


def test_unexpected_content_is_reported(servers, home, capsys):
    sftp, http, configure = servers
    http.serve(PATH, {"body": b"something else"})
    out, report = check(capsys)
    assert "unexpected content" in report["public data (HTTPS)"]
    assert report["private data (SFTP)"] == "ok"


def test_no_key_is_reported(servers, home, capsys):
    sftp, http, configure = servers
    configure(ssh_key="")
    out, report = check(capsys)
    assert report["public data (HTTPS)"] == "ok"
    assert report["private data (SFTP)"].startswith("failed: no SSH key found")


def test_missing_key_file_is_reported_not_raised(servers, home, capsys):
    sftp, http, configure = servers
    configure(ssh_key=str(home / "gone"))
    out, report = check(capsys)
    assert "does not exist" in report["private data (SFTP)"]


def test_rejected_key_is_reported(servers, home, capsys):
    sftp, http, configure = servers
    other = home / ".ssh" / "other_key"
    paramiko.RSAKey.generate(1024).write_private_key_file(str(other))
    configure(ssh_key=str(other))
    out, report = check(capsys)
    assert "SSH login as data@" in report["private data (SFTP)"]


def test_wrong_host_key_is_reported(servers, home, capsys):
    sftp, http, configure = servers
    wrong = paramiko.RSAKey.generate(1024)
    configure(host_keys=[f"{wrong.get_name()} {wrong.get_base64()}"])
    out, report = check(capsys)
    assert "does not match" in report["private data (SFTP)"]


def test_a_remembered_failure_is_tested_again(
    servers, home, ssh_keys, monkeypatch, capsys
):
    sftp, http, configure = servers
    configure(ssh_key="")
    with pytest.raises(AuthError):  # no key: remembered until Python restarts
        _sftp.stat(_config.settings(), "x")
    monkeypatch.setattr(FakeAgent, "keys", [ssh_keys[1]])  # as after ssh-add
    connections = sftp.connections
    out, report = check(capsys)
    assert report["private data (SFTP)"] == "ok"
    assert sftp.connections == connections + 1


def test_each_way_is_tried_once_on_a_fresh_connection(servers, home, capsys):
    sftp, http, configure = servers
    _sftp.stat(_config.settings(), "private_html")  # an open session
    http.serve(PATH, {"body": b"", "status": 500})
    sftp.fail_connections = 1
    out, report = check(capsys)
    assert "after 1 attempt:" in report["public data (HTTPS)"]
    assert "after 1 attempt:" in report["private data (SFTP)"]
    assert http.requests[PATH] == 1
    assert sftp.connections == 2  # the open session was not reused


def test_invalid_configuration_raises(home):
    path = _config.config_dir() / _config.CONFIG_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    _config._settings = None
    with pytest.raises(ConfigError):
        plasmasds_utility.check_connection()
