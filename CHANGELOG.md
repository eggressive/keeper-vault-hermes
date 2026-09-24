# Changelog

Notable changes to this project. Format based on Keep a Changelog; versions follow
semantic versioning.

## [1.0.0] - 2026-07-07

Initial release.

### Added

- `KeeperSource`, a Hermes Agent `SecretSource` implementation registering under
  the name `keeper` with the `ksm://` scheme.
- Mapped secret resolution: bind each environment variable to a
  `ksm://<record-ref>[#<field>]` reference, with `password` as the default field.
- Field extraction from Keeper record shapes, including `fields`, `customFields`
  and multi-value file references.
- Allowlisted child environment for the `ksm` CLI: only `KSM_*` variables and the
  bootstrap token variable are passed through, never the full `os.environ`.
- Optional binary pinning through `binary_path`, which bypasses `PATH` when set.
- In-process and on-disk result caching keyed on a truncated SHA-256 fingerprint
  of the auth material and the reference map, so no credential enters the cache
  key. On-disk cache is `ksm_cache.json` under the Hermes home, TTL 300 seconds
  by default, disable with `cache_ttl_seconds: 0`.
- Protection of the bootstrap variables (`token_env`, `KSM_CONFIG`,
  `KSM_CONFIG_BASE64_1`) so a resolved secret cannot overwrite them.
- Per-reference failure isolation: a missing record or field becomes a warning and
  drops that one variable instead of failing the fetch.
- Error classification onto the shared Hermes taxonomy (`TIMEOUT`,
  `BINARY_MISSING`, `AUTH_FAILED`, `EMPTY_VALUE`, `NETWORK`, `INTERNAL`,
  `NOT_CONFIGURED`).
- Test suite exercising the real subprocess, parse and field-extraction path
  against a fake `ksm` CLI, plus conformance against the upstream
  `SecretSourceConformance` base class.
- GitHub Actions workflow (`verify`) running the suite against a pinned Hermes
  Agent commit.

### Notes

- Targets Hermes Agent `main` after `v2026.7.1`, where the pluggable
  `SecretSource` API landed. There is no pinned-compatible release tag yet, which
  is why the workflow pins an exact commit.
- `override_existing` defaults to `true`: an explicit variable binding outranks a
  stale `.env` line, so rotated values take effect without manual cleanup.
