import base64
import hashlib
import json
import logging

import pytest

from plasmasds_utility import ConfigError, _config

# Fingerprint checked by an owner on the server itself (issue #11, 2026-10-06).
VERIFIED_FINGERPRINT = "usX2dUJQMwMew4aipu0soHO1cVN62JtI2L6uYT1oI7o"


@pytest.fixture
def config_file():
    path = _config.config_dir() / _config.CONFIG_FILE
    path.parent.mkdir(parents=True)
    return path


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_defaults_have_the_expected_keys_and_types():
    defaults = _config._defaults()
    assert {key: type(value) for key, value in defaults.items()} == {
        "host": str,
        "port": int,
        "user": str,
        "private_root": str,
        "public_root": str,
        "public_url": str,
        "host_keys": list,
        "working_dirs": dict,
    }
    assert defaults["working_dirs"] == {}


def test_every_setting_has_a_comment_above_it():
    path = _config.resources.files("plasmasds_utility") / "data" / "defaults.toml"
    lines = path.read_text(encoding="utf-8").splitlines()
    for key in _config._defaults():
        (index,) = [i for i, line in enumerate(lines) if line.startswith(f"{key} =")]
        assert lines[index - 1].startswith("#"), f"{key} has no comment"


def test_public_url_uses_https():
    assert _config._defaults()["public_url"].startswith("https://")


def test_shipped_host_key_is_the_verified_one():
    (line,) = _config._defaults()["host_keys"]
    kind, blob = line.split()
    assert kind == "ssh-ed25519"
    digest = hashlib.sha256(base64.b64decode(blob)).digest()
    assert base64.b64encode(digest).decode().rstrip("=") == VERIFIED_FINGERPRINT


def test_without_user_file_settings_are_the_defaults():
    assert _config.settings() == _config._defaults()


def test_user_file_overrides_key_by_key(config_file):
    write_json(config_file, {"port": 2222})
    settings = _config.settings()
    assert settings["port"] == 2222
    assert settings["host"] == _config._defaults()["host"]


def test_settings_are_cached(config_file):
    write_json(config_file, {"port": 2222})
    assert _config.settings()["port"] == 2222
    write_json(config_file, {"port": 3333})
    assert _config.settings()["port"] == 2222


def test_unknown_key_is_ignored_with_a_warning(config_file, caplog):
    write_json(config_file, {"ssh_key": "/somewhere"})
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        settings = _config.settings()
    assert "ssh_key" not in settings
    assert "ignoring unknown setting 'ssh_key'" in caplog.text
    assert str(config_file) in caplog.text


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("port", "72"),
        ("port", True),
        ("port", 72.0),
        ("host", None),
        ("host_keys", "ssh-ed25519 AAAA"),
        ("working_dirs", []),
    ],
)
def test_wrong_type_is_an_error(config_file, key, value):
    write_json(config_file, {key: value})
    with pytest.raises(ConfigError, match=f"setting '{key}' must be of type"):
        _config.settings()


@pytest.mark.parametrize("directory", ["relative/dir", 5, None])
def test_working_dir_must_be_absolute(config_file, directory):
    write_json(config_file, {"working_dirs": {"renate-od": directory}})
    with pytest.raises(ConfigError, match="working directory for 'renate-od'"):
        _config.settings()


def test_absolute_working_dir_is_accepted(config_file, home):
    write_json(config_file, {"working_dirs": {"renate-od": str(home / "data")}})
    assert _config.settings()["working_dirs"] == {"renate-od": str(home / "data")}


def test_invalid_json_names_the_file_and_line(config_file):
    config_file.write_text('{\n  "port": 72\n  "host": "x"\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"is not valid JSON \(line 3") as error:
        _config.settings()
    assert str(config_file) in str(error.value)


@pytest.mark.parametrize("content", ["[]", '"text"', "5", "null"])
def test_top_level_must_be_an_object(config_file, content):
    config_file.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match="must contain a JSON object"):
        _config.settings()


def test_unreadable_file_is_a_config_error(config_file):
    config_file.mkdir()  # a directory where the file should be
    with pytest.raises(ConfigError, match="cannot read"):
        _config.settings()


def test_reading_settings_creates_nothing(home):
    _config.settings()
    assert list(home.iterdir()) == []
