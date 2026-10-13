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


def progress_lines(caplog):
    return [r.getMessage() for r in caplog.records if " %" in r.getMessage()]


def test_progress_is_logged_every_tenth(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="plasmasds_utility")
    monkeypatch.setattr(_config, "_PROGRESS_SIZE", 100)
    report = _config.progress("downloading", "x.h5", 10_000_000)
    for done in range(0, 10_000_001, 250_000):
        report(done, 10_000_000)
    assert progress_lines(caplog) == [
        f"downloading x.h5: {p} % ({p // 10} of 10 MB)" for p in range(10, 101, 10)
    ]


def test_progress_skips_small_and_unknown_sizes(caplog):
    caplog.set_level(logging.INFO, logger="plasmasds_utility")
    small = _config.progress("downloading", "x.h5", _config._PROGRESS_SIZE - 1)
    unknown = _config.progress("downloading", "x.h5", None)
    for done in (0, 1000, _config._PROGRESS_SIZE):
        small(done)
        unknown(done)
    assert progress_lines(caplog) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", False), ("0", False), ("false", False), ("1", True), ("yes", True)],
)
def test_quiet(monkeypatch, value, expected):
    monkeypatch.setenv("PLASMASDS_QUIET", value)
    assert _config.quiet() is expected


def test_notice_prints_and_logs(capsys, caplog):
    caplog.set_level(logging.INFO, logger="plasmasds_utility")
    _config.notice("something to know")
    assert capsys.readouterr().err == "plasmasds_utility: something to know\n"
    assert "something to know" in caplog.text


def test_quiet_notice_only_logs(monkeypatch, capsys, caplog):
    caplog.set_level(logging.INFO, logger="plasmasds_utility")
    monkeypatch.setenv("PLASMASDS_QUIET", "1")
    _config.notice("something to know")
    assert capsys.readouterr().err == ""
    assert "something to know" in caplog.text


@pytest.fixture
def clock(monkeypatch):
    """Set the time seen by progress() with clock.now."""
    clock = type("Clock", (), {"now": 0.0})()
    monkeypatch.setattr(_config, "_clock", lambda: clock.now)
    return clock


MB = 1_000_000


def test_progress_start_line_names_the_size_and_how_to_follow(clock, capsys):
    _config.progress("downloading", "big.h5", 1400 * MB)
    err = capsys.readouterr().err
    assert err.startswith("plasmasds_utility: downloading big.h5 (1400 MB); ")
    assert "tail -f " in err or "Get-Content -Wait" in err
    assert _config.LOG_FILE in err


def test_follow_hint_on_windows_and_elsewhere():
    path = _config.log_dir() / _config.LOG_FILE
    assert _config._follow_hint(windows=True) == (
        f'Get-Content -Wait -Tail 20 "{path}"'
    )
    assert _config._follow_hint(windows=False).startswith("tail -f ")


def test_progress_status_every_minute(clock, capsys):
    report = _config.progress("downloading", "big.h5", 200 * MB)
    capsys.readouterr()
    for now, done in [(30, 30), (61, 60), (100, 100), (125, 120)]:
        clock.now = now
        report(done * MB)
    lines = capsys.readouterr().err.splitlines()
    assert lines == [
        "plasmasds_utility: downloading big.h5: 60 of 200 MB (30 %), 1.0 MB/s, "
        "about 2 min left",
        "plasmasds_utility: downloading big.h5: 120 of 200 MB (60 %), 1.0 MB/s, "
        "about 83 s left",
    ]


def test_progress_status_says_when_nothing_arrives(clock, capsys):
    report = _config.progress("uploading", "big.h5", 200 * MB)
    capsys.readouterr()
    clock.now = 61
    report(0)
    assert capsys.readouterr().err.endswith("0 of 200 MB (0 %), 0.0 MB/s, stalled\n")


def test_short_transfer_prints_only_the_start_line(clock, capsys):
    report = _config.progress("downloading", "big.h5", 200 * MB)
    for now, done in [(10, 50), (30, 150), (45, 200)]:
        clock.now = now
        report(done * MB)
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_quiet_progress_only_logs(clock, monkeypatch, capsys, caplog):
    caplog.set_level(logging.INFO, logger="plasmasds_utility")
    monkeypatch.setenv("PLASMASDS_QUIET", "1")
    report = _config.progress("downloading", "big.h5", 200 * MB)
    clock.now = 61
    report(60 * MB)
    assert capsys.readouterr().err == ""
    assert "downloading big.h5 (200 MB)" in caplog.text
    assert "60 of 200 MB (30 %)" in caplog.text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(40, "40 s"), (89, "89 s"), (150, "2 min"), (3000, "50 min"), (7500, "2 h 5 min")],
)
def test_duration(seconds, text):
    assert _config._duration(seconds) == text
