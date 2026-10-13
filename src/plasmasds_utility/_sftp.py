"""Download private files and upload files over SFTP, with paramiko.

One SSH session per server (host, port and user) is opened on first use and reused
for the rest of the process.

Host keys: the user's ``~/.ssh/known_hosts`` is checked first, as plain ``ssh`` and
``sftp`` would; the keys shipped in the packaged defaults are used for a server that
file has no entry for. Unknown hosts are rejected, never added.

Login: the key set with ``set_ssh_key``, the keys in the SSH agent, and the standard
``~/.ssh/id_*`` files. The utility never asks for a passphrase: a saved key that has
one is used through the SSH agent.

Failures that retrying cannot fix (a host key mismatch, an unknown host, a rejected or
missing key, a key with a passphrase that is not in the agent) raise
:class:`AuthError` and are remembered until Python (or the Python kernel) restarts, so
later calls fail at once instead of trying again; the messages say so. A file found
missing on the server is remembered the same way, so asking again costs nothing.

Uploads never replace a file on the server unless it is archived under another name
first, and readers on the server always see either the complete old file or the
complete new one.

One SFTP transfer runs at a time per process; parallel calls take turns.
"""

import atexit
import errno
import os
import secrets
import stat as stat_module
import threading
import time

import paramiko
from paramiko.hostkeys import HostKeyEntry, InvalidHostKey
from paramiko.sftp import CMD_EXTENDED

from plasmasds_utility import _config, _files
from plasmasds_utility.exceptions import (
    AuthError,
    ConfigError,
    PathError,
    PlasmasdsUtilityError,
    TransferError,
)


class NotOnServer(TransferError):
    """The file is not on the private server (a TransferError that is not retried)."""


_lock = threading.Lock()
_sessions = {}  # (host, port, user) -> (SSHClient, SFTPClient)
_unavailable = {}  # (host, port, user) -> AuthError
_not_on_server = set()  # ((host, port, user), remote path) known to be missing
_KEEPALIVE = 60  # seconds between SSH keepalive messages on an idle session
# Read requests a download keeps in flight (32 kB each). paramiko sends one for every
# block of a file at once by default: about 43,000 for a 1.4 GB file. 64 requests are
# 2 MB, paramiko's default SSH window, so the bound is never tighter than the flow
# control already in place and costs no speed.
_PREFETCH = 64


def _server(settings):
    return settings["host"], settings["port"], settings["user"]


def _host_name(host, port):
    """Return the host as known_hosts names it."""
    return host if port == 22 else f"[{host}]:{port}"


def _load_key(path):
    """Load the configured private key.

    Loaded here rather than passed to paramiko as a file name: paramiko then tries
    every key type and reports the last loading error instead of a rejected login.

    Returns
    -------
    tuple
        ``(key, locked)``: the loaded key, or None if none is configured or the
        key has a passphrase (the SSH agent is then used for it), and whether it
        has a passphrase.

    Raises
    ------
    ConfigError
        If the file cannot be read as a private key.
    """
    if path is None:
        return None, False
    try:
        return paramiko.PKey.from_path(str(path)), False
    except (TypeError, paramiko.PasswordRequiredException) as error:
        if "encrypted" not in str(error) and not isinstance(
            error, paramiko.PasswordRequiredException
        ):
            raise ConfigError(
                f"cannot read {path} as an SSH private key: {error}"
            ) from error
        _config.logger.info(
            "the SSH key %s has a passphrase; using the SSH agent for it", path
        )
        return None, True
    except (
        ValueError,
        paramiko.SSHException,
        paramiko.UnknownKeyType,
        OSError,
    ) as error:
        # UnknownKeyType: a key type paramiko does not support, such as DSA.
        raise ConfigError(
            f"cannot read {path} as an SSH private key: {error}"
        ) from error


def _connect(settings, timeout, key):
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
        # Keepalives stop a firewall from silently dropping an idle session.
        client.get_transport().set_keepalive(_KEEPALIVE)
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(timeout)
    except BaseException:
        client.close()
        raise
    return client, sftp


_RESTART = (
    "then restart Python (or the Python kernel), because this failure is "
    "remembered until then"
)  # follows "and"; set_ssh_key() needs no restart, as it forgets failures


def _auth_error(error, settings, locked_key):
    """Return an AuthError for a failure that retrying cannot fix, else None.

    ``locked_key`` is the configured key file if it has a passphrase, else None.
    """
    host, port, user = _server(settings)
    name = _host_name(host, port)
    if isinstance(error, paramiko.BadHostKeyException):
        return AuthError(
            f"the host key of {name} does not match the trusted one (from "
            "~/.ssh/known_hosts, or else the key shipped with plasmasds_utility). "
            "If the server was reinstalled, remove its line from ~/.ssh/known_hosts "
            f"and {_RESTART}; otherwise do not connect: someone may be "
            "impersonating the server"
        )
    # paramiko signals "no key at all" and "unknown host" only with a plain
    # SSHException, so these two cases are told apart by its message text; the
    # tests would catch a change of wording in a new paramiko release.
    login_failed = isinstance(error, paramiko.AuthenticationException) or (
        isinstance(error, paramiko.SSHException)
        and "No authentication methods available" in str(error)
    )
    if login_failed and locked_key is not None:
        return AuthError(
            f"SSH login as {user}@{name} failed: the SSH key {locked_key} has a "
            "passphrase and the SSH agent could not log in with it; load it with "
            f"ssh-add, {_RESTART}"
        )
    if isinstance(error, paramiko.AuthenticationException):
        return AuthError(
            f"SSH login as {user}@{name} failed ({error}); set another key with "
            "plasmasds_utility.set_ssh_key(), which takes effect at once, or load "
            f"one into the SSH agent with ssh-add and {_RESTART}"
        )
    if login_failed:
        return AuthError(
            f"no SSH key found for {user}@{name}: set one with "
            "plasmasds_utility.set_ssh_key(), which takes effect at once, or load "
            f"one into the SSH agent with ssh-add and {_RESTART}"
        )
    if isinstance(error, paramiko.SSHException) and "not found in known_hosts" in str(
        error
    ):
        return AuthError(
            f"{name} is not a known host: it is neither in ~/.ssh/known_hosts nor "
            f"among the keys shipped with plasmasds_utility, so it is rejected; "
            f"if this is wrong, fix the host keys and {_RESTART}"
        )
    return None


def _alive(client):
    """Return whether an SSH session is still open."""
    transport = client.get_transport()
    return transport is not None and transport.is_active()


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
        _not_on_server.clear()


atexit.register(close_all)


def forget(settings):
    """Close the session of one server and forget its remembered login failure.

    The next request connects afresh; used by the connection check, which must
    test the connection as it is now.
    """
    server = _server(settings)
    with _lock:
        _close(server)
        _unavailable.pop(server, None)


def download(
    settings, remote, target, *, use_cache=True, timeout=30, attempts=3, backoff=1.0
):
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
    use_cache : bool, default True
        False asks the server again for a file remembered as missing.
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
        no key is found. Remembered until Python restarts: later calls for the
        same server raise it again without connecting.
    TransferError
        If the file is not on the server (``NotOnServer``; remembered until Python
        restarts), access to it is denied, or the download still fails after the
        last attempt.
    ConfigError
        If the configured SSH key is missing, or a host key setting is invalid.
    PathError
        If the target directory cannot be created or the file cannot be written.
    ValueError
        If ``attempts`` is less than 1.
    """
    _files.make_parent(target)
    _run(
        settings,
        remote,
        lambda sftp: _fetch(sftp, remote, target),
        "download",
        timeout=timeout,
        attempts=attempts,
        backoff=backoff,
        use_cache=use_cache,
    )
    _config.logger.info("downloaded %s to %s", remote, target)
    return target


def _run(settings, remote, action, verb, *, timeout, attempts, backoff, use_cache):
    """Call action(sftp) on a session for settings, with bounded retries.

    Opens or reuses the session, raises remembered failures at once, remembers
    new AuthErrors and NotOnServer, and retries other connection failures with a
    new session. With ``use_cache`` false, a file remembered as missing is asked
    for again. ``verb`` names the operation in messages. Returns the result of
    action.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    server = _server(settings)
    for attempt in range(1, attempts + 1):
        with _lock:
            if server in _unavailable:
                # A fresh traceback each time, or the cached one grows per call.
                raise _unavailable[server].with_traceback(None)
            if use_cache and (server, str(remote)) in _not_on_server:
                raise NotOnServer(f"{remote} is not on the private server")
            locked_key = None
            if server in _sessions and not _alive(_sessions[server][0]):
                _close(server)  # closed by the server: reconnect without a warning
            try:
                if server not in _sessions:
                    key_path = _config.ssh_key()
                    key, locked = _load_key(key_path)
                    locked_key = key_path if locked else None
                    _sessions[server] = _connect(settings, timeout, key)
                result = action(_sessions[server][1])
                _not_on_server.discard((server, str(remote)))
                return result
            except NotOnServer:
                _not_on_server.add((server, str(remote)))
                raise
            except (paramiko.SSHException, OSError, EOFError) as error:
                auth_error = _auth_error(error, settings, locked_key)
                if auth_error is not None:
                    _close(server)
                    _unavailable[server] = auth_error
                    raise auth_error from error
                _close(server)
                last_error = error
            except PlasmasdsUtilityError:
                raise  # a refusal: the session is fine
            except BaseException:
                # Interrupted (Ctrl-C, say) in the middle of a request: the channel
                # may be out of step, so the next call starts a new session.
                _close(server)
                raise
        _config.logger.warning(
            "%s of %s failed (attempt %d of %d): %s",
            verb,
            remote,
            attempt,
            attempts,
            last_error,
        )
        if attempt < attempts:
            time.sleep(backoff * 2 ** (attempt - 1))
    raise TransferError(
        f"cannot {verb} {remote} on {server[0]} after {attempts} "
        f"attempt{'s' if attempts > 1 else ''}: {last_error}"
    ) from last_error


def listdir(settings, remote_dir, *, timeout=30, attempts=3, backoff=1.0):
    """Return the size and modification time of every file in a server directory.

    One request for the whole directory, so checking many files in one directory
    costs one round trip instead of one per file. The server is always asked,
    even if the directory was found missing before. Only regular files are
    returned: subdirectories and broken links are left out, and a symbolic link
    is described by its target (one extra request each), as :func:`stat` does.

    Parameters
    ----------
    settings : dict
        The merged settings, as for :func:`download`.
    remote_dir : pathlib.PurePosixPath
        The directory on the server.
    timeout, attempts, backoff
        As for :func:`download`.

    Returns
    -------
    dict
        Maps each entry name to ``(size, mtime)``.

    Raises
    ------
    AuthError, TransferError, ConfigError, ValueError
        As for :func:`download`; a missing directory raises ``NotOnServer``.
    """

    def ask(sftp):
        try:
            entries = sftp.listdir_attr(str(remote_dir))
        except OSError as error:
            if _server_file_error(remote_dir, error) is None:
                raise
            raise _server_file_error(remote_dir, error) from error
        files = {}
        for entry in entries:
            name, attributes = entry.filename, entry
            if attributes.st_mode is not None and stat_module.S_ISLNK(
                attributes.st_mode
            ):
                # A listing describes the link itself; compare its target, as
                # get() does.
                try:
                    attributes = sftp.stat(f"{remote_dir}/{name}")
                except OSError:
                    continue  # a broken link is not a file
            mode = attributes.st_mode
            if mode is None or stat_module.S_ISREG(mode):
                files[name] = (attributes.st_size, attributes.st_mtime)
        return files

    return _run(
        settings,
        remote_dir,
        ask,
        "list",
        timeout=timeout,
        attempts=attempts,
        backoff=backoff,
        use_cache=False,
    )


def stat(settings, remote, *, use_cache=True, timeout=30, attempts=3, backoff=1.0):
    """Return the size and modification time of a private file on the server.

    Parameters
    ----------
    settings, remote, use_cache, timeout, attempts, backoff
        As for :func:`download`.

    Returns
    -------
    tuple
        ``(size, mtime)``: the size in bytes and the POSIX modification time.

    Raises
    ------
    AuthError, TransferError, ConfigError, ValueError
        As for :func:`download`.
    """

    def ask(sftp):
        try:
            attributes = sftp.stat(str(remote))
        except OSError as error:
            if _server_file_error(remote, error) is None:
                raise
            raise _server_file_error(remote, error) from error
        return attributes.st_size, attributes.st_mtime

    return _run(
        settings,
        remote,
        ask,
        "check",
        timeout=timeout,
        attempts=attempts,
        backoff=backoff,
        use_cache=use_cache,
    )


_MODE = 0o644  # uploaded files: readable by everyone, so the web server can serve them
_DIRECTORY_MODE = 0o755
_ERRORS = (paramiko.SSHException, OSError, EOFError)  # what a failed request raises


def upload(
    settings,
    source,
    remote,
    *,
    archive_to=None,
    timeout=30,
    attempts=3,
    backoff=1.0,
):
    """Upload a local file to the server, never replacing a file unless archiving it.

    The file is uploaded to a temporary name next to ``remote``
    (``.<name>.<random>.part``), its size is confirmed and its mode set to 0644,
    and only then is it renamed to ``remote``. Missing directories are created with
    mode 0755.

    Without ``archive_to``, an existing ``remote`` is refused, also one that appears
    during the upload. With ``archive_to``, an existing ``remote`` is first given
    that second name (a hard link, so the file itself is not touched), and the new
    file then replaces ``remote`` in one atomic rename. If there was nothing to
    archive, the file is uploaded with a warning.

    Parameters
    ----------
    settings : dict
        The merged settings, as for :func:`download`.
    source : pathlib.Path
        The local file.
    remote : pathlib.PurePosixPath
        Where to put it on the server.
    archive_to : pathlib.PurePosixPath, optional
        Where to keep an existing ``remote``; must not exist.
    timeout, attempts, backoff
        As for :func:`download`. Only the upload to the temporary name is retried;
        archiving and renaming are tried once, because after a lost connection a
        retry could not tell whether they had happened.

    Returns
    -------
    int or None
        The modification time of the uploaded file on the server (the time of the
        upload), or None if the server sent none.

    Raises
    ------
    TransferError
        If ``remote`` exists and ``archive_to`` is not given, ``archive_to``
        exists, access is denied, the server cannot make hard links, or the upload
        still fails after the last attempt. The message says which step failed
        and whether the file at ``remote`` is unchanged; the temporary file and a
        new archive name are removed when that is safe, or else named. (A rename
        the server reports as failed but did, its answer lost, counts as done,
        with a warning.)
    AuthError, ConfigError, ValueError
        As for :func:`download`.
    PathError
        If the local file cannot be read, or changes during the upload.
    """
    temporary = remote.parent / f".{remote.name}.{secrets.token_hex(4)}.part"
    left = set()  # temporary files that may still be on the server

    def prepare(sftp):
        for path in list(left):  # from an attempt that lost its connection
            if _remove(sftp, path):
                left.discard(path)
        try:
            current = sftp.stat(str(remote))
            mode, existed = current.st_mode, True
        except FileNotFoundError:
            current, mode, existed = None, None, False
        if mode is not None and not stat_module.S_ISREG(mode):
            raise TransferError(
                f"cannot upload {remote}: it is a folder (or another non-file) on "
                "the server"
            )
        if existed and archive_to is None:
            raise TransferError(
                f"{remote} is already on the server; an upload replaces a file only "
                "when the old one is archived (archive_to)"
            )
        if archive_to is not None and _exists(sftp, archive_to):
            raise TransferError(
                f"cannot archive {remote} as {archive_to}: {archive_to} is already "
                "on the server"
            )
        try:  # before anything is made on the server
            file = open(source, "rb")  # noqa: SIM115 - closed below
        except OSError as error:
            raise PathError(f"cannot read {source}: {error}") from error
        with file:
            _make_dirs(sftp, remote.parent)
            if existed:  # before sending the data, so that a problem shows early
                _make_dirs(sftp, archive_to.parent)
            left.add(temporary)
            try:
                before = os.fstat(file.fileno())
                attributes = sftp.putfo(
                    _Source(file, source),
                    str(temporary),
                    file_size=before.st_size,
                    callback=_files.progress("uploading", remote, before.st_size),
                    confirm=True,
                )
                after = os.fstat(file.fileno())
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise PathError(
                        f"{source} changed during the upload; upload it again when "
                        "it is complete"
                    )
                sftp.chmod(str(temporary), _MODE)
            except BaseException:
                if _remove(sftp, temporary):
                    left.discard(temporary)
                raise
        # The size, if it equals the size of the file being replaced.
        same_size = (
            before.st_size if existed and current.st_size == before.st_size else None
        )
        # The time of the upload: chmod and the rename do not change it.
        return existed, same_size, attributes.st_mtime

    reported = False  # whether a failure message already accounts for the files

    linking = False  # whether an archive name may have been made

    def finish(sftp):
        nonlocal reported, linking
        reported = True
        step = "renaming it to"
        archived = False
        try:
            if existed:
                step = f"archiving the old {remote} as {archive_to} before replacing"
                linking = True
                _hardlink(sftp, remote, archive_to)
                archived = True
                step = "replacing"
                sftp.posix_rename(str(temporary), str(remote))
            else:
                sftp.rename(str(temporary), str(remote))
            left.clear()
            linking = False
        except _ERRORS as error:
            # A rename either happened or did not; whether the temporary file is
            # still there tells which, also when only the answer was lost.
            present = _exists_or_unknown(sftp, temporary)
            renaming = archived or not existed
            if renaming and present is False:
                _config.logger.warning(
                    "the server reported a failure renaming %s to %s (%s), but the "
                    "file is in place",
                    temporary,
                    remote,
                    error,
                )
            else:
                cause = error
                if not existed:
                    cause = f"{error}; did {remote} appear on the server meanwhile?"
                if renaming and present is None:  # the connection failed
                    stray = [temporary, archive_to] if archived else [temporary]
                    done = "replaced" if existed else "created"
                    outcome = f"it is not known whether {remote} was {done}"
                else:
                    # Nothing was renamed: remote is as it was, and the temporary
                    # file and an archive name made here are spares.
                    stray = [] if _remove(sftp, temporary) else [temporary]
                    if archived and not _remove(sftp, archive_to):
                        stray.append(archive_to)
                    outcome = (
                        "the server file is unchanged"
                        if existed
                        else f"{remote} was not created"
                    )
                    if (
                        existed
                        and not archived
                        and not isinstance(error, _NoHardLinks)
                        and _exists_or_unknown(sftp, archive_to) is not False
                    ):
                        # The link may have been made with its answer lost, or
                        # someone else may have put a file there meanwhile.
                        outcome += (
                            f"; {archive_to} may now be on the server: if it has "
                            f"the same content as {remote}, this upload made it as a "
                            "second name of the old file, and it can be deleted "
                            "before trying again"
                        )
                raise TransferError(
                    f"uploaded {source} to the temporary file {temporary}, but "
                    f"{step} {remote} failed ({cause}); {outcome}"
                    + (
                        f"; left on the server: {', '.join(map(str, stray))}"
                        if stray
                        else ", and the temporary file was removed"
                    )
                ) from error

    try:
        existed, same_size, mtime = _run(
            settings,
            remote,
            _denied_is_final(prepare, remote),
            "upload",
            timeout=timeout,
            attempts=attempts,
            backoff=backoff,
            use_cache=False,
        )
        if same_size is not None:
            # Often new data (HDF5 files keep their size), but maybe the same file
            # again, which every user would then download again.
            _config.logger.warning(
                "replacing %s with a file of the same size (%d bytes); if it is "
                "the same file, the upload only makes every user download it again",
                remote,
                same_size,
            )
        if archive_to is not None and not existed:
            _config.logger.warning(
                "uploading %s to %s: there is nothing to archive as %s",
                source,
                remote,
                archive_to,
            )
        _run(
            settings,
            remote,
            finish,
            "upload",
            timeout=timeout,
            attempts=1,
            backoff=backoff,
            use_cache=False,
        )
    except TransferError as error:
        if left and not reported:
            raise type(error)(
                f"{error}; the temporary file {temporary} may be left on the server"
            ) from error
        raise
    except BaseException:  # interrupted, for example with Ctrl-C
        stray = [*map(str, left), *([str(archive_to)] if linking else [])]
        if stray:
            _config.logger.warning(
                "the upload of %s to %s was interrupted; this may be left on the "
                "server: %s",
                source,
                remote,
                ", ".join(stray),
            )
        raise
    _config.logger.info(
        "uploaded %s to %s%s",
        source,
        remote,
        f", archiving the old file as {archive_to}" if existed else "",
    )
    return mtime


def _denied_is_final(action, remote):
    """Wrap an upload action so that a missing or denied path is not retried."""

    def run(sftp):
        try:
            return action(sftp)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.EACCES):
                raise
            raise TransferError(
                f"cannot upload {remote}: {error.strerror or error} on the server"
            ) from error

    return run


def _exists(sftp, remote):
    """Return whether anything is at remote on the server."""
    try:
        sftp.stat(str(remote))
    except FileNotFoundError:
        return False
    return True


def _exists_or_unknown(sftp, remote):
    """Return whether anything is at remote on the server, or None if unknown."""
    try:
        return _exists(sftp, remote)
    except _ERRORS:
        return None


def _make_dirs(sftp, directory):
    """Create directory and its missing parents on the server, with mode 0755.

    Raises TransferError if a file is in the way.
    """
    missing = []
    while directory != directory.parent:
        try:
            mode = sftp.stat(str(directory)).st_mode
        except FileNotFoundError:
            missing.append(directory)
            directory = directory.parent
            continue
        if mode is not None and not stat_module.S_ISDIR(mode):
            raise TransferError(
                f"cannot create a folder below {directory}: it is a file on the server"
            )
        break
    for path in reversed(missing):
        sftp.mkdir(str(path))
        sftp.chmod(str(path), _DIRECTORY_MODE)  # mkdir's mode is cut by the umask


class _Source:
    """The local file being uploaded; its read errors raise PathError.

    So a local problem is not mistaken for a transfer error and retried.
    """

    def __init__(self, file, path):
        self._file = file
        self._path = path

    def read(self, size=-1):
        try:
            return self._file.read(size)
        except OSError as error:
            raise PathError(f"cannot read {self._path}: {error}") from error


class _NoHardLinks(OSError):
    """The server does not support OpenSSH's hard-link extension."""


def _hardlink(sftp, old, new):
    """Give the server file old the second name new (OpenSSH's extension).

    paramiko has no method for it, so the request is sent the way paramiko's own
    ``posix_rename`` sends ``posix-rename@openssh.com``.
    """
    try:
        sftp._request(CMD_EXTENDED, "hardlink@openssh.com", str(old), str(new))
    except OSError as error:
        if "unsupported" not in str(error).lower():
            raise
        raise _NoHardLinks(
            "the server does not support hard links (hardlink@openssh.com), which "
            "archiving needs"
        ) from error


def _remove(sftp, path):
    """Remove a server file; return whether it is gone."""
    try:
        sftp.remove(str(path))
    except FileNotFoundError:
        return True
    except _ERRORS:
        return False
    return True


def _server_file_error(remote, error):
    """Return the error for a missing or forbidden server file, else None."""
    if isinstance(error, FileNotFoundError):
        return NotOnServer(f"{remote} is not on the private server")
    if isinstance(error, PermissionError):
        return TransferError(f"access to {remote} is denied on the private server")
    return None


def _fetch(sftp, remote, target):
    """Download one file over an open SFTP channel.

    A missing or forbidden server file raises TransferError and local file problems
    raise PathError; neither is retried. Other OSErrors are transfer failures.
    """
    try:
        attributes = sftp.stat(str(remote))
    except OSError as error:
        if _server_file_error(remote, error) is None:
            raise
        raise _server_file_error(remote, error) from error
    with _files.writing(target) as partial:
        try:
            received = sftp.getfo(
                str(remote),
                partial.file,
                callback=_files.progress("downloading", remote, attributes.st_size),
                max_concurrent_prefetch_requests=_PREFETCH,
            )
        except OSError as error:  # opening the file can fail after stat worked
            if _server_file_error(remote, error) is None:
                raise
            raise _server_file_error(remote, error) from error
        if received != attributes.st_size:
            raise ConnectionError(
                f"transfer cut short: received {received} of {attributes.st_size} bytes"
            )
        partial.mtime = attributes.st_mtime
