"""Move data between the plasma-sds data server and a local working directory.

The package is under development; see issue #6 for the design.
"""

from plasmasds_utility.exceptions import (
    AuthError,
    ConfigError,
    PathError,
    PlasmasdsUtilityError,
    TransferError,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "AuthError",
    "ConfigError",
    "PathError",
    "PlasmasdsUtilityError",
    "TransferError",
    "__version__",
]
