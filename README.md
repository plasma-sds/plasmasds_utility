# plasmasds_utility

A shared utility package for the plasma-sds synthetic diagnostics (renate, neuro_bes, synref, …).
Its first job is data access: fetching the data files a package needs from the group's data server when they are not available locally, and uploading data to the server on request.

> **Status:** early development, no release yet.
> What works so far: choosing where each client's data is stored (below).
> Downloading and uploading come in the next steps.

## Planned scope (first version)

- Download data files, `.dll` files or `.onnx` networks when they are missing from the expected local path.
- Download from private (SSH key) and public sources.
- Upload local data to the server on request.
- Serve several client packages, each with its own directory on the server and locally.
- Keep the local data tree a mirror of the server tree.

The design and the decisions that amend it are in [issue #6](https://github.com/plasma-sds/plasmasds_utility/issues/6); progress is tracked on the [project board](https://github.com/orgs/plasma-sds/projects/10).

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
data.set_working_dir(None)  # back to the default
```

If a saved working directory is hidden by `PLASMASDS_DATA_DIR` or by an explicit `working_dir`, `set_working_dir` still saves it and logs a warning.

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
