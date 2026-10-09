# Changelog

Notable changes to this project. Format based on Keep a Changelog; versions follow
semantic versioning.

## [Unreleased]

### Fixed

- `ksm secret get` no longer passes `--no-color`. The Keeper CLI declares
  `--color/--no-color` on the root `ksm` group only, so
  `ksm secret get --no-color --json -- <ref>` was rejected during click argument
  parsing (`Error: No such option '--no-color'.`, exit 2) before any vault call:
  every reference failed to resolve and every variable was dropped for every
  configuration. Colour is already off — `run_secret_cli` sets `NO_COLOR=1` and the
  child has no TTY. Verified against `keeper-secrets-manager-cli` 1.5.0 and 1.0.0.
- The fake `ksm` fixture now asserts the exact child argv
  (`secret get --json -- <ref>`), and a new test pins that invocation, so an option
  the real CLI rejects fails the suite instead of only failing at runtime.
- Custom-field references now resolve. `ksm secret get --json` emits custom fields
  under `custom` (KSM-820 renamed it from `custom_fields`), but extraction only
  scanned `customFields` — a spelling used in record-create payloads, never in that
  output — so `#<custom label>` fell through to "has no value for field ..." and the
  variable was dropped. The scan now covers `fields`, `custom` and both legacy
  spellings.
- Field extraction no longer raises on a non-string field label; an unexpected record
  shape costs the field it belongs to instead of every variable in the fetch (only
  `RuntimeError` is handled by the caller, so an `AttributeError` used to sink the
  whole source).
- Test fixtures now copy the real `ksm secret get --json` record shapes (`uid`,
  `custom`, array-valued fields), which is what let the custom-key bug stay invisible;
  a new test covers the real key, a legacy-spelled record and a malformed label.

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
