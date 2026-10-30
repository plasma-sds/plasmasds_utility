import io
import logging
import shlex
import sys

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
    """Set the time seen by progress() with clock.now; give the hint again."""
    clock = type("Clock", (), {"now": 0.0})()
    monkeypatch.setattr(_config, "_clock", lambda: clock.now)
    monkeypatch.setattr(_config, "_hinted", False)
    return clock


MB = 1_000_000


def test_progress_start_line_names_the_size_and_how_to_follow(clock, capsys):
    _config.progress("downloading", "big.h5", 1400 * MB)
    err = capsys.readouterr().err
    assert err.startswith("plasmasds_utility: downloading big.h5 (1400 MB); ")
    assert "tail -F " in err or "Get-Content -Wait" in err
    assert _config.LOG_FILE in err


def test_progress_gives_the_follow_command_once(clock, capsys):
    _config.progress("downloading", "a.h5", 100 * MB)
    _config.progress("downloading", "b.h5", 100 * MB)
    first, second = capsys.readouterr().err.splitlines()
    assert "to follow the progress" in first
    assert second == "plasmasds_utility: downloading b.h5 (100 MB)"


def test_follow_hint_on_windows_and_elsewhere(monkeypatch, tmp_path):
    folder = tmp_path / "it's a log"
    monkeypatch.setattr(_config, "log_dir", lambda: folder)
    path = str(folder / _config.LOG_FILE)
    assert _config._follow_hint(windows=True) == (
        "Get-Content -Wait -Tail 20 '" + path.replace("'", "''") + "'"
    )
    assert _config._follow_hint(windows=False) == f"tail -F {shlex.quote(path)}"


def test_follow_hint_uses_the_platform(monkeypatch):
    monkeypatch.setattr(_config, "_WINDOWS", True)
    assert _config._follow_hint().startswith("Get-Content")
    monkeypatch.setattr(_config, "_WINDOWS", False)
    assert _config._follow_hint().startswith("tail -F")


def test_progress_status_every_minute(clock, capsys):
    report = _config.progress("downloading", "big.h5", 200 * MB)
    capsys.readouterr()
    for now, done in [(30, 30), (61, 60), (100, 100), (125, 120)]:
        clock.now = now
        report(done * MB)
    lines = capsys.readouterr().err.splitlines()
    assert lines == [
        "plasmasds_utility: downloading big.h5: 60 of 200 MB (30 %), 984 kB/s, "
        "about 2 min left",
        "plasmasds_utility: downloading big.h5: 120 of 200 MB (60 %), 960 kB/s, "
        "about 83 s left",
    ]


def test_progress_status_of_a_slow_transfer_in_kb(clock, capsys):
    report = _config.progress("uploading", "big.h5", 200 * MB)
    capsys.readouterr()
    clock.now = 100
    report(4 * MB)
    assert capsys.readouterr().err.endswith(
        "4 of 200 MB (2 %), 40 kB/s, about 82 min left\n"
    )


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


def test_quiet_keeps_warnings_on_stderr(monkeypatch, capsys):
    monkeypatch.setenv("PLASMASDS_QUIET", "1")
    _config.start_logging()
    _config.logger.warning("something went wrong")
    assert "something went wrong" in capsys.readouterr().err


def test_speed():
    assert _config._speed(10_200_000) == "10.2 MB/s"
    assert _config._speed(1_000_000) == "1.0 MB/s"
    assert _config._speed(984_000) == "984 kB/s"


class Terminal(io.StringIO):
    """A stderr that says it is a terminal."""

    def isatty(self):
        return True


@pytest.fixture
def terminal(monkeypatch):
    """Return a function that makes stderr a terminal.

    Called in the test itself: pytest sets its own sys.stderr after the fixtures.
    On Windows, the fake terminal counts as a console that shows colours.
    """

    def use():
        stream = Terminal()
        monkeypatch.setattr("sys.stderr", stream)
        monkeypatch.setattr(_config, "_windows_colours", lambda stream: True)
        return stream

    return use


def test_terminal_gets_coloured_levels(terminal):
    stream = terminal()
    _config.start_logging()
    logger.warning("look here")
    logger.error("broken")
    assert stream.getvalue() == (
        "plasmasds_utility \033[33mWARNING\033[0m: look here\n"
        "plasmasds_utility \033[31mERROR\033[0m: broken\n"
    )


def test_log_file_stays_plain_on_a_terminal(terminal):
    stream = terminal()
    _config.start_logging()
    logger.warning("look here")
    text = log_file().read_text("utf-8")
    assert "WARNING plasmasds_utility: look here" in text
    assert "\033" not in text
    assert "\033" in stream.getvalue()


def test_no_colour_when_stderr_is_not_a_terminal(capsys):
    _config.start_logging()
    logger.warning("look here")
    assert "\033" not in capsys.readouterr().err


@pytest.mark.parametrize(("value", "coloured"), [("1", False), ("", True)])
def test_no_color_variable(terminal, monkeypatch, value, coloured):
    # https://no-color.org: set to anything but empty
    stream = terminal()
    monkeypatch.setenv("NO_COLOR", value)
    _config.start_logging()
    logger.warning("look here")
    assert ("\033" in stream.getvalue()) is coloured


def test_notices_stay_plain_on_a_terminal(terminal):
    stream = terminal()
    _config.notice("for your information")
    assert stream.getvalue() == "plasmasds_utility: for your information\n"


@pytest.fixture
def windows(monkeypatch):
    """Make _colour take its Windows branch, with a stand-in console check.

    Returns the streams the check was asked about, and a dict whose ``"value"``
    is the check's answer.
    """
    calls = []
    answer = {"value": True}

    def check(stream):
        calls.append(stream)
        return answer["value"]

    monkeypatch.setattr(_config.sys, "platform", "win32")
    monkeypatch.setattr(_config, "_windows_colours", check)
    return calls, answer


def test_windows_terminal_colours_when_the_console_can(windows):
    calls, answer = windows
    stream = Terminal()
    assert _config._colour(stream) is True
    answer["value"] = False
    assert _config._colour(stream) is False
    assert calls == [stream, stream]


def test_windows_console_is_left_alone_without_colour(windows, monkeypatch):
    calls, _ = windows
    assert _config._colour(io.StringIO()) is False  # not a terminal
    monkeypatch.setenv("NO_COLOR", "1")
    assert _config._colour(Terminal()) is False
    assert calls == []


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows console")


@windows_only
def test_windows_check_on_streams_that_are_not_consoles(tmp_path):
    assert _config._windows_colours(io.StringIO()) is False  # no file behind it
    with open(tmp_path / "plain.txt", "w") as file:
        assert _config._windows_colours(file) is False  # a file, not a console


@windows_only
def test_windows_check_switches_colours_on_in_a_console():
    import ctypes
    import msvcrt
    from ctypes import wintypes

    try:
        console = open("CONOUT$", "w")  # closed in finally
    except OSError:
        pytest.skip("no console attached")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    handle = msvcrt.get_osfhandle(console.fileno())
    original = wintypes.DWORD()
    try:
        if not kernel32.GetConsoleMode(handle, ctypes.byref(original)):
            pytest.skip("CONOUT$ is not a console")
        kernel32.SetConsoleMode(handle, original.value & ~_config._VT_PROCESSING)
        assert _config._windows_colours(console) is True
        mode = wintypes.DWORD()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        assert mode.value & _config._VT_PROCESSING
    finally:
        kernel32.SetConsoleMode(handle, original.value)
        console.close()
