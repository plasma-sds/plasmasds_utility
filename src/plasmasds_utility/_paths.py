"""Turn a data key into its local path and its location on the server.

A key is the path of a file below the client's directory, written with ``/`` on every
platform, for example ``"atomic_data/Na/rates.h5"``. The same key names the file on the
server and locally, so the local tree mirrors the server.

The key rules are the same on every platform, so a key that works for a Linux user also
works for a Windows user. Because a valid key has no ``..``, no absolute start and no
drive, every path built from it stays inside its base directory.
"""

import re

from plasmasds_utility.exceptions import PathError

# Windows opens these as devices, with or without an extension: writing to them loses
# the data silently.
_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}
_FORBIDDEN = re.compile(r"[\\:\x00]")


def _invalid(key, reason):
    return PathError(f"invalid data key {key!r}: {reason}")


def check_key(key):
    """Check a data key and split it into its parts.

    Parameters
    ----------
    key : str
        A relative path with ``/`` separators, for example ``"atomic_data/Na/x.h5"``.

    Returns
    -------
    tuple of str
        The parts of the key, for example ``("atomic_data", "Na", "x.h5")``.

    Raises
    ------
    PathError
        If the key is not a string, is empty or absolute, contains a backslash, ``:``
        or a NUL character, or has a part that is empty, ``.``, ``..``, ends in a dot
        or a space, or is a Windows device name such as ``NUL`` or ``com1.txt``.
    """
    if not isinstance(key, str):
        raise _invalid(
            key, f"keys are strings with '/' separators, got {type(key).__name__}"
        )
    if not key:
        raise _invalid(key, "the key is empty")
    match = _FORBIDDEN.search(key)
    if match:
        raise _invalid(key, f"{match.group()!r} is not allowed")
    if key.startswith("/"):
        raise _invalid(key, "keys are relative; remove the leading '/'")
    parts = tuple(key.split("/"))
    for part in parts:
        if part in ("", ".", ".."):
            name = "an empty part" if not part else repr(part)
            raise _invalid(key, f"{name} is not allowed")
        if part[-1] in ". ":
            raise _invalid(key, f"part {part!r} ends in a dot or a space")
        if part.split(".")[0].upper() in _DEVICE_NAMES:
            raise _invalid(key, f"part {part!r} is a reserved device name on Windows")
    return parts


def local_path(client_dir, key, *, public):
    """Return the local path of a data file.

    Parameters
    ----------
    client_dir : pathlib.Path
        The client directory.
    key : str
        The data key.
    public : bool
        True for the copy of public data, False for private data.

    Returns
    -------
    pathlib.Path
        ``<client_dir>/public/<key>`` or ``<client_dir>/private/<key>``.

    Raises
    ------
    PathError
        If the key is invalid (see :func:`check_key`).
    """
    parts = check_key(key)
    return client_dir.joinpath("public" if public else "private", *parts)
