# plasmasds_utility

A shared utility package for the plasma-sds synthetic diagnostics (renate, neuro_bes, synref, …).
Its first job is data access: fetching the data files a package needs from the group's data server when they are not available locally, and uploading data to the server on request.

> **Status:** early development, no release yet.
> What works so far: choosing where each client's data is stored, where each data file goes, downloading private and public data, and checking the server for newer data (below).
> Uploading comes in the next step.

## Planned scope (first version)

- Download data files, `.dll` files or `.onnx` networks when they are missing from the expected local path.
- Download from private (SSH key) and public sources.
- Upload local data to the server on request.
- Serve several client packages, each with its own directory on the server and locally.
- Keep the local data tree a mirror of the server tree.

The design and the decisions that amend it are in [issue #6](https://github.com/plasma-sds/plasmasds_utility/issues/6); progress is tracked on the [project board](https://github.com/orgs/plasma-sds/projects/10).

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
A local public copy is returned once the private server has been asked for the file, at most once per file and session, because private data comes first; `private=False` skips that (for example offline).
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

By default, a local copy is used without asking the server whether it has a newer version; the first time this happens for private data in a session, a notice says so.
To check:

```python
data.get(key, check_server=True)  # download again if the server copy differs
data.get(key, force=True)  # download again regardless (a corrupted local copy)
data.check_updates()  # check every local file of this client
```

The check compares modification time and size, from the local file and from the server (SFTP for private data, an HTTPS `HEAD` request for public data), and downloads again when the server copy is newer or differs in size.
`check_updates()` covers both the private and the public copies.
`check_updates()` prints a short report and returns the files it downloaded again; a file missing on the server is kept with a warning.

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
What each one means, and how server paths are built from them, is explained in the packaged [`defaults.toml`](src/plasmasds_utility/data/defaults.toml).
An invalid value stops the utility with a `ConfigError` that names the file and the setting.
An unknown setting is ignored with a warning, so a file written by a newer version still works with an older one.

### The log

The utility logs what it does to `plasmasds.log` (rotated at 1 MB, three old files kept).
Warnings and errors are also printed to stderr.

## Development

- `master` holds released code and is updated from `development` by the owners only.
- `development` is the integration branch.
  All work happens on feature branches and arrives through pull requests into `development`.
- The development environment is managed with [pixi](https://pixi.sh): `pixi run test` runs the tests and `pixi run lint` checks the code.
- Design questions go to [Discussions](https://github.com/plasma-sds/plasmasds_utility/discussions); planned work is tracked as issues.

This repository is developed largely by LLM coding agents under review by the owners.
The rules agents follow are in [AGENTS.md](AGENTS.md), with Claude Code specifics in [CLAUDE.md](CLAUDE.md).

## License

[LGPL-2.1](LICENSE)
