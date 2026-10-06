import re

import pytest

import plasmasds_utility
from plasmasds_utility import (
    AuthError,
    ConfigError,
    PathError,
    PlasmasdsUtilityError,
    TransferError,
)


def test_version_is_pep440():
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev\d+)?", plasmasds_utility.__version__)


@pytest.mark.parametrize("error", [ConfigError, PathError, TransferError, AuthError])
def test_errors_derive_from_base(error):
    assert issubclass(error, PlasmasdsUtilityError)


def test_auth_error_is_a_transfer_error():
    assert issubclass(AuthError, TransferError)
