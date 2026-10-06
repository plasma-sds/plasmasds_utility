r"""Directories, packaged defaults, the user configuration file and the log file.

Nothing here runs at import time; every function computes its result when called.

Default directories (issue #9):

========  ==================================  =====================================
          Linux                               Windows
========  ==================================  =====================================
data      ``$XDG_DATA_HOME/plasmasds``        ``%LOCALAPPDATA%\plasmasds``
          (``~/.local/share/plasmasds``)
config    ``$XDG_CONFIG_HOME/plasmasds``      ``%LOCALAPPDATA%\plasmasds``
          (``~/.config/plasmasds``)
log       ``$XDG_STATE_HOME/plasmasds``       ``%LOCALAPPDATA%\plasmasds``
          (``~/.local/state/plasmasds``)
========  ==================================  =====================================

An ``XDG_*`` variable that is empty or not an absolute path is ignored, as the XDG
specification requires; so is an empty or relative ``LOCALAPPDATA``, which falls back to
``~/AppData/Local``.

Settings come from the packaged ``data/defaults.json``, read on every new process, with
the user's ``config.json`` in :func:`config_dir` applied on top, key by key.
The user file holds only what the user changed, so a release that changes a default
(host, port, server roots, host key) reaches every user who has not overridden it.
"""

import contextlib
import json
import logging
import os
import tempfile
from importlib import resources
from pathlib import Path

from plasmasds_utility.exceptions import ConfigError

_APP = "plasmasds"
_WINDOWS = os.name == "nt"
CONFIG_FILE = "config.json"

logger = logging.getLogger("plasmasds_utility")

_settings = None


def _base(variable, fallback):
    """Return ``$variable/plasmasds``, or ``~/fallback/plasmasds`` if it is unusable."""
    value = os.environ.get(variable, "")
    if value and Path(value).is_absolute():
        return Path(value) / _APP
    return Path.home() / fallback / _APP


def _local_app_data():
    """Return the Windows directory used for data, configuration and the log."""
    return _base("LOCALAPPDATA", Path("AppData", "Local"))


def config_dir():
    """Return the directory that holds the user configuration file.

    Returns
    -------
    pathlib.Path
        The directory; it is not created.
    """
    if _WINDOWS:
        return _local_app_data()
    return _base("XDG_CONFIG_HOME", ".config")


def log_dir():
    """Return the directory that holds the log file.

    Returns
    -------
    pathlib.Path
        The directory; it is not created.
    """
    if _WINDOWS:
        return _local_app_data()
    return _base("XDG_STATE_HOME", Path(".local", "state"))


def default_data_dir():
    """Return the default data directory, which holds one directory per client prefix.

    Returns
    -------
    pathlib.Path
        The directory; it is not created.
    """
    if _WINDOWS:
        return _local_app_data()
    return _base("XDG_DATA_HOME", Path(".local", "share"))


def env_data_dir():
    """Return the data directory set by ``PLASMASDS_DATA_DIR``, if any.

    Returns
    -------
    pathlib.Path or None
        The directory with ``~`` expanded, or None when the variable is unset or empty.

    Raises
    ------
    ConfigError
        If the variable is set to a relative path.
    """
    value = os.environ.get("PLASMASDS_DATA_DIR", "")
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError(f"PLASMASDS_DATA_DIR must be an absolute path, got {value!r}")
    return path


def _defaults():
    """Return the packaged defaults as a dict."""
    text = resources.files("plasmasds_utility").joinpath("data", "defaults.json")
    return json.loads(text.read_text(encoding="utf-8"))


def _read_user(path):
    """Return the contents of the user configuration file, or {} if it does not exist.

    Raises
    ------
    ConfigError
        If the file cannot be read, is not valid JSON, or is not a JSON object.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"{path} is not valid JSON (line {error.lineno}, column {error.colno}): "
            f"{error.msg}"
        ) from error
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path} must contain a JSON object, got {type(data).__name__}"
        )
    return data


def _merge(defaults, user, path):
    """Apply the user settings on top of the defaults and validate the result.

    An unknown key only logs a warning, because clients pin the utility: two clients
    in two environments may share one ``config.json`` while running different versions,
    and a key added by the newer version must not break the older one.
    A known key with a value of the wrong type is an error.

    Raises
    ------
    ConfigError
        If a value has the wrong type, or a working directory is not an absolute path.
    """
    merged = dict(defaults)
    for key, value in user.items():
        if key not in defaults:
            logger.warning(
                "%s: ignoring unknown setting %r (from a newer plasmasds_utility?)",
                path,
                key,
            )
            continue
        expected = type(defaults[key])
        if type(value) is not expected:
            raise ConfigError(
                f"{path}: setting {key!r} must be of type {expected.__name__}, "
                f"got {type(value).__name__}"
            )
        merged[key] = value
    for prefix, directory in merged["working_dirs"].items():
        if not isinstance(directory, str) or not Path(directory).is_absolute():
            raise ConfigError(
                f"{path}: working directory for {prefix!r} must be an absolute path, "
                f"got {directory!r}"
            )
    return merged


def settings():
    """Return the settings: the packaged defaults with the user overrides applied.

    The result is read once per process and cached; saving the user configuration
    clears the cache.

    Returns
    -------
    dict
        The merged settings. Do not modify it.

    Raises
    ------
    ConfigError
        If the user configuration file cannot be read or holds an invalid value.
    """
    global _settings
    if _settings is None:
        path = config_dir() / CONFIG_FILE
        _settings = _merge(_defaults(), _read_user(path), path)
    return _settings


def _write_atomic(path, text):
    """Write text to path through a temporary file in the same directory.

    The temporary file is moved into place with :func:`os.replace`, so readers see
    either the old or the new content. On failure the temporary file is removed and
    an existing file is left unchanged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def save_working_dir(prefix, directory):
    """Save or clear the working directory of one client in the user configuration.

    Other entries in the file, including keys this version does not know, are kept.

    Parameters
    ----------
    prefix : str
        The client prefix.
    directory : str or None
        The absolute working directory, or None to remove the saved entry.

    Raises
    ------
    ConfigError
        If the existing file is invalid, or the file cannot be written.
    """
    global _settings
    settings()  # refuse to rewrite a file that does not validate
    path = config_dir() / CONFIG_FILE
    data = _read_user(path)
    working_dirs = data.setdefault("working_dirs", {})
    if directory is None:
        working_dirs.pop(prefix, None)
    else:
        working_dirs[prefix] = directory
    if not working_dirs:
        del data["working_dirs"]
    try:
        _write_atomic(
            path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
    except OSError as error:
        raise ConfigError(f"cannot write {path}: {error}") from error
    _settings = None
