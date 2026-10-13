"""Write files atomically: through a temporary file moved into place when complete.

Used for downloads and for the user configuration file, so a failed or interrupted
write never leaves a partial file under the target name.
"""

import contextlib
import logging
import os
import secrets

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


def _create(target):
    """Create a new, empty temporary file next to target; return (fd, path).

    Created with mode ``0o666`` like any new file, so the umask and a default
    ACL of the folder apply as usual (``tempfile`` would force ``0o600``, which
    a shared group folder cannot read, #25). A name already taken is retried.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        path = target.parent / f".{target.name}.{secrets.token_hex(4)}.part"
        try:
            return os.open(path, flags, 0o666), path
        except FileExistsError:
            continue
    raise FileExistsError(f"no free temporary name next to {target}")


@contextlib.contextmanager
def writing(target):
    """Write target atomically.

    Yields an object whose ``file`` is a binary file opened in the target's
    directory, under a unique ``.part`` name, with the permissions any new file
    of the user gets there (see :func:`_create`). When the block ends normally the file
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
        fd, temporary = _create(target)
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
