import pytest

from plasmasds_utility import ConfigError, _config


def test_all_by_default():
    assert _config.server_access() == "all"
    assert _config.server_access_note() == "server access is all"


@pytest.mark.parametrize("value", ["true", "1", "yes", "True"])
def test_public_in_ci(monkeypatch, value):
    monkeypatch.setenv("CI", value)
    assert _config.server_access() == "public"
    assert "because CI is set" in _config.server_access_note()


@pytest.mark.parametrize("value", ["", "0", "false", "False"])
def test_ci_switched_off(monkeypatch, value):
    monkeypatch.setenv("CI", value)
    assert _config.server_access() == "all"


@pytest.mark.parametrize("value", ["all", "public", "local"])
def test_the_variable_wins_over_ci(monkeypatch, value):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", value)
    assert _config.server_access() == value
    assert _config.server_access_note().startswith(
        f"server access is {value} (PLASMASDS_SERVER_ACCESS={value}"
    )
    if value != "all":
        assert "unset it, or set it to all" in _config.server_access_note()


def test_empty_variable_counts_as_unset(monkeypatch):
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", "")
    assert _config.server_access() == "all"


@pytest.mark.parametrize("value", [" Public ", "LOCAL", "All"])
def test_case_and_spaces_are_ignored(monkeypatch, value):
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", value)
    assert _config.server_access() == value.strip().lower()


@pytest.mark.parametrize("value", ["none", "pub", "offline"])
def test_other_values_are_refused(monkeypatch, value):
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", value)
    with pytest.raises(ConfigError, match="must be one of all, public, local"):
        _config.server_access()


def test_read_on_every_call(monkeypatch):
    assert _config.server_access() == "all"
    monkeypatch.setenv("PLASMASDS_SERVER_ACCESS", "local")
    assert _config.server_access() == "local"
