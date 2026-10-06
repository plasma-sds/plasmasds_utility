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


@contextlib.contextmanager
def writing(target):
    """Write target atomically.

    Yields an object whose ``file`` is a binary file opened in the target's
    directory, under a unique ``.part`` name. When the block ends normally the file
    is closed, given the timestamp in ``mtime`` if one was set, and moved over
    target with :func:`os.replace`. If the block raises, the temporary file is
    removed and an existing target is left unchanged.

    A failure to set the timestamp only logs a warning: the content matters more.

    Parameters
    ----------
    target : pathlib.Path
        The file to write. Its directory must exist (see :func:`make_parent`).

    Yields
    ------
    object
        With attributes ``file`` (the open binary file) and ``mtime`` (None, or a
        POSIX timestamp to set).

    Raises
    ------
    OSError
        If the temporary file cannot be created or moved into place.
    """
    fd, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".part"
    )
    try:
        with os.fdopen(fd, "wb") as file:
            partial = _Partial(file)
            yield partial
        if partial.mtime is not None:
            try:
                os.utime(temporary, (partial.mtime, partial.mtime))
            except OSError as error:
                logger.warning(
                    "cannot set the modification time of %s: %s", target, error
                )
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
