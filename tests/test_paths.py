from pathlib import Path, PurePosixPath

import pytest

from plasmasds_utility import PathError, _paths

VALID_KEYS = {
    "a.h5": ("a.h5",),
    "atomic_data/Na/rates.h5": ("atomic_data", "Na", "rates.h5"),
    "dummy/x.txt": ("dummy", "x.txt"),
    "a b/c d.txt": ("a b", "c d.txt"),
    "ünï/çødé.h5": ("ünï", "çødé.h5"),
    ".hidden": (".hidden",),
    "a..b": ("a..b",),
    "net.onnx": ("net.onnx",),
    "console.txt": ("console.txt",),
    "com10": ("com10",),
}

# (key, text expected in the error message)
INVALID_KEYS = [
    ("", "empty"),
    ("/abs/x.h5", "leading '/'"),
    ("a//b", "an empty part"),
    ("a/", "an empty part"),
    ("./a", "'.' is not allowed"),
    ("a/./b", "'.' is not allowed"),
    ("../a", "'..' is not allowed"),
    ("a/../b", "'..' is not allowed"),
    ("..", "'..' is not allowed"),
    ("a\\b", "'\\\\' is not allowed"),
    ("..\\a", "'\\\\' is not allowed"),
    ("C:/x", "':' is not allowed"),
    ("C:x", "':' is not allowed"),
    ("a:stream", "':' is not allowed"),
    ("a\x00b", "'\\x00' is not allowed"),
    ("a./x", "ends in a dot or a space"),
    ("a /x", "ends in a dot or a space"),
    ("x.h5.", "ends in a dot or a space"),
    ("NUL", "reserved device name"),
    ("data/con", "reserved device name"),
    ("nul.h5", "reserved device name"),
    ("Com1.tar.gz", "reserved device name"),
    ("lpt9", "reserved device name"),
    ("aux/x", "reserved device name"),
]


@pytest.mark.parametrize(("key", "parts"), VALID_KEYS.items())
def test_valid_keys(key, parts):
    assert _paths.check_key(key) == parts


@pytest.mark.parametrize(("key", "reason"), INVALID_KEYS)
def test_invalid_keys_name_the_key_and_the_reason(key, reason):
    with pytest.raises(PathError, match="invalid data key") as error:
        _paths.check_key(key)
    assert repr(key) in str(error.value)
    assert reason in str(error.value)


@pytest.mark.parametrize("key", [None, 5, b"a.h5", PurePosixPath("a/b"), Path("a")])
def test_keys_must_be_strings(key):
    with pytest.raises(PathError, match="keys are strings with '/' separators, got"):
        _paths.check_key(key)


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("key", VALID_KEYS)
def test_local_path_stays_inside_its_base(home, key, public):
    base = home / "client" / ("public" if public else "private")
    path = _paths.local_path(home / "client", key, public=public)
    assert path.is_relative_to(base)
    assert path != base


def test_local_path_private_and_public(home):
    client = home / "client"
    key = "atomic_data/Na/rates.h5"
    assert _paths.local_path(client, key, public=False) == (
        client / "private" / "atomic_data" / "Na" / "rates.h5"
    )
    assert _paths.local_path(client, key, public=True) == (
        client / "public" / "atomic_data" / "Na" / "rates.h5"
    )


@pytest.mark.parametrize(("key", "reason"), INVALID_KEYS[:4])
def test_local_path_rejects_invalid_keys(home, key, reason):
    with pytest.raises(PathError, match="invalid data key"):
        _paths.local_path(home, key, public=False)
