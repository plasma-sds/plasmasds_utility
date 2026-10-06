import itertools
import json
import logging
import os
import subprocess
import sys

import pytest

from plasmasds_utility import ConfigError, DataClient, PathError, _config

logger = logging.getLogger("plasmasds_utility")


@pytest.mark.parametrize("prefix", ["renate-od", "neuro_bes", "synref", "a", "v1.2"])
def test_valid_prefixes(prefix):
    assert DataClient(prefix).prefix == prefix


@pytest.mark.parametrize(
    "prefix",
    ["", ".", "..", ".hidden", "-x", "_x", "a/b", "a\\b", "C:", "/abs", "a b", "é"],
)
def test_invalid_prefixes(prefix):
    with pytest.raises(PathError, match="invalid client prefix"):
        DataClient(prefix)


@pytest.mark.parametrize("prefix", [None, 5, b"renate-od"])
def test_prefix_must_be_a_string(prefix):
    with pytest.raises(PathError, match="invalid client prefix"):
        DataClient(prefix)


@pytest.mark.parametrize("prefix", ["config.json", "CONFIG.JSON", "plasmasds.log"])
def test_reserved_prefixes(prefix):
    with pytest.raises(PathError, match="reserved") as error:
        DataClient(prefix)
    assert "config.json" in str(error.value)
    assert "plasmasds.log" in str(error.value)


@pytest.mark.parametrize(
    ("prefix", "reason"),
    [
        ("renate.", "ends in a dot or a space"),
        ("con", "reserved device name"),
        ("NUL", "reserved device name"),
        ("com1.data", "reserved device name"),
    ],
)
def test_prefixes_windows_would_mangle(prefix, reason):
    with pytest.raises(PathError, match=f"invalid client prefix {prefix!r}"):
        DataClient(prefix)
    with pytest.raises(PathError, match=reason):
        DataClient(prefix)


def test_construction_reads_and_writes_nothing(home):
    DataClient("renate-od", working_dir=home / "data")
    assert list(home.iterdir()) == []
    assert logger.handlers == []


def test_import_and_construction_in_a_fresh_process_write_nothing(home):
    code = "import plasmasds_utility; plasmasds_utility.DataClient('renate-od')"
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ.copy())
    assert list(home.iterdir()) == []


def test_first_use_attaches_logging():
    DataClient("renate-od").client_dir()
    assert len(logger.handlers) == 2


def test_default_client_dir(home):
    expected = _config.default_data_dir() / "renate-od"
    assert DataClient("renate-od").client_dir() == expected
    assert not expected.exists()


@pytest.mark.parametrize(
    ("explicit", "env", "saved"), list(itertools.product([False, True], repeat=3))
)
def test_client_dir_precedence(home, monkeypatch, explicit, env, saved):
    if saved:
        _config.save_working_dir("renate-od", str(home / "saved"))
    if env:
        monkeypatch.setenv("PLASMASDS_DATA_DIR", str(home / "env"))
    client = DataClient(
        "renate-od", working_dir=home / "explicit" if explicit else None
    )
    if explicit:
        expected = home / "explicit"
    elif env:
        expected = home / "env" / "renate-od"
    elif saved:
        expected = home / "saved"
    else:
        expected = _config.default_data_dir() / "renate-od"
    assert client.client_dir() == expected


def test_saved_dir_belongs_to_its_prefix_only(home):
    _config.save_working_dir("synref", str(home / "synref"))
    assert DataClient("renate-od").client_dir() == (
        _config.default_data_dir() / "renate-od"
    )


def test_relative_working_dir_is_taken_from_the_current_directory(home, monkeypatch):
    monkeypatch.chdir(home)
    client = DataClient("renate-od", working_dir="data")
    monkeypatch.chdir(home.parent)
    assert client.client_dir() == home / "data"


def test_working_dir_expands_user(home):
    assert DataClient("renate-od", working_dir="~/data").client_dir() == home / "data"


def test_relative_env_dir_is_a_config_error(monkeypatch):
    monkeypatch.setenv("PLASMASDS_DATA_DIR", "relative")
    with pytest.raises(ConfigError, match="PLASMASDS_DATA_DIR"):
        DataClient("renate-od").client_dir()


def test_set_working_dir_creates_saves_and_returns_the_directory(home):
    result = DataClient("renate-od").set_working_dir(home / "new" / "data")
    assert result == home / "new" / "data"
    assert result.is_dir()
    assert DataClient("renate-od").client_dir() == home / "new" / "data"


def test_set_working_dir_is_seen_by_a_new_process(home):
    DataClient("renate-od").set_working_dir(home / "data")
    code = (
        "import plasmasds_utility as p; print(p.DataClient('renate-od').client_dir())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.stdout.strip() == str(home / "data")


def test_set_working_dir_resolves_relative_paths_and_user(home, monkeypatch):
    monkeypatch.chdir(home)
    assert DataClient("renate-od").set_working_dir("rel") == home / "rel"
    assert DataClient("synref").set_working_dir("~/tilde") == home / "tilde"


def test_set_working_dir_accepts_an_existing_directory(home):
    (home / "data").mkdir()
    assert DataClient("renate-od").set_working_dir(home / "data") == home / "data"


def test_set_working_dir_on_a_file_is_a_path_error(home):
    (home / "file").write_text("x", encoding="utf-8")
    with pytest.raises(PathError, match="exists and is not a directory"):
        DataClient("renate-od").set_working_dir(home / "file")
    assert not (_config.config_dir() / _config.CONFIG_FILE).exists()


def test_set_working_dir_below_a_file_is_a_path_error(home):
    (home / "file").write_text("x", encoding="utf-8")
    with pytest.raises(PathError):
        DataClient("renate-od").set_working_dir(home / "file" / "data")


def test_set_working_dir_none_is_a_path_error(home):
    with pytest.raises(PathError, match=r"use clear_working_dir\(\)"):
        DataClient("renate-od").set_working_dir(None)
    assert list(home.iterdir()) == []  # rejected before any I/O


def test_clear_working_dir_restores_the_default(home):
    client = DataClient("renate-od")
    client.set_working_dir(home / "data")
    assert client.clear_working_dir() is None
    assert DataClient("renate-od").client_dir() == (
        _config.default_data_dir() / "renate-od"
    )
    assert (home / "data").is_dir()  # the directory itself is left alone


def test_clear_working_dir_keeps_other_clients(home):
    DataClient("renate-od").set_working_dir(home / "renate")
    DataClient("synref").set_working_dir(home / "synref")
    DataClient("renate-od").clear_working_dir()
    assert DataClient("synref").client_dir() == home / "synref"


def test_clear_working_dir_without_a_saved_one_writes_nothing(caplog):
    with caplog.at_level(logging.INFO, logger="plasmasds_utility"):
        DataClient("renate-od").clear_working_dir()
    assert not (_config.config_dir() / _config.CONFIG_FILE).exists()
    assert "removed" not in caplog.text


def test_clear_working_dir_logs_the_change(home):
    client = DataClient("renate-od")
    client.set_working_dir(home / "data")
    client.clear_working_dir()
    text = (_config.log_dir() / _config.LOG_FILE).read_text("utf-8")
    assert "removed the working directory for 'renate-od'" in text


def test_set_working_dir_logs_the_change(home):
    DataClient("renate-od").set_working_dir(home / "data")
    text = (_config.log_dir() / _config.LOG_FILE).read_text("utf-8")
    assert f"working directory for 'renate-od' set to {home / 'data'}" in text


def test_set_working_dir_warns_when_env_takes_precedence(home, monkeypatch, caplog):
    monkeypatch.setenv("PLASMASDS_DATA_DIR", str(home / "env"))
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        DataClient("renate-od").set_working_dir(home / "data")
    assert "PLASMASDS_DATA_DIR is set and takes precedence" in caplog.text


def test_set_working_dir_warns_when_explicit_dir_takes_precedence(home, caplog):
    client = DataClient("renate-od", working_dir=home / "explicit")
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        client.set_working_dir(home / "data")
    assert "given when it was created" in caplog.text
    assert client.client_dir() == home / "explicit"


def test_set_working_dir_without_precedence_conflict_does_not_warn(home, caplog):
    with caplog.at_level(logging.WARNING, logger="plasmasds_utility"):
        DataClient("renate-od").set_working_dir(home / "data")
    assert caplog.records == []


def test_saved_file_has_the_expected_content(home):
    DataClient("renate-od").set_working_dir(home / "data")
    path = _config.config_dir() / _config.CONFIG_FILE
    assert json.loads(path.read_text("utf-8")) == {
        "working_dirs": {"renate-od": str(home / "data")}
    }


def test_local_path_private_by_default():
    client = DataClient("renate-od")
    assert client.local_path("atomic_data/Na/rates.h5") == (
        client.client_dir() / "private" / "atomic_data" / "Na" / "rates.h5"
    )


def test_local_path_public():
    client = DataClient("renate-od")
    assert client.local_path("a/b.h5", private=False) == (
        client.client_dir() / "public" / "a" / "b.h5"
    )


def test_local_path_follows_the_client_dir(home):
    client = DataClient("renate-od", working_dir=home / "wd")
    assert client.local_path("a.h5") == home / "wd" / "private" / "a.h5"


def test_local_path_creates_nothing_below_the_client_dir():
    client = DataClient("renate-od")
    client.local_path("a/b.h5")
    assert not client.client_dir().exists()


def test_local_path_rejects_an_invalid_key_before_any_io(home):
    with pytest.raises(PathError, match=r"invalid data key '\.\./x': '\.\.'"):
        DataClient("renate-od").local_path("../x")
    assert list(home.iterdir()) == []
    assert logger.handlers == []
