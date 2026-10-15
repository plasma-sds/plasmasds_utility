"""A minimal SFTP server for the tests, serving files from a local directory.

Paths from the client are taken relative to that directory, like an SSH user's home.
It accepts one user with one public key, counts connections, stat requests and
directory listings, and can be told to drop the first connections, to deny access to
paths (or only opening them), or to report a larger size than a file has (which
makes a download look truncated).

Uploads are supported like OpenSSH's server does them: writing files, creating
directories, changing modes, ``rename`` (which refuses an existing target),
``posix-rename@openssh.com`` (which replaces it), ``hardlink@openssh.com`` and removing
files. Each of these counts in ``operations``, can be refused with an SFTP status
through ``refused``, or can drop the connection once through ``drop``.
"""

import collections
import os
import socket
import threading

import paramiko
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface
from paramiko.sftp import CMD_EXTENDED


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
        file = getattr(self, "readfile", None) or self.writefile
        return SFTPAttributes.from_stat(os.fstat(file.fileno()))


class _Server(SFTPServer):
    """paramiko's SFTP server, with OpenSSH's hard-link extension.

    paramiko reads the extension name before it dispatches, so every extension is
    handled here; other requests go to paramiko.
    """

    def _process(self, t, request_number, msg):
        if t != CMD_EXTENDED:
            return super()._process(t, request_number, msg)
        tag = msg.get_text()
        if tag in ("hardlink@openssh.com", "posix-rename@openssh.com"):
            oldpath = msg.get_text()
            newpath = msg.get_text()
            if tag == "hardlink@openssh.com":
                status = self.server.hardlink(oldpath, newpath)
            else:
                status = self.server.posix_rename(oldpath, newpath)
        else:
            status = paramiko.SFTP_OP_UNSUPPORTED
        self._send_status(request_number, status)


class _SFTP(SFTPServerInterface):
    def __init__(self, server, *args, stub, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.stub = stub

    def _local(self, path):
        return os.path.join(self.stub.root, path.lstrip("/"))

    def _fault(self, operation):
        """Count an upload operation; return the status a test asked it to fail with."""
        self.stub.operations[operation] += 1
        if operation in self.stub.drop:
            self.stub.drop.discard(operation)
            self.stub.drop_connections()
            return paramiko.SFTP_FAILURE
        return self.stub.refused.get(operation)

    def _do(self, operation, action, *paths):
        """Run action on the local paths unless the operation is refused."""
        status = self._fault(operation)
        if status is not None:
            return status
        try:
            action(*(self._local(path) for path in paths))
        except OSError as error:
            return SFTPServer.convert_errno(error.errno)
        return paramiko.SFTP_OK

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
        writing = flags & (os.O_WRONLY | os.O_RDWR)
        if path in self.stub.denied or (not writing and path in self.stub.denied_open):
            return paramiko.SFTP_PERMISSION_DENIED
        if writing:
            status = self._fault("write")
            if status is not None:
                return status
        try:
            # Closed by the handle.
            file = open(self._local(path), "wb" if writing else "rb")  # noqa: SIM115
        except OSError as error:
            return SFTPServer.convert_errno(error.errno)
        handle = _Handle(flags)
        handle.filename = self._local(path)
        if writing:
            handle.writefile = file
        else:
            handle.readfile = file
        return handle

    def mkdir(self, path, attr):
        return self._do("mkdir", os.mkdir, path)

    def chattr(self, path, attr):
        # The mode is recorded, because Windows can only make a file read-only.
        self.stub.modes[path] = attr.st_mode
        return self._do("chmod", lambda local: os.chmod(local, attr.st_mode), path)

    def rename(self, oldpath, newpath):
        # Like OpenSSH: link, then unlink, so an existing target is refused.
        def rename(old, new):
            os.link(old, new)
            os.unlink(old)

        return self._do("rename", rename, oldpath, newpath)

    def posix_rename(self, oldpath, newpath):
        return self._do("posix-rename", os.replace, oldpath, newpath)

    def hardlink(self, oldpath, newpath):
        return self._do("hardlink", os.link, oldpath, newpath)

    def remove(self, path):
        return self._do("remove", os.remove, path)


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
        self.operations = collections.Counter()  # upload operations by name
        self.refused = {}  # upload operation name -> SFTP status to answer with
        self.drop = set()  # upload operations that close the connection, once
        self.modes = {}  # path -> the mode a client set
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
            transport.set_subsystem_handler("sftp", _Server, _SFTP, stub=self)
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
