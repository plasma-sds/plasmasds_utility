"""The public entry point: one :class:`DataClient` per client package."""

import re
from pathlib import Path

from plasmasds_utility import _config, _https, _paths, _sftp
from plasmasds_utility.exceptions import AuthError, PathError, TransferError

_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# On Windows these files share a directory with the client directories.
_RESERVED = {_config.CONFIG_FILE, _config.LOG_FILE}
# (prefix, key) pairs already warned about using their public copy.
_public_warned = set()


def _present(path, key):
    """Return whether path is a file; raise PathError if something else is there."""
    if path.is_file():
        return True
    if path.exists():
        raise PathError(f"cannot store {key!r} at {path}: it is not a file")
    return False


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

    def get(self, key):
        """Return the local path of a data file, downloading it if it is missing.

        Looks in this order and returns the first file found:

        1. the local private copy;
        2. the private server, over SFTP, downloading into the private copy;
        3. the local public copy;
        4. the public server, over HTTPS, downloading into the public copy.

        Step 2 is skipped when private data is not available to you (no SSH key,
        key rejected, unknown or mismatching host key), which is remembered for
        the rest of the process, or when the file is not on the private server.
        Any other failure of the private server, such as a timeout, raises
        :class:`TransferError` instead, so public data never silently replaces
        private data. Returning public data logs a warning, once per key and
        process.

        A local copy is returned after a single ``stat``, without contacting a
        server.

        Parameters
        ----------
        key : str
            The data key (see :meth:`local_path`).

        Returns
        -------
        pathlib.Path
            The local file.

        Raises
        ------
        PathError
            If the key is invalid, or something other than a file is in the way.
        TransferError
            If the private server fails for a reason other than those above, or the
            file is on neither server.
        ConfigError
            As for :meth:`client_dir`, or if the configured SSH key is missing or
            unreadable.
        """
        private = self.local_path(key, private=True)
        if _present(private, key):
            return private
        settings = _config.settings()
        remote = _paths.private_remote(settings, self.prefix, key)
        try:
            return _sftp.download(settings, remote, private)
        except (AuthError, _sftp.NotOnServer) as error:
            reason = error
        public = self.local_path(key, private=False)
        if not _present(public, key):
            url = _paths.public_url(settings, self.prefix, key)
            try:
                _https.download(url, public)
            except TransferError as error:
                raise TransferError(
                    f"cannot get {key!r}: not from the private server ({reason}), "
                    f"and not from the public server ({error})"
                ) from error
        if (self.prefix, key) not in _public_warned:
            _public_warned.add((self.prefix, key))
            _config.logger.warning(
                "using the public copy of %r (%s): %s", key, reason, public
            )
        return public

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
    files are tried. A key with a passphrase must be loaded into the agent
    (``ssh-add``), because the utility never asks for a passphrase.

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
        _config.logger.info("removed the saved SSH key")
        return None
    key = Path(path).expanduser().absolute()
    if not key.is_file():
        raise PathError(f"cannot use {key} as the SSH key: no such file")
    _config.save_ssh_key(str(key))
    _config.logger.info("SSH key set to %s", key)
    return key
