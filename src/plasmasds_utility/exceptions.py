"""Exceptions raised by plasmasds_utility.

Every exception the package raises on purpose is a subclass of
:class:`PlasmasdsUtilityError`, so clients can catch all of them at once.
"""


class PlasmasdsUtilityError(Exception):
    """Base class for all errors raised by plasmasds_utility."""


class ConfigError(PlasmasdsUtilityError):
    """The packaged defaults or the user configuration are missing or invalid."""


class PathError(PlasmasdsUtilityError):
    """A key or path cannot be resolved, or would leave its base directory."""


class TransferError(PlasmasdsUtilityError):
    """A connection to the server or a file transfer failed."""


class AuthError(TransferError):
    """SSH authentication or host key verification failed."""
