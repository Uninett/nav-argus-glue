# Changelog

Notable changes to the library will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Add all parent organizations of the organization of a netbox to tags.

## [0.8.2] - 2025-08-21

### Fixed

- Fix regression (crash bug) introduced by v0.8.1.

## [0.8.1] - 2025-07-24

### Fixed

- Only ignore alerts for subjects currently on maintenance.

## [0.8.0] - 2025-04-08

### Added

- Periodic full re-synchronization can now be enabled.  Use the new
  `sync-interval` config option to specify the interval in minutes.
  The default is `1`.  If set to `null`, no full synchronization is
  performed.

### Removed

- The `sync-on-startup` config option has been removed. The new
  `sync-interval` config option replaces it.

## [0.7.1] - 2024-09-13

### Fixed

- Don't crash when Argus introduces new incident attributes.

## [0.7.0] - 2023-09-01

### Added

- Support for configuring API request timeout values in `navargus.yml`

## [0.6.6] - 2023-03-13

### Fixed

- Stop navargus from getting stuck in a CPU-eating tight loop when the controlling eventengine process exits ([#11](https://github.com/Uninett/nav-argus-glue/issues/11)).
- Slightly restructured and updated [README.md](README.md).

## [0.6.5] - 2022-07-07

### Fixed

- Fully empty the eventengine input stream when the last read buffer was
  full. Otherwise, some events would go missing and never be reported to Argus
  (or reported only the next time new data is available).

## [0.6.4] - 2022-06-24

### Fixed

- Attempt to avoid infinite tight loops when select() call exits for other
  reasons than stdin being readable..

## [0.6.3] - 2022-03-25

### Added

- A new configuration option under `filters`:
  - `ignore-stateless`: If set to true, nav-argus-glue will **not** submit
    *stateless* NAV alerts to Argus at all.
- An actual change log :-)
