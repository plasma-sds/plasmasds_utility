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


class AlreadyOnServer(TransferError):
    """An upload would replace a file on the server without archiving it.

    Raised by :meth:`DataClient.upload` when the key is already on the server and
    no ``archive_to`` is given; the file was not put in place. A client that
    uploads many files can catch it to skip those already up. A file that
    appears on the server *during* an upload gives a plain :class:`TransferError`
    instead, as the other upload may not be complete.
    """
