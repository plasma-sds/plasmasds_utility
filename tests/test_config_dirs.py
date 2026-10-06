import pytest

from plasmasds_utility import ConfigError, _config

LINUX_DEFAULTS = {
    "config_dir": (".config", "plasmasds"),
    "log_dir": (".local", "state", "plasmasds"),
    "default_data_dir": (".local", "share", "plasmasds"),
}
XDG_VARIABLES = {
    "config_dir": "XDG_CONFIG_HOME",
    "log_dir": "XDG_STATE_HOME",
    "default_data_dir": "XDG_DATA_HOME",
}


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(_config, "_WINDOWS", False)


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(_config, "_WINDOWS", True)


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("name", LINUX_DEFAULTS)
def test_linux_defaults(home, name):
    assert getattr(_config, name)() == home.joinpath(*LINUX_DEFAULTS[name])


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("name", XDG_VARIABLES)
def test_linux_xdg_variable_overrides(home, monkeypatch, name):
    monkeypatch.setenv(XDG_VARIABLES[name], str(home / "xdg"))
    assert getattr(_config, name)() == home / "xdg" / "plasmasds"


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("name", XDG_VARIABLES)
@pytest.mark.parametrize("value", ["", "relative/dir"])
def test_linux_ignores_empty_or_relative_xdg_variable(home, monkeypatch, name, value):
    monkeypatch.setenv(XDG_VARIABLES[name], value)
    assert getattr(_config, name)() == home.joinpath(*LINUX_DEFAULTS[name])


@pytest.mark.usefixtures("windows")
@pytest.mark.parametrize("name", LINUX_DEFAULTS)
def test_windows_uses_local_app_data(home, name):
    assert getattr(_config, name)() == home / "AppData" / "Local" / "plasmasds"


@pytest.mark.usefixtures("windows")
@pytest.mark.parametrize("value", ["", "relative"])
def test_windows_falls_back_without_usable_local_app_data(home, monkeypatch, value):
    monkeypatch.setenv("LOCALAPPDATA", value)
    assert _config.config_dir() == home / "AppData" / "Local" / "plasmasds"


@pytest.mark.usefixtures("windows")
def test_windows_ignores_xdg_variables(home, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    assert _config.config_dir() == home / "AppData" / "Local" / "plasmasds"


def test_env_data_dir_unset_or_empty(monkeypatch):
    assert _config.env_data_dir() is None
    monkeypatch.setenv("PLASMASDS_DATA_DIR", "")
    assert _config.env_data_dir() is None


def test_env_data_dir_absolute(home, monkeypatch):
    monkeypatch.setenv("PLASMASDS_DATA_DIR", str(home / "shared"))
    assert _config.env_data_dir() == home / "shared"


def test_env_data_dir_expands_user(home, monkeypatch):
    monkeypatch.setenv("PLASMASDS_DATA_DIR", "~/shared")
    assert _config.env_data_dir() == home / "shared"


def test_env_data_dir_relative_is_an_error(monkeypatch):
    monkeypatch.setenv("PLASMASDS_DATA_DIR", "relative/dir")
    with pytest.raises(
        ConfigError, match="PLASMASDS_DATA_DIR must be an absolute path"
    ):
        _config.env_data_dir()


def test_functions_create_nothing(home):
    for name in LINUX_DEFAULTS:
        getattr(_config, name)()
    assert list(home.iterdir()) == []
