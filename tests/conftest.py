import pytest

from plasmasds_utility import _config

ENV_VARIABLES = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "PLASMASDS_DATA_DIR",
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Point every location the package may touch into a temporary home directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    for variable in ENV_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(_config, "_settings", None)
    yield home
    # Close the log file, or Windows cannot delete the temporary directory.
    _config._stop_logging()
