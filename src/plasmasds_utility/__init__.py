"""Move data between the plasma-sds data server and a local working directory.

How to use it is in the README; the design, and the decisions that amend it,
are in issue #6: https://github.com/plasma-sds/plasmasds_utility/issues/6
"""

from plasmasds_utility.client import (
    DataClient,
    check_connection,
    set_ssh_key,
    show_public_fallbacks,
)
from plasmasds_utility.exceptions import (
    AlreadyOnServerError,
    AuthError,
    ConfigError,
    PathError,
    PlasmasdsUtilityError,
    TransferError,
)

__version__ = "0.2.0.dev0"

__all__ = [
    "AlreadyOnServerError",
    "AuthError",
    "ConfigError",
    "DataClient",
    "PathError",
    "PlasmasdsUtilityError",
    "TransferError",
    "__version__",
    "check_connection",
    "set_ssh_key",
    "show_public_fallbacks",
]
