import json
import os

import pytest

from plasmasds_utility import ConfigError, _config


@pytest.fixture
def config_file():
    return _config.config_dir() / _config.CONFIG_FILE


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_save_creates_the_file_with_overrides_only(config_file, home):
    _config.save_working_dir("renate-od", str(home / "data"))
    assert read_json(config_file) == {"working_dirs": {"renate-od": str(home / "data")}}


def test_saved_file_is_utf8_json_with_a_final_newline(config_file, home):
    _config.save_working_dir("renate-od", str(home / "dáta"))
    raw = config_file.read_bytes()
    assert raw.endswith(b"}\n")
    assert b"\r\n" not in raw
    assert "dáta" in raw.decode("utf-8")


def test_save_clears_the_settings_cache(home):
    assert _config.settings()["working_dirs"] == {}
    _config.save_working_dir("renate-od", str(home / "data"))
    assert _config.settings()["working_dirs"] == {"renate-od": str(home / "data")}


def test_save_keeps_other_entries_and_unknown_keys(config_file, home):
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        json.dumps(
            {
                "port": 2222,
                "future_key": 1,
                "working_dirs": {"synref": str(home / "synref")},
            }
        ),
        encoding="utf-8",
    )
    _config.save_working_dir("renate-od", str(home / "data"))
    assert read_json(config_file) == {
        "port": 2222,
        "future_key": 1,
        "working_dirs": {
            "renate-od": str(home / "data"),
            "synref": str(home / "synref"),
        },
    }


def test_clearing_removes_the_entry(config_file, home):
    _config.save_working_dir("renate-od", str(home / "data"))
    _config.save_working_dir("synref", str(home / "synref"))
    _config.save_working_dir("renate-od", None)
    assert read_json(config_file) == {"working_dirs": {"synref": str(home / "synref")}}


def test_clearing_the_last_entry_removes_working_dirs(config_file, home):
    _config.save_working_dir("renate-od", str(home / "data"))
    _config.save_working_dir("renate-od", None)
    assert read_json(config_file) == {}


def test_clearing_without_a_file_creates_none(config_file):
    _config.save_working_dir("renate-od", None)
    assert not config_file.exists()


def test_clearing_an_unsaved_prefix_leaves_the_file_alone(config_file, home):
    _config.save_working_dir("synref", str(home / "synref"))
    before = config_file.read_bytes()
    _config.save_working_dir("renate-od", None)
    assert config_file.read_bytes() == before


def test_invalid_existing_file_is_not_overwritten(config_file, home):
    config_file.parent.mkdir(parents=True)
    config_file.write_text('{"port": "72"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="setting 'port'"):
        _config.save_working_dir("renate-od", str(home / "data"))
    assert config_file.read_text(encoding="utf-8") == '{"port": "72"}'


def test_failed_replace_keeps_the_old_file_and_no_temporary(
    config_file, home, monkeypatch
):
    _config.save_working_dir("renate-od", str(home / "old"))
    before = config_file.read_bytes()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(ConfigError, match="cannot write .*disk full"):
        _config.save_working_dir("renate-od", str(home / "new"))
    assert config_file.read_bytes() == before
    assert [p.name for p in config_file.parent.iterdir()] == [config_file.name]


def test_failed_write_leaves_no_temporary(home):
    target = home / "out" / "file.json"
    with pytest.raises(TypeError):
        _config._write_atomic(target, 123)  # file.write() fails after the open
    assert list(target.parent.iterdir()) == []


def test_unwritable_location_is_a_config_error(home, monkeypatch):
    blocker = home / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(blocker))
    monkeypatch.setenv("LOCALAPPDATA", str(blocker))
    with pytest.raises(ConfigError):
        _config.save_working_dir("renate-od", str(home / "data"))
