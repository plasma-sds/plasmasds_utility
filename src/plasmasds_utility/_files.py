"""Write files atomically: through a temporary file moved into place when complete.

Used for downloads and for the user configuration file, so a failed or interrupted
write never leaves a partial file under the target name.
"""

import contextlib
import logging
import os
import tempfile

from plasmasds_utility.exceptions import PathError

logger = logging.getLogger("plasmasds_utility")


class _Partial:
    """The file being written; set ``mtime`` to give the result that timestamp."""

    def __init__(self, file):
        self.file = file
        self.mtime = None


def make_parent(target):
    """Create the directory of target if needed.

    Parameters
    ----------
    target : pathlib.Path
        The file whose directory is created.

    Raises
    ------
    PathError
        If the directory cannot be created, for example because a file has its name.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise PathError(
            f"cannot create the directory {target.parent}: {error}"
        ) from error


def _local_error(target, error):
    return PathError(f"cannot write {target}: {error}")


class _LocalFile:
    """The open temporary file, whose write errors become PathError.

    So a full disk is not mistaken for a transfer error and retried.
    """

    def __init__(self, file, target):
        self._file = file
        self._target = target

    def write(self, data):
        try:
            return self._file.write(data)
        except OSError as error:
            raise _local_error(self._target, error) from error

    def tell(self):
        return self._file.tell()


def _read_umask():
    """Return the umask of this process.

    On Linux it is read from ``/proc``; elsewhere it can only be read by setting
    it and setting it back, which is done once, at import, before any transfer.
    """
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


# New files get 0o666 minus this, like any file the user creates (#25).
_UMASK = _read_umask()


@contextlib.contextmanager
def writing(target, mode=None):
    """Write target atomically.

    Yields an object whose ``file`` is a binary file opened in the target's
    directory, under a unique ``.part`` name. When the block ends normally the file
    is closed, given the timestamp in ``mtime`` if one was set, and moved over
    target with :func:`os.replace`. If the block raises, the temporary file is
    removed and an existing target is left unchanged.

    A failure to set the timestamp only logs a warning: the content matters more.
    Every other local failure (creating, writing, flushing or moving the file)
    raises :class:`PathError`, which download retries do not catch: a full disk or
    an unwritable directory is not a transfer error.

    Parameters
    ----------
    target : pathlib.Path
        The file to write. Its directory must exist (see :func:`make_parent`).
    mode : int, optional
        The permissions of the file on POSIX systems. The default is what a new
        file of the user gets: ``0o666`` minus the umask, so that data in a
        shared group folder is readable by the group; ``tempfile`` alone would
        give ``0o600``. Ignored on Windows.

    Yields
    ------
    object
        With attributes ``file`` (with ``write`` and ``tell``) and ``mtime``
        (None, or a POSIX timestamp to set).

    Raises
    ------
    PathError
        If the file cannot be created, written or moved into place.
    """
    try:
        fd, temporary = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".part"
        )
    except OSError as error:
        raise _local_error(target, error) from error
    try:
        if hasattr(os, "fchmod"):  # POSIX; Windows has no such permissions
            try:
                os.fchmod(fd, (0o666 & ~_UMASK) if mode is None else mode)
            except OSError as error:
                os.close(fd)
                raise _local_error(target, error) from error
        with os.fdopen(fd, "wb") as file:
            partial = _Partial(_LocalFile(file, target))
            yield partial
            try:
                file.flush()
            except OSError as error:
                raise _local_error(target, error) from error
        if partial.mtime is not None:
            try:
                os.utime(temporary, (partial.mtime, partial.mtime))
            except OSError as error:
                logger.warning(
                    "cannot set the modification time of %s: %s", target, error
                )
        try:
            os.replace(temporary, target)
        except OSError as error:
            raise _local_error(target, error) from error
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
