from importlib.metadata import version

import pytest

import plasmasds_utility
from plasmasds_utility import (
    AuthError,
    ConfigError,
    PathError,
    PlasmasdsUtilityError,
    TransferError,
)


def test_version_matches_installed_metadata():
    assert version("plasmasds_utility") == plasmasds_utility.__version__


@pytest.mark.parametrize("error", [ConfigError, PathError, TransferError, AuthError])
def test_errors_derive_from_base(error):
    assert issubclass(error, PlasmasdsUtilityError)


def test_auth_error_is_a_transfer_error():
    assert issubclass(AuthError, TransferError)
