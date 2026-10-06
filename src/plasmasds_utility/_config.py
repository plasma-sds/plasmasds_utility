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
"""

import os
from pathlib import Path

from plasmasds_utility.exceptions import ConfigError

_APP = "plasmasds"
_WINDOWS = os.name == "nt"


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
