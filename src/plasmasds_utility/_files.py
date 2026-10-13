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


# Transfers at least this large (bytes) log their progress; smaller ones log only
# when done.
_PROGRESS_SIZE = 50_000_000


def progress(verb, name, total):
    """Return a callback that logs the progress of a large transfer at INFO.

    The callback takes the bytes transferred so far (and ignores a second
    argument, as paramiko passes the total too); it logs at most once per 10 %,
    when another 10 % is done, if ``total`` is at least 50 MB.

    Parameters
    ----------
    verb : str
        What is done, for the message, for example ``"downloading"``.
    name : object
        What is transferred, for the message.
    total : int or None
        The size in bytes; None (unknown) logs nothing.

    Returns
    -------
    callable
        ``callback(done, total=None)``.
    """
    reported = 0  # tenths logged so far

    def report(done, _total=None):
        nonlocal reported
        if not total or total < _PROGRESS_SIZE:
            return
        tenths = min(done * 10 // total, 10)
        if tenths > reported:
            reported = tenths
            logger.info(
                "%s %s: %d %% (%d of %d MB)",
                verb,
                name,
                tenths * 10,
                done // 1_000_000,
                total // 1_000_000,
            )

    return report


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


@contextlib.contextmanager
def writing(target):
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
