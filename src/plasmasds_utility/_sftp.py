"""Download private files over SFTP, with paramiko.

One SSH session per server (host, port and user) is opened on first use and reused
for the rest of the process.

Host keys: the user's ``~/.ssh/known_hosts`` is checked first, as plain ``ssh`` and
``sftp`` would; the keys shipped in the packaged defaults are used for a server that
file has no entry for. Unknown hosts are rejected, never added.

Login: the key set with ``set_ssh_key``, the keys in the SSH agent, and the standard
``~/.ssh/id_*`` files. The utility never asks for a passphrase.

Failures that retrying cannot fix (a host key mismatch, an unknown host, a rejected or
missing key) raise :class:`AuthError` and are remembered for the rest of the process,
so later calls fail at once instead of trying again.
"""

import atexit
import threading
import time

import paramiko
from paramiko.hostkeys import HostKeyEntry, InvalidHostKey

from plasmasds_utility import _config, _files
from plasmasds_utility.exceptions import AuthError, ConfigError, TransferError

_lock = threading.Lock()
_sessions = {}  # (host, port, user) -> (SSHClient, SFTPClient)
_unavailable = {}  # (host, port, user) -> AuthError


def _server(settings):
    return settings["host"], settings["port"], settings["user"]


def _host_name(host, port):
    """Return the host as known_hosts names it."""
    return host if port == 22 else f"[{host}]:{port}"


def _load_key(path):
    """Load the configured private key, or return None if none is configured.

    Loaded here rather than passed to paramiko as a file name: paramiko then tries
    every key type and reports the last loading error instead of a rejected login.

    Raises
    ------
    AuthError
        If the key has a passphrase.
    ConfigError
        If the file cannot be read as a private key.
    """
    if path is None:
        return None
    try:
        return paramiko.PKey.from_path(str(path))
    except (TypeError, paramiko.PasswordRequiredException) as error:
        if "encrypted" not in str(error) and not isinstance(
            error, paramiko.PasswordRequiredException
        ):
            raise
        raise AuthError(
            f"the SSH key {path} has a passphrase; load it into the SSH agent "
            "(ssh-add), because plasmasds_utility never asks for one"
        ) from error
    except (ValueError, paramiko.SSHException, OSError) as error:
        raise ConfigError(
            f"cannot read {path} as an SSH private key: {error}"
        ) from error


def _connect(settings, timeout):
    """Open an SSH session and an SFTP channel; raise paramiko or socket errors."""
    host, port, user = _server(settings)
    name = _host_name(host, port)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    for line in settings["host_keys"]:
        try:
            entry = HostKeyEntry.from_line(f"{name} {line}")
        except InvalidHostKey:
            entry = None
        if entry is None or entry.key is None:
            raise ConfigError(f"invalid entry in host_keys: {line!r}")
        client.get_host_keys().add(name, entry.key.get_name(), entry.key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    key = _load_key(_config.ssh_key())
    try:
        client.connect(
            host,
            port=port,
            username=user,
            pkey=key,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            channel_timeout=timeout,
            allow_agent=True,
            look_for_keys=True,
        )
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(timeout)
    except BaseException:
        client.close()
        raise
    return client, sftp


def _auth_error(error, settings):
    """Return an AuthError for a failure that retrying cannot fix, else None."""
    host, port, user = _server(settings)
    name = _host_name(host, port)
    if isinstance(error, paramiko.BadHostKeyException):
        return AuthError(
            f"the host key of {name} does not match the trusted one (from "
            "~/.ssh/known_hosts, or else the key shipped with plasmasds_utility). "
            "If the server was reinstalled, remove its line from ~/.ssh/known_hosts; "
            "otherwise do not connect: someone may be impersonating the server"
        )
    if isinstance(error, paramiko.AuthenticationException):
        return AuthError(
            f"SSH login as {user}@{name} failed ({error}); check the key set with "
            "plasmasds_utility.set_ssh_key(), or load a key with a passphrase into "
            "the SSH agent"
        )
    if isinstance(error, paramiko.SSHException):
        message = str(error)
        if "No authentication methods available" in message:
            return AuthError(
                f"no SSH key found for {user}@{name}: set one with "
                "plasmasds_utility.set_ssh_key(), or load it into the SSH agent"
            )
        if "not found in known_hosts" in message:
            return AuthError(
                f"{name} is not a known host: it is neither in ~/.ssh/known_hosts nor "
                "among the keys shipped with plasmasds_utility, so it is rejected"
            )
    return None


def _close(server):
    """Close and forget the session of one server, if any."""
    session = _sessions.pop(server, None)
    if session is not None:
        session[0].close()


def close_all():
    """Close every open session and forget remembered failures."""
    with _lock:
        for server in list(_sessions):
            _close(server)
        _unavailable.clear()


atexit.register(close_all)


def download(settings, remote, target, *, timeout=30, attempts=3, backoff=1.0):
    """Download a private file over SFTP to target.

    The local file gets the server's modification time.

    Parameters
    ----------
    settings : dict
        The merged settings; ``host``, ``port``, ``user`` and ``host_keys`` are used.
    remote : pathlib.PurePosixPath
        The file on the server (see :func:`plasmasds_utility._paths.private_remote`).
    target : pathlib.Path
        Where to store it; missing directories are created, and an existing file is
        replaced only when the download is complete.
    timeout : float, default 30
        Seconds to wait for the connection, the login and each read.
    attempts : int, default 3
        How many times to try. Connection errors, timeouts and truncated transfers
        are retried with a new session; the failures below are not.
    backoff : float, default 1.0
        Seconds to wait before the second attempt; the wait doubles after each one.

    Returns
    -------
    pathlib.Path
        ``target``.

    Raises
    ------
    AuthError
        If the host key does not match, the host is unknown, or the login fails or
        no key is found. Remembered: later calls for the same server raise it again
        without connecting.
    TransferError
        If the file is not on the server, access to it is denied, or the download
        still fails after the last attempt.
    ConfigError
        If the configured SSH key is missing, or a host key setting is invalid.
    PathError
        If the target directory cannot be created.
    """
    server = _server(settings)
    _files.make_parent(target)
    for attempt in range(1, attempts + 1):
        with _lock:
            if server in _unavailable:
                raise _unavailable[server]
            try:
                if server not in _sessions:
                    _sessions[server] = _connect(settings, timeout)
                _fetch(_sessions[server][1], remote, target)
            except (paramiko.SSHException, OSError, EOFError) as error:
                auth_error = _auth_error(error, settings)
                if auth_error is not None:
                    _close(server)
                    _unavailable[server] = auth_error
                    raise auth_error from error
                _close(server)
                last_error = error
            else:
                _config.logger.info("downloaded %s to %s", remote, target)
                return target
        _config.logger.warning(
            "download of %s failed (attempt %d of %d): %s",
            remote,
            attempt,
            attempts,
            last_error,
        )
        if attempt < attempts:
            time.sleep(backoff * 2 ** (attempt - 1))
    raise TransferError(
        f"cannot download {remote} from {server[0]} after {attempts} attempts: "
        f"{last_error}"
    ) from last_error


def _fetch(sftp, remote, target):
    """Download one file over an open SFTP channel."""
    try:
        attributes = sftp.stat(str(remote))
    except FileNotFoundError as error:
        raise TransferError(f"{remote} is not on the private server") from error
    except PermissionError as error:
        raise TransferError(
            f"access to {remote} is denied on the private server"
        ) from error
    with _files.writing(target) as partial:
        received = sftp.getfo(str(remote), partial.file)
        if received != attributes.st_size:
            raise ConnectionError(
                f"transfer cut short: received {received} of {attributes.st_size} bytes"
            )
        partial.mtime = attributes.st_mtime
