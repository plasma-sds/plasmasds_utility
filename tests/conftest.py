"""Fixtures shared by all tests.

pytest loads a file named exactly ``conftest.py`` automatically and makes its fixtures
available to every test in this folder, so this file must keep its name.

The ``home`` fixture is ``autouse``: it runs for every test, even those that do not ask
for it, and keeps the whole suite away from the real home, config and log folders.
The ``http_server`` fixture is a local web server whose answers each test scripts, and
``sftp_server`` a local SFTP server configured as the private data server.
"""

import collections
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paramiko
import pytest
from _sftp_server import StubSFTPServer

from plasmasds_utility import _config, _sftp, client


class FakeServer:
    """Script the answers of a local HTTP server and count the requests it gets.

    ``serve(path, *responses)`` sets the answers for a path, one per request; the
    last one repeats. A response is a dict with ``body`` (bytes), and optionally
    ``status`` (default 200), ``last_modified`` (header value), ``length`` (the
    Content-Length to claim, default the body length) and ``stall`` (seconds to
    wait before answering). Unknown paths answer 404.
    """

    def __init__(self, port):
        self.port = port
        self.routes = {}
        self.requests = collections.Counter()

    def serve(self, path, *responses):
        self.routes[path] = list(responses)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes the connection after each answer, so a truncated body ends
    # in end-of-file instead of waiting for the rest until the timeout.
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        fake = self.server.fake
        fake.requests[self.path] += 1
        responses = fake.routes.get(self.path)
        if not responses:
            self.send_error(404)
            return
        response = responses.pop(0) if len(responses) > 1 else responses[0]
        time.sleep(response.get("stall", 0))
        status = response.get("status", 200)
        if status != 200:
            self.send_error(status)
            return
        body = response["body"]
        self.send_response(200)
        self.send_header("Content-Length", str(response.get("length", len(body))))
        if response.get("last_modified"):
            self.send_header("Last-Modified", response["last_modified"])
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # keep the test output clean


@pytest.fixture
def http_server(monkeypatch):
    """A local HTTP server on 127.0.0.1, scripted through FakeServer."""
    for variable in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(variable, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.fake = FakeServer(server.server_address[1])
    # A short poll interval keeps shutdown() at teardown fast.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    yield server.fake
    server.shutdown()
    server.server_close()


ENV_VARIABLES = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "PLASMASDS_DATA_DIR",
    "SSH_AUTH_SOCK",  # never use the real SSH agent
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Point every location the package may touch into a temporary home directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    for variable in ENV_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(_config, "_settings", None)
    monkeypatch.setattr(client, "_fallbacks", {})
    yield home
    _sftp.close_all()
    # Close the log file, or Windows cannot delete the temporary directory.
    _config._stop_logging()


@pytest.fixture(scope="session")
def ssh_keys():
    """An RSA host key for the server and an RSA key for the client.

    Generated once per test run, because generating keys is slow; paramiko cannot
    generate ED25519 keys, and the shipped ED25519 key is tested on its own.
    """
    return paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)


def write_config(**settings):
    """Write the user configuration file with these settings."""
    path = _config.config_dir() / _config.CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")
    _config._settings = None


@pytest.fixture
def sftp_server(tmp_path, home, ssh_keys):
    """A local SFTP server, configured as the private data server.

    It serves ``tmp_path / "server"`` as the SSH user's home; the client key is in
    ``home/.ssh/test_key`` and saved as the SSH key, and the server's host key is
    the only trusted one.
    """
    host_key, client_key = ssh_keys
    root = tmp_path / "server"
    root.mkdir()
    stub = StubSFTPServer(root, host_key, client_key)
    key_file = home / ".ssh" / "test_key"
    key_file.parent.mkdir()
    client_key.write_private_key_file(str(key_file))
    write_config(
        host="127.0.0.1",
        port=stub.port,
        host_keys=[f"{host_key.get_name()} {host_key.get_base64()}"],
        ssh_key=str(key_file),
    )
    stub.key_file = key_file
    yield stub
    _sftp.close_all()
    stub.close()
