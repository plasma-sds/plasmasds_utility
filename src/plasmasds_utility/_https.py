"""Download public files over HTTPS, streaming them to disk.

Downloads are written through :func:`plasmasds_utility._files.writing`, so a failed
or interrupted transfer never leaves a partial file under the target name.
"""

import email.utils
import http.client
import shutil
import time
import urllib.error
import urllib.request

from plasmasds_utility import _files
from plasmasds_utility._config import logger
from plasmasds_utility.exceptions import TransferError

_CHUNK = 1024 * 1024


def _server_time(last_modified, url):
    """Return the Last-Modified header as a POSIX timestamp, or None with a warning."""
    try:
        return email.utils.parsedate_to_datetime(last_modified).timestamp()
    except (TypeError, ValueError):
        logger.warning(
            "%s sent no usable Last-Modified header (%r); the local copy keeps the "
            "download time, so the update check cannot compare it with the server",
            url,
            last_modified,
        )
        return None


def _content_length(headers, url):
    """Return the Content-Length header as an int, or None if absent or malformed."""
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning(
            "%s sent a malformed Content-Length (%r); the size is not checked",
            url,
            value,
        )
        return None


def _fetch_once(url, target, timeout):
    """Download url to target once.

    Raises HTTPError for an HTTP error status, and OSError or HTTPException for
    connection problems, timeouts and truncated transfers.
    """
    with _files.writing(target) as partial:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            expected = _content_length(response.headers, url)
            last_modified = response.headers.get("Last-Modified")
            shutil.copyfileobj(response, partial.file, _CHUNK)
        received = partial.file.tell()
        # A server that closes early gives a short read, not an exception.
        if expected is not None and received != expected:
            raise ConnectionError(
                f"transfer cut short: received {received} of {expected} bytes"
            )
        partial.mtime = _server_time(last_modified, url)


def download(url, target, *, timeout=30, attempts=3, backoff=1.0):
    """Download a public file over HTTPS to target.

    The local file gets the server's modification time from the ``Last-Modified``
    header, which the update check relies on.

    Parameters
    ----------
    url : str
        The URL of the file.
    target : pathlib.Path
        Where to store it; missing directories are created, and an existing file is
        replaced only when the download is complete.
    timeout : float, default 30
        Seconds to wait for the connection and for each read.
    attempts : int, default 3
        How many times to try. Connection errors, timeouts, truncated transfers and
        server errors (HTTP 5xx) are retried; other HTTP errors are not.
    backoff : float, default 1.0
        Seconds to wait before the second attempt; the wait doubles after each one.

    Returns
    -------
    pathlib.Path
        ``target``.

    Raises
    ------
    PathError
        If the target directory cannot be created or the file cannot be written.
    ValueError
        If ``attempts`` is less than 1.
    TransferError
        If the download fails; the message names the URL and the reason.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    _files.make_parent(target)
    if not url.startswith("https://"):
        logger.warning("downloading over an unencrypted connection: %s", url)
    for attempt in range(1, attempts + 1):
        try:
            _fetch_once(url, target, timeout)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise TransferError(
                    f"{url} is not on the public server (HTTP 404)"
                ) from error
            if error.code < 500:
                raise TransferError(
                    f"cannot download {url}: HTTP {error.code} {error.reason}"
                ) from error
            last_error = error
        except (OSError, http.client.HTTPException) as error:
            last_error = error
        else:
            logger.info("downloaded %s to %s", url, target)
            return target
        logger.warning(
            "download of %s failed (attempt %d of %d): %s",
            url,
            attempt,
            attempts,
            last_error,
        )
        if attempt < attempts:
            time.sleep(backoff * 2 ** (attempt - 1))
    raise TransferError(
        f"cannot download {url} after {attempts} attempts: {last_error}"
    ) from last_error
