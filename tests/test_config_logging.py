import logging

import pytest

from plasmasds_utility import ConfigError, _config

logger = logging.getLogger("plasmasds_utility")


def log_file():
    return _config.log_dir() / _config.LOG_FILE


def test_no_handlers_until_started():
    assert logger.handlers == []


def test_start_creates_the_directory_but_not_the_file():
    _config.start_logging()
    assert _config.log_dir().is_dir()
    assert not log_file().exists()


def test_info_goes_to_the_file_only(capsys):
    _config.start_logging()
    logger.info("hello file")
    assert "INFO plasmasds_utility: hello file" in log_file().read_text("utf-8")
    assert "hello file" not in capsys.readouterr().err


def test_warning_goes_to_the_file_and_stderr(capsys):
    _config.start_logging()
    logger.warning("look here")
    assert "WARNING plasmasds_utility: look here" in log_file().read_text("utf-8")
    assert "plasmasds_utility WARNING: look here" in capsys.readouterr().err


def test_debug_is_not_logged():
    _config.start_logging()
    logger.debug("noise")
    assert not log_file().exists()


def test_starting_twice_adds_no_handlers():
    _config.start_logging()
    _config.start_logging()
    assert len(logger.handlers) == 2


def test_log_file_rotates_at_1_mb_keeping_three():
    _config.start_logging()
    (handler,) = [h for h in logger.handlers if hasattr(h, "maxBytes")]
    assert (handler.maxBytes, handler.backupCount) == (1_000_000, 3)
    assert handler.baseFilename == str(log_file())


def test_stop_detaches_everything():
    _config.start_logging()
    _config._stop_logging()
    assert logger.handlers == []
    assert logger.level == logging.NOTSET


def test_unusable_log_directory_is_a_config_error(home, monkeypatch):
    blocker = home / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    monkeypatch.setenv("LOCALAPPDATA", str(blocker))
    with pytest.raises(ConfigError, match="cannot create the log directory"):
        _config.start_logging()
