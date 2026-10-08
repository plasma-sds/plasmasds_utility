"""A minimal SFTP server for the tests, serving files from a local directory.

Paths from the client are taken relative to that directory, like an SSH user's home.
It accepts one user with one public key, counts connections, stat requests and
directory listings, and can be told to drop the first connections, to deny access to
paths (or only opening them), or to report a larger size than a file has (which
makes a download look truncated).
"""

import collections
import os
import socket
import threading

import paramiko
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface


class _Interface(paramiko.ServerInterface):
    def __init__(self, stub):
        self.stub = stub

    def get_allowed_auths(self, username):
        return "publickey"

    def check_auth_publickey(self, username, key):
        if username == self.stub.user and key.asbytes() == self.stub.client_key_bytes:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _Handle(SFTPHandle):
    def stat(self):
        return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))


class _SFTP(SFTPServerInterface):
    def __init__(self, server, *args, stub, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.stub = stub

    def _local(self, path):
        return os.path.join(self.stub.root, path.lstrip("/"))

    def stat(self, path):
        self.stub.stats[path] += 1
        if path in self.stub.denied:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            attributes = SFTPAttributes.from_stat(os.stat(self._local(path)))
        except OSError as error:
            return SFTPServer.convert_errno(error.errno)
        attributes.st_size += self.stub.extra_size.get(path, 0)
        return attributes

    lstat = stat

    def list_folder(self, path):
        self.stub.listings[path] += 1
        if path in self.stub.denied:
            return paramiko.SFTP_PERMISSION_DENIED
        local = self._local(path)
        try:
            names = os.listdir(local)
        except OSError as error:
            return SFTPServer.convert_errno(error.errno)
        entries = []
        for name in names:
            # Like OpenSSH: a listing describes links themselves (lstat).
            attributes = SFTPAttributes.from_stat(
                os.lstat(os.path.join(local, name)), filename=name
            )
            attributes.st_size += self.stub.extra_size.get(f"{path}/{name}", 0)
            entries.append(attributes)
        return entries

    def open(self, path, flags, attr):
        if path in self.stub.denied or path in self.stub.denied_open:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            file = open(self._local(path), "rb")  # noqa: SIM115 - closed by the handle
        except OSError as error:
            return SFTPServer.convert_errno(error.errno)
        handle = _Handle(flags)
        handle.filename = self._local(path)
        handle.readfile = file
        return handle


class StubSFTPServer:
    """An SFTP server on 127.0.0.1 serving ``root``; see the module docstring."""

    def __init__(self, root, host_key, client_key, user="data"):
        self.root = str(root)
        self.host_key = host_key
        self.client_key_bytes = client_key.asbytes()
        self.user = user
        self.connections = 0
        self.fail_connections = 0
        self.denied = set()
        self.denied_open = set()  # stat works, open is refused
        self.stats = collections.Counter()  # stat requests per path
        self.listings = collections.Counter()  # directory listings per path
        self.extra_size = {}
        self._transports = []
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen()
        self.port = self._socket.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return  # closed
            self.connections += 1
            if self.fail_connections:
                self.fail_connections -= 1
                connection.close()
                continue
            transport = paramiko.Transport(connection)
            transport.add_server_key(self.host_key)
            transport.set_subsystem_handler("sftp", SFTPServer, _SFTP, stub=self)
            self._transports.append(transport)
            try:
                transport.start_server(server=_Interface(self))
            except (paramiko.SSHException, EOFError, OSError):
                transport.close()

    def drop_connections(self):
        """Close every open session from the server side."""
        for transport in self._transports:
            transport.close()

    def close(self):
        self._socket.close()
        for transport in self._transports:
            transport.close()
