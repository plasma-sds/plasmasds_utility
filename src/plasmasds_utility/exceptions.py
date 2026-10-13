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
    """A connection to the server or a file transfer failed or was refused."""


class AuthError(TransferError):
    """SSH authentication or host key verification failed."""


class AlreadyOnServer(TransferError):
    """An upload would replace a file on the server without archiving it.

    Raised by :meth:`DataClient.upload` when the key is already on the server and
    no ``archive_to`` is given; nothing was replaced. A client that uploads many
    files can catch it to skip those already up. The check is part of the
    upload, so there is no separate check-then-upload step; a file that appears
    on the server *during* an upload still gives a plain :class:`TransferError`,
    as the other upload may not be complete (simultaneous uploads of one key are
    not supported, #29).
    """
