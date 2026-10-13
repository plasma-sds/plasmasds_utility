# plasmasds_utility

A shared utility for the plasma-sds synthetic diagnostics (renate, neuro_bes, synref, bolometer, …): it moves the data files a package needs between the group's data server and the user's computer.

- It downloads a file when it is missing locally: private data over SFTP with an SSH key, public data over HTTPS without one.
- It checks the server for newer versions when asked, and uploads data on request without ever losing a file on the server.
- It serves several client packages, each with its own folder on the server and locally; the local tree mirrors the server.
- It runs on Linux and Windows with Python 3.11 or newer; its only dependency is paramiko.

The design and the decisions that amend it are in [issue #6](https://github.com/plasma-sds/plasmasds_utility/issues/6); progress is tracked on the [project board](https://github.com/orgs/plasma-sds/projects/10).

## Installing

```bash
pip install plasmasds_utility
```

The development version, from GitHub (this needs `git`): `pip install "plasmasds_utility @ git+https://github.com/plasma-sds/plasmasds_utility@development"`.
To check that it can reach the data server, run `python -c "import plasmasds_utility; plasmasds_utility.check_connection()"` (see [Troubleshooting](https://github.com/plasma-sds/plasmasds_utility#troubleshooting)).

## Using it in a client package

A client package declares the dependency in its `pyproject.toml`:

```toml
[project]
dependencies = [
    # up to the next release that may change the interface
    "plasmasds_utility>=0.1,<0.2",
]
```

A client managed with pixi can list it under `[tool.pixi.pypi-dependencies]` instead, as `plasmasds_utility = ">=0.1,<0.2"`.
Until 1.0, a release that raises the second number (0.1 to 0.2) may change the interface, so the upper bound keeps a client working until it is updated.

The client creates one `DataClient` for its own folder on the server, its *prefix*, usually its package name, and asks it for files wherever it needs them:

```python
# mypackage/data.py
from plasmasds_utility import DataClient

data = DataClient("mypackage")  # reads and writes nothing until first used


def rates(element):
    return data.get(f"atomic_data/{element}/rates.h5")
```

The client's folders on the server are created by its first upload.

The environment variable `PLASMASDS_SERVER_ACCESS` limits which servers are contacted:

- `all` (the default): both, as described below.
- `public`: only the public server, over HTTPS; `get(key)` uses a local private copy if there is one, and public data otherwise.
  This is the default when the `CI` variable is set (to anything but `0` or `false`), as in GitHub Actions and GitLab CI, so a client's tests there never try SFTP and need no setup.
  On a CI service that does not set `CI`, such as Jenkins or Azure Pipelines, set `PLASMASDS_SERVER_ACCESS=public` in the job.
- `local`: no server at all; `get(key)` returns local copies only, and a file that is not on disk raises an error at once.
  Set it on machines without network, for example in cluster job scripts: `PLASMASDS_SERVER_ACCESS=local python run.py`.

Runnable examples for a terminal and for a notebook are in [`examples/`](https://github.com/plasma-sds/plasmasds_utility/tree/master/examples).

## Getting data

```python
import h5py
from plasmasds_utility import DataClient

data = DataClient("renate-od")
path = data.get("atomic_data/Na/rates.h5")  # local path; downloaded if missing
with h5py.File(path) as f:  # the client package opens the file itself
    ...
```

`get` returns the local path of a data file, downloading it first if needed; it does not open the file.
Client packages call it every time they need a file: when the file is already on disk, that costs a single `stat`.
It returns the first of these it finds:

1. the local private copy;
2. the file on the private server, downloaded over SFTP (needs an SSH key);
3. the local public copy;
4. the file on the public server, downloaded over HTTPS.

A local private copy is returned straight away, without contacting a server.
A local public copy is returned once the private server has been asked for the file, at most once per file and session, because private data comes first; `private=False` skips that; on a machine without network, set `PLASMASDS_SERVER_ACCESS=local` (see [Using it in a client package](https://github.com/plasma-sds/plasmasds_utility#using-it-in-a-client-package)).
Downloads are written to a temporary file and moved into place only when complete, and the local file keeps the server's modification time.

To choose the source, pass `private`:

```python
data.get(key)  # best available: private if you have access, else public
data.get(key, private=True)  # private only: raises instead of using public data
data.get(key, private=False)  # public only: never contacts the private server
```

With the default, private data gives way to public data only when you have no access to it (no key, key rejected, a host key that is unknown or does not match; remembered for the session) or the file is not on the private server.
Any other failure, such as a timeout, is raised, so public data never silently replaces private data.
Such fallbacks are announced: the first one in a session with a warning, every one in the log file, and all of them in a summary when the program ends.
To list them at any time, for example at the end of a notebook:

```python
plasmasds_utility.show_public_fallbacks()
```

### Newer data on the server

By default, a local copy is used without asking the server whether it has a newer version; a file that is not on disk is always downloaded.
The `update` argument changes what happens to a local copy, and the first `get()` in a session prints these options:

```python
data.get(key)  # update="never": use the local copy as it is
data.get(key, update="if_newer")  # download again if the server copy is newer
data.get(key, update="force")  # download again regardless, e.g. a damaged copy
data.check_updates()  # check every local file of this client ("if_newer")
```

The check compares the modification time of the local file with the server's (SFTP for private data, an HTTPS `HEAD` request for public data, which must send `Last-Modified`) and downloads again only when the server copy is more than 2 seconds newer.
A local copy that is newer than the server's is never overwritten by a check, so data you are testing locally is safe; a copy with the same modification time but a different size is kept with a warning.
`check_updates()` covers both the private and the public copies, lists each private server directory once, and prints a short report to stderr; it returns the files it downloaded again, and a file missing on the server is kept with a warning.
It follows symbolic links inside the client directory, so data kept on another disk and linked in is checked too, each real folder once; a link to a folder that contains the client directory (the home folder, say) and a broken link, for example to a disk that is not mounted, are skipped with a warning.
A newer copy of a file in a linked folder goes into that folder, which must be writable, while a linked file is replaced by a regular file and the file it pointed to is left alone.

**For whoever manages the server:** when an older version of a file is restored or re-published (for example with `rsync -a`, `cp -p` or `tar`, which keep the old modification time), give it a fresh modification time with `touch`.
Otherwise users' checks see their copy as newer and keep it.

### Large files

Transfers stream to and from disk: downloading a 1.1 GB file over SFTP took under 70 MB of memory in a test.
A transfer of 50 MB or more prints when it starts, with its size (and, the first time, the command that follows the [log](https://github.com/plasma-sds/plasmasds_utility#the-log)), and then, while data arrives, a line at least a minute apart with how much is done, the average speed and the time left:

```text
plasmasds_utility: downloading private_html/bolometer/aug-axuv/raytransfer/raytransfer_S16_refl.h5 (1400 MB); to follow the progress in the log: tail -F /home/me/.local/state/plasmasds/plasmasds.log
plasmasds_utility: downloading private_html/bolometer/aug-axuv/raytransfer/raytransfer_S16_refl.h5: 612 of 1400 MB (43 %), 10.2 MB/s, about 77 s left
```

On Windows the command is `Get-Content -Wait -Tail 20 '<log file>'`, in PowerShell.
The log also gets a line every 10 %.
A transfer that stops receiving data fails after 30 seconds without data and is tried again, with a warning.
A transfer that fails starts again from the beginning, up to three times; resuming is planned in [#22](https://github.com/plasma-sds/plasmasds_utility/issues/22).
Processes that ask for the same missing file at the same time each download it, so when many processes run in parallel, get the files they need once before starting them.

### Private data

Private data needs an SSH key that the data server accepts.
The utility uses the key saved with `set_ssh_key`, the keys in your SSH agent, and the standard `~/.ssh/id_*` files:

```python
import plasmasds_utility

plasmasds_utility.set_ssh_key("~/.ssh/plasmasds_deep")  # saved for every client
plasmasds_utility.set_ssh_key(None)  # back to the agent and ~/.ssh/id_*
```

It never asks for a passphrase: load a key with a passphrase into the SSH agent (`ssh-add`); a saved key that has one is then used through the agent.
A failed login is remembered for the session, so after fixing it (for example with `ssh-add`), restart Python (or the Python kernel).
The server's host key is checked against your `~/.ssh/known_hosts` first, and against the key shipped with the package if that file has no entry for the server; an unknown host is rejected.
If the check fails although the server is genuine (for example after a reinstall), remove the server's line from `~/.ssh/known_hosts`.

## Uploading data

```python
data.upload(key)  # the local private copy, to the private server
data.upload(key, private=False)  # the local public copy, to the public server
```

`upload` sends the local copy of a key (see [Where files go](https://github.com/plasma-sds/plasmasds_utility#where-files-go)) to the same key on the server.
It uses SFTP for both trees, so it needs an SSH key (see [Private data](https://github.com/plasma-sds/plasmasds_utility#private-data)) even for public data.

An upload never loses data on the server: a file that is already there is replaced only when you name another key, in the same tree, to keep the old one under.
For example, a release of renate keeps the previous reference results with the version that made them:

```python
data.upload(
    "test_dataset/crm_systemtests/actual/case1.h5",
    archive_to="test_dataset/crm_systemtests/archive/1.3.1/case1.h5",
)
```

Each client package chooses its own archive layout.
Nothing may be at `archive_to` yet; if there is no file to archive, the upload goes ahead with a warning.
Replacing a file with one of the same size also gives a warning, in case it is the same file: uploading it again only makes every user download it again.
The content is not compared, as new data often keeps the size of the old (HDF5 files, for example).
The old file is not copied: it gets a second name on the server (a hard link), so archiving costs no space or time.
Do not upload the same key from two places at once: the server has no locks, so one of two simultaneous replacements can be lost.

An upload refused because the key is already there raises `AlreadyOnServerError`, so a client uploading many files can skip those already up:

```python
from plasmasds_utility import AlreadyOnServerError

for key in keys:
    try:
        data.upload(key)
    except AlreadyOnServerError:
        pass  # already up
```

To ask whether the server has a file without downloading it, use `on_server`; it raises when the server cannot be asked, so "cannot tell" is never `False`:

```python
key = "aug-axuv/geometry/AXUV_LOS_geom.txt"
data.on_server(key)  # the private server
data.on_server(key, private=False)  # the public server, over HTTPS
```

The file is uploaded to a temporary name next to its target (`.<name>.<random>.part`) and renamed into place only when complete, so people downloading it get either the complete old or the complete new file.
If a step fails, the message says which one, whether the file on the server is unchanged, and what may be left behind; a source file that changes during the upload is refused.
Uploaded files are readable by everyone on the server (mode 0644, directories 0755), which public data needs to be served over HTTPS.
The local copy is given the server's modification time, the time of the upload: `get(key, update="if_newer")` does not download your own upload again, while other users' copies are older and are updated by `update="if_newer"` or `check_updates()`.

**For whoever manages the server:** the utility never deletes files on the server.
A `.part` file left behind by a failed or interrupted upload (the message names it) can be deleted by hand; in the public tree it can also be downloaded until then.
An archive name that a failed upload says may be left is a second name of the live file if both have the same content: delete it, never write over it in place, because writing over one name changes both.

## Where files go

Each client package has a *prefix*: its directory name on the data server, for example `renate-od`.
The utility keeps that client's data in a *client directory*, and its own configuration and log in per-user locations:

| | Linux | Windows |
|---|---|---|
| Data (default) | `~/.local/share/plasmasds/<prefix>/` | `%LOCALAPPDATA%\plasmasds\<prefix>\` |
| Configuration | `~/.config/plasmasds/config.json` | `%LOCALAPPDATA%\plasmasds\config.json` |
| Log | `~/.local/state/plasmasds/plasmasds.log` | `%LOCALAPPDATA%\plasmasds\plasmasds.log` |

On Linux, `$XDG_DATA_HOME`, `$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` replace `~/.local/share`, `~/.config` and `~/.local/state` when they are set.
Nothing is ever written inside the installed package.

On Linux and macOS, downloaded files get the permissions any new file of yours gets in that folder (`0666` minus your umask, usually `0644`, or what the folder's default ACL says), so a data folder shared with a group, through `PLASMASDS_DATA_DIR` or `set_working_dir`, is readable by its members; if the folder is group-writable and setgid to the group, they can also update the files in it.

### Choosing the client directory

The first of these that is set wins:

1. a `working_dir` passed to `DataClient`, for that object only;
2. the `PLASMASDS_DATA_DIR` environment variable, which moves every client at once (to `$PLASMASDS_DATA_DIR/<prefix>/`); it must be an absolute path;
3. a working directory saved with `set_working_dir`, which applies to that client from then on, in every session;
4. the default data directory in the table above.

```python
from plasmasds_utility import DataClient

data = DataClient("renate-od")
data.client_dir()  # where renate-od's data is kept
data.set_working_dir("~/renate-data")  # created if needed, and remembered
data.clear_working_dir()  # back to the default
```

If a saved working directory is hidden by `PLASMASDS_DATA_DIR` or by an explicit `working_dir`, `set_working_dir` still saves it and logs a warning.

### Data keys

A data file is named by its *key*: its path below the client's directory on the server, with `/` separators on every platform.
The key is the same on the server and locally, so the local tree mirrors the server:

```python
data.local_path("atomic_data/Na/rates.h5")
# server:  private_html/renate-od/atomic_data/Na/rates.h5
# local:   <client directory>/private/atomic_data/Na/rates.h5
data.local_path("atomic_data/Na/rates.h5", private=False)
# server:  https://deep.reak.bme.hu/~data/renate-od/atomic_data/Na/rates.h5
# local:   <client directory>/public/atomic_data/Na/rates.h5
```

A key must be relative and may not contain `..`, `.`, empty parts, the characters `\ : < > " | ? *` or control characters, parts ending in a dot or a space, or Windows device names such as `NUL` or `com1.txt`.
The rules are the same on every platform, so a key that works on Linux also works on Windows; an invalid key raises `PathError` naming the part that is wrong.

### The configuration file

`config.json` holds only the settings you changed; everything else comes from the defaults shipped with the package, so updated defaults reach you with a new release.
The file does not exist until something is saved, for example by `set_working_dir`.
You can also edit it by hand:

```json
{
  "working_dirs": {
    "renate-od": "/home/me/renate-data"
  }
}
```

The other settings it may override are `host`, `port`, `user`, `private_root`, `public_root`, `public_url` and `host_keys`; a value must have the same type as the default.
What each one means, and how server paths are built from them, is explained in the packaged [`defaults.toml`](https://github.com/plasma-sds/plasmasds_utility/blob/master/src/plasmasds_utility/data/defaults.toml).
An invalid value stops the utility with a `ConfigError` that names the file and the setting.
An unknown setting is ignored with a warning, so a file written by a newer version still works with an older one.

### The log

The utility logs what it does to `plasmasds.log` (rotated at 1 MB, three old files kept).
Warnings and errors are also printed to stderr, and so are a few notices: the first `get()` in a session explains its options, a summary at exit lists the public fallbacks, and large transfers show their progress.
To keep the notices in the log only, for example when many processes run in parallel, set the environment variable `PLASMASDS_QUIET` to anything but empty, `0` or `false` (for example `1`); warnings and errors are still printed.
This concerns the utility's own output: a program that sends all log messages to the terminal itself, for example with `logging.basicConfig(level=logging.INFO)`, still shows them.

## Troubleshooting

Start with the connection check, in Python or a notebook:

```python
import plasmasds_utility

plasmasds_utility.check_connection()
```

It downloads a small test file kept on the server, over HTTPS and over SFTP, into a temporary folder, and prints the settings in use and what failed:

```text
plasmasds_utility <version> connection check
  server: data@deep.reak.bme.hu, port 72; public data: https://deep.reak.bme.hu/~data
  SSH key: none saved (the SSH agent and ~/.ssh/id_* keys)
  data folder: /home/me/.local/share/plasmasds
  log file: /home/me/.local/state/plasmasds/plasmasds.log
  server access: all
  public data (HTTPS): ok
  private data (SFTP): failed: no SSH key found for data@[deep.reak.bme.hu]:72: ...
```

The check always connects afresh, so after fixing something, run it again to see whether the fix worked.
The report holds no secrets (the SSH key appears only as a file name), so it can be sent along with a question.
The usual failures:

- **"no SSH key found"** or **"SSH login … failed":** save the key the server accepts with `plasmasds_utility.set_ssh_key()`; if you have none, ask the server's admins to add your public key.
- **"the SSH key … does not exist"** or **"cannot read … as an SSH private key":** the saved key file is gone, or is not a private key (for example the `.pub` file); save the right one with `set_ssh_key()`.
- **"has a passphrase":** load the key into the SSH agent with `ssh-add`.
- **"remembered until then":** a login or host key failure is remembered for the session, so restart Python (or the Python kernel) after fixing it; `set_ssh_key()` takes effect at once.
- **"the host key … does not match":** if the server was reinstalled, remove its line from `~/.ssh/known_hosts`, or update plasmasds_utility if the message is about the key shipped with it; otherwise do not connect, as someone may be impersonating the server.
- **"… is not a known host":** the server's host key is neither in `~/.ssh/known_hosts` nor shipped with the package, usually because `host` or `port` was changed in the configuration file.
- **A timeout or "Unable to connect to port 72", for private data only:** the network blocks the SSH port; try another network or a VPN.
  Public data uses HTTPS, which goes through a proxy set in `HTTPS_PROXY`; SFTP does not.
- **"CERTIFICATE_VERIFY_FAILED" for public data:** a network that inspects HTTPS traffic replaces the server's certificate; ask the network's admins, or use another network.
- **"not contacting the server over SFTP"**, **"not contacting the public server"** or **"no server is contacted":** `PLASMASDS_SERVER_ACCESS` is `public` or `local`, or `CI` is set; the message says which.
  Unset it, or set it to `all`, to use the servers.
- **"the test file … is missing":** the server was reached; tell the server's admins.
  (For them: the check needs `plasmasds_utility/test/connection_test.txt` in both trees, with the content `plasmasds_utility connection test`; the tests use the same folder for files of their own, which they remove themselves.)

The [log](https://github.com/plasma-sds/plasmasds_utility#the-log) has the details of every failure.

## Development

- `master` holds released code and is updated from `development` by the owners only.
- `development` is the integration branch.
  All work happens on feature branches and arrives through pull requests into `development`.
- Design questions go to [Discussions](https://github.com/plasma-sds/plasmasds_utility/discussions); planned work is tracked as issues.

The development environment is managed with [pixi](https://pixi.sh), whose configuration is in `pyproject.toml`:

```bash
pixi run test  # the tests, on the newest supported Python
pixi run -e py311 test  # on one Python version, py311 to py314
pixi run lint  # ruff lint and format check
```

Without pixi, a virtual environment works too:

```bash
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install -e . pytest "ruff>=0.15" nbclient ipykernel
pytest
ruff check .
ruff format --check .
```

Tests marked `network` download from the public data server and run by default; offline, use `pytest -m "not private and not network"`.
Tests marked `private` need an SSH key for the data server and are deselected by default; the [agent instructions](https://github.com/plasma-sds/plasmasds_utility/blob/master/AGENTS.md#commands) explain how to run them.

This repository is developed largely by LLM coding agents under review by the owners.
The rules agents follow are in [AGENTS.md](https://github.com/plasma-sds/plasmasds_utility/blob/master/AGENTS.md), with Claude Code specifics in [CLAUDE.md](https://github.com/plasma-sds/plasmasds_utility/blob/master/CLAUDE.md).

## License

[LGPL-2.1](https://github.com/plasma-sds/plasmasds_utility/blob/master/LICENSE)
