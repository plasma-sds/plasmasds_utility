# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Until 1.0, a release that raises the second number (0.1 to 0.2) may change the interface.

## [Unreleased]

## [0.1.0] - 2026-10-15

The first release.

### Added

- `DataClient`, one per client package and its prefix on the server:
  - `get()` downloads a file when it is missing locally: private data over SFTP with an SSH key, public data over HTTPS without one, with public data as an announced fallback for private data.
    `update="never"` (the default), `"if_newer"` or `"force"` selects whether the server is asked for a newer copy.
  - `check_updates()` checks every local file of the client against the server.
  - `upload()` uploads a local file, replacing a file on the server only when `archive_to` names the key to keep the old one under.
  - `on_server()` asks whether the server has a file, without downloading it; `local_path()` and `client_dir()` tell where files are stored locally.
  - `set_working_dir()` and `clear_working_dir()` choose the client's local directory.
- `set_ssh_key()` saves the SSH key for private data, and `check_connection()` tests access to the data server over HTTPS and over SFTP.
- `show_public_fallbacks()` lists the files for which public data was used in place of private data.
- Exception classes: `PlasmasdsUtilityError` and its subclasses `ConfigError`, `PathError`, `TransferError`, `AuthError` and `AlreadyOnServerError`.
- Settings: packaged defaults with a per-user JSON file of overrides, and the environment variables `PLASMASDS_DATA_DIR`, `PLASMASDS_SERVER_ACCESS` and `PLASMASDS_QUIET`.
- A rotating log file, and progress notices for transfers of 50 MB or more.
- Safety: SSH host keys are verified, transfers have timeouts and bounded retries, downloads are moved into place atomically, and data keys cannot leave their base directory.

[Unreleased]: https://github.com/plasma-sds/plasmasds_utility/compare/v0.1.0...development
[0.1.0]: https://github.com/plasma-sds/plasmasds_utility/releases/tag/v0.1.0
