"""The public entry point: one :class:`DataClient` per client package."""

import atexit
import re
import sys
from pathlib import Path

from plasmasds_utility import _config, _https, _paths, _sftp
from plasmasds_utility.exceptions import (
    AuthError,
    ConfigError,
    PathError,
    TransferError,
)

_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# On Windows these files share a directory with the client directories.
_RESERVED = {_config.CONFIG_FILE, _config.LOG_FILE}
# (prefix, key) -> reason, for files that came from public data in a fallback.
_fallbacks = {}


def _record_fallback(prefix, key, reason, path):
    """Note that get() fell back to public data, and announce it."""
    if (prefix, key) in _fallbacks:
        return
    first = not _fallbacks
    _fallbacks[(prefix, key)] = str(reason)
    _config.logger.info(
        "using the public copy of %r for %s (%s): %s", key, prefix, reason, path
    )
    if first:
        _config.logger.warning(
            "using public data instead of private data for %r (%s); further cases "
            "are only logged in %s and listed at exit, or by "
            "plasmasds_utility.show_public_fallbacks()",
            key,
            reason,
            _config.log_dir() / _config.LOG_FILE,
        )


def _fallback_summary():
    """Return the text listing the fallbacks of this process."""
    if not _fallbacks:
        return "All files came from the source asked for; no public fallbacks."
    lines = [
        f"{len(_fallbacks)} file(s) came from the public server instead of the "
        "private one:"
    ]
    lines += [
        f"  {prefix}: {key} ({reason})" for (prefix, key), reason in _fallbacks.items()
    ]
    return "\n".join(lines)


def show_public_fallbacks():
    """Print the files that ``get`` took from public data instead of private data.

    Lists, for this process, every file for which :meth:`DataClient.get` with the
    default ``private=None`` fell back to public data, with the reason. The log
    file keeps the same information for every process.

    Returns
    -------
    None
        The list is printed to standard output, not returned.

    Examples
    --------
    >>> import plasmasds_utility
    >>> plasmasds_utility.show_public_fallbacks()  # doctest: +SKIP
    1 file(s) came from the public server instead of the private one:
      renate-od: atomic_data/Na/rates.h5 (no SSH key found ...)
    """
    print(_fallback_summary())


@atexit.register
def _summary_at_exit():
    if _fallbacks:
        print(f"plasmasds_utility: {_fallback_summary()}", file=sys.stderr)


_noticed = False  # whether the unchecked-local-data notice was given


_UPDATE_CHOICES = ("never", "if_newer", "force")


def _explain_update():
    """Print once per process what get() does with local copies, and the options.

    A notice, not a warning: it is printed to stderr like the exit summary and
    logged at INFO, so that warnings keep meaning that something went wrong.
    """
    global _noticed
    if _noticed:
        return
    _noticed = True
    text = (
        "get() uses a local copy without asking the server for a newer version "
        '(update="never", the default), and downloads a file that is not on disk. '
        'Other options: update="if_newer" downloads again if the server copy is '
        'newer; update="force" downloads again regardless. '
        "DataClient(prefix).check_updates() checks every local file of a client."
    )
    _config.logger.info(text)
    print(f"plasmasds_utility: {text}", file=sys.stderr)


# Seconds by which the server time must exceed the local time to count as newer:
# FAT and exFAT store times in 2-second steps, which would otherwise make some
# files look newer on every check.
_TOLERANCE = 2


def _newer(path, size, mtime):
    """Return whether the server copy (size, mtime) is newer than the local file.

    Newer means a modification time more than ``_TOLERANCE`` seconds later. A local
    copy that is newer than the server's is kept (edited locally, or the server
    copy was restored with an older time) and logged at INFO; one with the same
    time but a different size is kept with a warning.

    Raises
    ------
    PathError
        If the local file cannot be read.
    TransferError
        If the server sent no modification time, so the copies cannot be compared.
    """
    try:
        local = path.stat()
    except OSError as error:  # removed or replaced while being checked
        raise PathError(f"cannot read {path}: {error}") from error
    if mtime is None:
        raise TransferError(
            f"cannot compare {path} with the server copy: the server sent no "
            "modification time"
        )
    if int(mtime) - int(local.st_mtime) > _TOLERANCE:
        return True
    if int(local.st_mtime) - int(mtime) > _TOLERANCE:
        _config.logger.info(
            "kept %s, which is newer than the server copy (edited locally? if the "
            'server copy was restored, use update="force")',
            path,
        )
    elif size is not None and size != local.st_size:
        _config.logger.warning(
            "%s has the same modification time as the server copy but a different "
            "size (local copy: %d bytes, server copy: %d bytes): it was edited "
            "locally, or changed on the server without a new modification time; "
            'to replace it with the server copy, use get(key, update="force")',
            path,
            local.st_size,
            size,
        )
    return False


def _refresh_private(settings, remote, path, force):
    """Download the private copy if the server copy is newer, or always if forced.

    Returns whether the file was downloaded.
    """
    if not force:
        size, mtime = _sftp.stat(settings, remote, use_cache=False)
        if not _newer(path, size, mtime):
            return False
    _sftp.download(settings, remote, path, use_cache=False)
    return True


def _refresh_public(url, path, force):
    """Download the public copy if the server copy is newer, or always if forced.

    Returns whether the file was downloaded.
    """
    if not force:
        size, mtime = _https.head(url)
        if not _newer(path, size, mtime):
            return False
    _https.download(url, path)
    return True


def _kept(path, error, force):
    """Return the error for a requested check or download that could not be done.

    An AuthError stays an AuthError, so callers catching it still do.
    """
    cls = AuthError if isinstance(error, AuthError) else TransferError
    if isinstance(error, (_sftp.NotOnServer, _https.NotOnServer)):
        return cls(f"kept the local copy {path}: {error}")
    failed = "could not be downloaded again" if force else "could not be checked"
    return cls(f"kept the local copy {path}, but the server copy {failed}: {error}")


def _present(path, key):
    """Return whether path is a file; raise PathError if something else is there."""
    if path.is_file():
        return True
    if path.exists():
        raise PathError(f"cannot store {key!r} at {path}: it is not a file")
    return False


def _local_files(tree):
    """Return (path, key) for the data files below tree, sorted.

    Temporary files, anything hidden (including inside hidden directories) and
    names that are not valid keys (with a warning) are left out.
    """
    if not tree.is_dir():
        return []
    files = []
    for path in sorted(p for p in tree.rglob("*") if p.is_file()):
        parts = path.relative_to(tree).parts
        if any(part.startswith(".") for part in parts) or path.name.endswith(".part"):
            continue
        key = "/".join(parts)
        try:
            _paths.check_key(key)
        except PathError as error:
            _config.logger.warning("skipping %s: %s", path, error)
            continue
        files.append((path, key))
    return files


class DataClient:
    """Access the data of one client package.

    Creating a client reads and writes nothing, so a package can create its client at
    import time; the configuration and the log file are opened on first use.

    Parameters
    ----------
    prefix : str
        The client's directory name on the server, for example ``"renate-od"``.
        It may contain ASCII letters, digits, ``.``, ``_`` and ``-``, and must start
        with a letter or a digit. ``config.json`` and ``plasmasds.log`` are reserved,
        because on Windows those files share a directory with the client directories.
        As for data keys, it may not end in a dot or be a Windows device name such as
        ``nul``.
    working_dir : str or os.PathLike, optional
        A directory to use for this client's data, for this object only; it takes
        precedence over every other setting (see :meth:`client_dir`).
        ``~`` is expanded, and a relative path is taken relative to the current
        directory at the time the client is created.

    Raises
    ------
    PathError
        If ``prefix`` is not a valid name.

    Examples
    --------
    >>> from plasmasds_utility import DataClient
    >>> data = DataClient("renate-od")
    """

    def __init__(self, prefix, *, working_dir=None):
        if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
            raise PathError(
                f"invalid client prefix {prefix!r}: use ASCII letters, digits, '.', "
                "'_' and '-', starting with a letter or a digit"
            )
        if prefix.lower() in _RESERVED:
            raise PathError(
                f"invalid client prefix {prefix!r}: the utility keeps its own "
                f"{_config.CONFIG_FILE} and {_config.LOG_FILE} next to the client "
                "directories (on Windows), so these names are reserved"
            )
        problem = _paths.windows_name_problem(prefix)
        if problem:
            raise PathError(f"invalid client prefix {prefix!r}: {problem}")
        self.prefix = prefix
        self._working_dir = (
            None if working_dir is None else Path(working_dir).expanduser().absolute()
        )

    def client_dir(self):
        """Return the directory that holds this client's data.

        The first of these that is set wins:

        1. the ``working_dir`` given when this object was created;
        2. ``$PLASMASDS_DATA_DIR/<prefix>``;
        3. the working directory saved with :meth:`set_working_dir`;
        4. ``<default data directory>/<prefix>``, for example
           ``~/.local/share/plasmasds/renate-od`` on Linux.

        Returns
        -------
        pathlib.Path
            The absolute directory; it is not created.

        Raises
        ------
        ConfigError
            If ``PLASMASDS_DATA_DIR`` is a relative path, the user configuration
            file is invalid, or the log directory cannot be created.
        """
        _config.start_logging()
        if self._working_dir is not None:
            return self._working_dir
        env_dir = _config.env_data_dir()
        if env_dir is not None:
            return env_dir / self.prefix
        saved = _config.settings()["working_dirs"].get(self.prefix)
        if saved is not None:
            return Path(saved)
        return _config.default_data_dir() / self.prefix

    def local_path(self, key, *, private=True):
        """Return where a data file is, or will be, stored locally.

        This neither checks nor creates the file, and never contacts the server.

        Parameters
        ----------
        key : str
            The path of the file below the client's directory on the server, with
            ``/`` separators on every platform, for example
            ``"atomic_data/Na/rates.h5"``.
        private : bool, default True
            True for the local copy of private data, False for public data.

        Returns
        -------
        pathlib.Path
            ``<client_dir>/private/<key>``, or ``<client_dir>/public/<key>`` if
            ``private`` is false (see :meth:`client_dir`).

        Raises
        ------
        PathError
            If the key is not a valid relative path; the message names the key and
            the part that is wrong.
        ConfigError
            As for :meth:`client_dir`.

        Examples
        --------
        On Linux, with the default client directory:

        >>> DataClient("renate-od").local_path("Na/rates.h5")  # doctest: +SKIP
        PosixPath('/home/me/.local/share/plasmasds/renate-od/private/Na/rates.h5')
        """
        _paths.check_key(key)  # before any I/O
        return _paths.local_path(self.client_dir(), key, private=private)

    def get(self, key, *, private=None, update="never"):
        """Return the local path of a data file, downloading it first if needed.

        ``get`` does not open the file: it returns a :class:`pathlib.Path` for the
        client to open with its usual tools (``h5py``, ``numpy``, ...). Clients
        call it every time they need a file; when the file is already on disk,
        that costs a single ``stat``.

        Looks in this order and returns the first file found:

        1. the local private copy;
        2. the private server, over SFTP, downloading into the private copy;
        3. the local public copy;
        4. the public server, over HTTPS, downloading into the public copy.

        ``private`` selects the sources: True uses only 1 and 2, False only 3 and
        4, and None (the default) all four. With None, steps 1 and 2 give way to
        public data only when private data is not available to you (no SSH key,
        key rejected, unknown or mismatching host key; remembered for the rest of
        the process) or the file is not on the private server. Any other failure
        of the private server, such as a timeout, raises, so public data never
        silently replaces private data. Such a fallback is announced: the first in
        a process with a warning, every one in the log file, and all of them in a
        summary at exit and in :func:`show_public_fallbacks`.

        ``update`` decides what happens to a file that is already on disk; a file
        that is not on disk is always downloaded:

        - ``"never"`` (default): the local copy is used without asking the server;
        - ``"if_newer"``: the local copy is compared with its server and
          downloaded again only if the server copy is newer (modification time);
          a newer local copy is kept, and one with the same time but a different
          size is kept with a warning;
        - ``"force"``: the local copy is downloaded again regardless, for example
          when it is damaged.

        The first call in a process prints these options. If a requested check
        cannot be done, the local copy is kept and :class:`TransferError` is
        raised. Without a local copy, the private
        server is asked even about a file it was found not to have earlier in
        the process (with the default, that answer is remembered, so a local
        public copy normally costs one question per file and process; use
        ``private=False`` to skip it, for example offline).

        Parameters
        ----------
        key : str
            The data key (see :meth:`local_path`).
        private : bool or None, default None
            True for private data only, False for public data only (the private
            server is never contacted), None for the best available.
        update : {"never", "if_newer", "force"}, default "never"
            What to do with a local copy (see above).

        Returns
        -------
        pathlib.Path
            The local file.

        Raises
        ------
        ValueError
            If ``update`` is not one of the values above.
        PathError
            If the key is invalid, something other than a file is in the way, or
            the file cannot be written.
        AuthError
            With ``private=True``, if private data is not available to you.
        TransferError
            If the private server fails, other than by the fallbacks above, the
            file is not on the servers that were tried, or a requested check of a
            local copy cannot be done.
        ConfigError
            As for :meth:`client_dir`, or if the configured SSH key is missing or
            unreadable.
        """
        if update not in _UPDATE_CHOICES:
            raise ValueError(
                f"update must be one of {', '.join(map(repr, _UPDATE_CHOICES))}, "
                f"got {update!r}"
            )
        _paths.check_key(key)  # an invalid call prints nothing
        _config.start_logging()  # so that the explanation reaches the log file
        _explain_update()
        settings = _config.settings()
        refresh = update != "never"
        force = update == "force"
        reason = None
        if private is not False:
            private_path = self.local_path(key, private=True)
            remote = _paths.private_remote(settings, self.prefix, key)
            if _present(private_path, key):
                if not refresh:
                    return private_path
                try:
                    _refresh_private(settings, remote, private_path, force)
                except TransferError as error:
                    raise _kept(private_path, error, force) from error
                return private_path
            try:
                return _sftp.download(
                    settings, remote, private_path, use_cache=not refresh
                )
            except (AuthError, _sftp.NotOnServer) as error:
                if private:
                    raise
                reason = error
        public_path = self.local_path(key, private=False)
        url = _paths.public_url(settings, self.prefix, key)
        if _present(public_path, key):
            if refresh:
                try:
                    _refresh_public(url, public_path, force)
                except TransferError as error:
                    raise _kept(public_path, error, force) from error
        else:
            try:
                _https.download(url, public_path)
            except TransferError as error:
                if reason is None:
                    raise
                raise TransferError(
                    f"cannot get {key!r}: not from the private server ({reason}), "
                    f"and not from the public server ({error})"
                ) from error
        if reason is not None:
            _record_fallback(self.prefix, key, reason, public_path)
        return public_path

    def check_updates(self):
        """Check every local data file against its server and download newer ones.

        Walks the local ``public/`` and ``private/`` trees of this client and
        compares each file with its server, as ``get(key, update="if_newer")``
        does, downloading it again where the server copy is newer. Temporary and
        hidden files are skipped, including anything inside a hidden directory,
        and so is a file whose name is not a valid key (with a warning).

        Private files are checked with one SFTP listing per directory; public
        files with one HTTPS ``HEAD`` request each, so a public tree of many
        thousands of files takes a while.
        Unlike a single ``get``, a file missing on its server is kept with a
        warning, and the other files are still checked. If private data is not
        available to you (no usable SSH key), the ``private/`` tree is not checked
        further. At the
        end a short report is printed to stderr, followed by the public-fallback
        summary if there were fallbacks in this process.

        Returns
        -------
        list of pathlib.Path
            The files that were downloaded again.

        Raises
        ------
        TransferError
            After the whole check, if anything could not be checked; the message
            lists it. Files that could be checked are already updated: the
            exception's ``updated`` and ``missing`` attributes list the files that
            were downloaded again and those missing on their server.
        ConfigError
            As for :meth:`client_dir`. A configured SSH key that is missing or
            unreadable does not raise here: the private data is reported as not
            checked, like a missing key.
        """
        settings = _config.settings()
        base = self.client_dir()
        updated, missing, failed = [], [], []
        public = _local_files(base / "public")
        private = _local_files(base / "private")
        keys = {key for _, key in public} | {key for _, key in private}
        for path, key in public:
            url = _paths.public_url(settings, self.prefix, key)
            try:
                if _refresh_public(url, path, False):
                    updated.append(path)
                    _config.logger.info("updated %s", path)
            except _https.NotOnServer as error:
                missing.append(path)
                _config.logger.warning("kept %s: %s", path, error)
            except (TransferError, PathError) as error:
                failed.append((key, error))
                _config.logger.warning("cannot check %s: %s", path, error)
        unchecked = 0
        try:
            self._check_private(settings, private, updated, missing, failed)
        except (AuthError, ConfigError) as error:
            done = {p for p in updated + missing} | {k for k, _ in failed}
            unchecked = sum(
                1 for path, key in private if path not in done and key not in done
            )
            failed.append(("private data (not checked)", error))
            _config.logger.warning(
                "the private data of %s was not checked: %s", self.prefix, error
            )
        not_checked = unchecked + sum(1 for what, _ in failed if what in keys)
        print(
            f"plasmasds_utility: {len(public) + len(private)} local file(s) of "
            f"{self.prefix}: {len(updated)} updated, {len(missing)} not on the "
            f"server, {not_checked} could not be checked",
            file=sys.stderr,
        )
        if _fallbacks:
            print(f"plasmasds_utility: {_fallback_summary()}", file=sys.stderr)
        if failed:
            lines = "\n".join(f"  {what}: {error}" for what, error in failed)
            error = TransferError(
                f"could not check everything of {self.prefix}:\n{lines}"
            )
            error.updated = updated
            error.missing = missing
            raise error
        return updated

    def _check_private(self, settings, files, updated, missing, failed):
        """Check local private files, listing each server directory once.

        Appends to ``updated``, ``missing`` and ``failed``; an AuthError stops the
        check and is raised.
        """
        by_dir = {}
        for path, key in files:
            remote = _paths.private_remote(settings, self.prefix, key)
            by_dir.setdefault(remote.parent, []).append((path, key, remote))
        for remote_dir, entries in by_dir.items():
            try:
                listing = _sftp.listdir(settings, remote_dir)
            except _sftp.NotOnServer:
                listing = {}
            except AuthError:
                raise
            except TransferError as error:
                for _path, key, _remote in entries:
                    failed.append((key, error))
                _config.logger.warning("cannot list %s: %s", remote_dir, error)
                continue
            for path, key, remote in entries:
                if remote.name not in listing:
                    missing.append(path)
                    _config.logger.warning(
                        "kept %s: %s is not on the private server", path, remote
                    )
                    continue
                try:
                    if _newer(path, *listing[remote.name]):
                        _sftp.download(settings, remote, path, use_cache=False)
                        updated.append(path)
                        _config.logger.info("updated %s", path)
                except AuthError:
                    raise
                except (TransferError, PathError) as error:
                    failed.append((key, error))
                    _config.logger.warning("cannot check %s: %s", path, error)

    def set_working_dir(self, path):
        """Save the working directory for this client in the user configuration.

        The directory is created if needed, and applies to every later
        :class:`DataClient` with this prefix, in any process, unless
        ``PLASMASDS_DATA_DIR`` or an explicit ``working_dir`` takes precedence
        (see :meth:`client_dir`); a warning is logged when one does.

        Use :meth:`clear_working_dir` to go back to the default.

        Parameters
        ----------
        path : str or os.PathLike
            The directory. ``~`` is expanded and a relative path is taken relative to
            the current directory.

        Returns
        -------
        pathlib.Path
            The absolute directory that was saved.

        Raises
        ------
        PathError
            If ``path`` is None, or the directory cannot be created, for example
            because a file has that name.
        ConfigError
            If the user configuration file is invalid or cannot be written, or the
            log directory cannot be created.
        """
        if path is None:
            raise PathError(
                f"no working directory given for {self.prefix!r}; use "
                "clear_working_dir() to go back to the default"
            )
        _config.start_logging()
        directory = Path(path).expanduser().absolute()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except FileExistsError as error:
            raise PathError(
                f"cannot use {directory} as the working directory for {self.prefix!r}: "
                "it exists and is not a directory"
            ) from error
        except OSError as error:
            raise PathError(
                f"cannot create the working directory {directory} for "
                f"{self.prefix!r}: {error}"
            ) from error
        _config.save_working_dir(self.prefix, str(directory))
        _config.logger.info(
            "working directory for %r set to %s", self.prefix, directory
        )
        if self._working_dir is not None:
            _config.logger.warning(
                "the working directory for %r was saved, but this client uses %s, "
                "given when it was created",
                self.prefix,
                self._working_dir,
            )
        elif _config.env_data_dir() is not None:
            _config.logger.warning(
                "the working directory for %r was saved, but PLASMASDS_DATA_DIR is "
                "set and takes precedence",
                self.prefix,
            )
        return directory

    def clear_working_dir(self):
        """Remove the saved working directory for this client.

        Later :class:`DataClient` objects with this prefix use the default again (see
        :meth:`client_dir`). The directory itself and its data are left alone.
        Nothing is written if no working directory was saved.

        Raises
        ------
        ConfigError
            If the user configuration file is invalid or cannot be written, or the
            log directory cannot be created.
        """
        _config.start_logging()
        if self.prefix not in _config.settings()["working_dirs"]:
            return
        _config.save_working_dir(self.prefix, None)
        _config.logger.info("removed the working directory for %r", self.prefix)


def set_ssh_key(path):
    """Save the SSH private key used for private data, for every client package.

    Without a saved key, the keys in the SSH agent and the standard ``~/.ssh/id_*``
    files are tried. Open SSH sessions are closed and remembered login failures
    forgotten, so the change takes effect at once. A key with a passphrase must be
    loaded into the agent (``ssh-add``), because the utility never asks for a
    passphrase.

    Parameters
    ----------
    path : str or os.PathLike or None
        The private key file. ``~`` is expanded and a relative path is taken
        relative to the current directory. None removes the saved key.

    Returns
    -------
    pathlib.Path or None
        The absolute key file that was saved, or None if it was removed.

    Raises
    ------
    PathError
        If the file does not exist.
    ConfigError
        If the user configuration file is invalid or cannot be written, or the log
        directory cannot be created.

    Examples
    --------
    >>> import plasmasds_utility
    >>> plasmasds_utility.set_ssh_key("~/.ssh/plasmasds_deep")  # doctest: +SKIP
    """
    _config.start_logging()
    if path is None:
        _config.save_ssh_key(None)
        _sftp.close_all()
        _config.logger.info("removed the saved SSH key")
        return None
    key = Path(path).expanduser().absolute()
    if not key.is_file():
        raise PathError(f"cannot use {key} as the SSH key: no such file")
    _config.save_ssh_key(str(key))
    _sftp.close_all()  # use the new key at once, not a remembered failure
    _config.logger.info("SSH key set to %s", key)
    return key
