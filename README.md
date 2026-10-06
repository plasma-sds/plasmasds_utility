# plasmasds_utility

A shared utility package for the plasma-sds synthetic diagnostics (renate, neuro_bes, synref, …).
Its first job is data access: fetching the data files a package needs from the group's data server when they are not available locally, and uploading data to the server on request.

> **Status:** early development.
> There is no usable code or release yet.

## Planned scope (first version)

- Download data files, `.dll` files or `.onnx` networks when they are missing from the expected local path.
- Download from private (SSH key) and public sources.
- Upload local data to the server on request.
- Serve several client packages, each declaring its own data paths in a JSON file.
- Keep the local data tree a mirror of the server tree.

The design and the decisions that amend it are in [issue #6](https://github.com/plasma-sds/plasmasds_utility/issues/6); progress is tracked on the [project board](https://github.com/orgs/plasma-sds/projects/10).

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
