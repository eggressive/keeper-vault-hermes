# Changelog

Notable changes to this project. Format based on Keep a Changelog; versions follow
semantic versioning.

## [Unreleased]

### Fixed

- Auth is read from the **per-fetch environment** Hermes installs for the profile being
  served (`agent.secret_sources.base.get_source_environment`) instead of the process
  environment, and the `ksm` child's `KSM_*` variables are passed by value from that
  same view. Under multiplexing (one gateway process serving several profiles) the old
  code did two wrong things at once: a token living only in a routed profile's `.env`
  never reached the child, and the child could be handed *another* profile's
  `KSM_CONFIG`/`KSM_CONFIG_BASE64_1` — which outranks `KSM_TOKEN` in the CLI's own
  precedence order, so the wrong credential could be the one that won. Builds without
  the environment-view hook keep working against the process environment.
- The in-process fetch cache is now keyed by `home_path` as well as the auth and
  reference fingerprints. The gateway is one long-lived process, so a `HERMES_HOME`
  switch inside it (`hermes --profile ...`, gateway child, cron) could be served the
  previous profile's cached values: the second profile's `ksm` child was never even
  started. The on-disk cache is already stored per home, so it stays out of that key
  and existing cache files keep working.
- `ksm secret get` no longer passes `--no-color`. The Keeper CLI declares
  `--color/--no-color` on the root `ksm` group only, so
  `ksm secret get --no-color --json -- <ref>` was rejected during click argument
  parsing (`Error: No such option '--no-color'.`, exit 2) before any vault call:
  every reference failed to resolve and every variable was dropped for every
  configuration. Colour is already off — `run_secret_cli` sets `NO_COLOR=1` and the
  child has no TTY. Verified against `keeper-secrets-manager-cli` 1.5.0 and 1.0.0.
- Test-harness fidelity: the tests now hand each source the per-fetch environment the
  loader builds (process env's global names plus the profile's `.env`), as
  `hermes_cli.env_loader` does, rather than an empty dict; the fake `ksm` refuses to
  resolve without a credential; and three tests pin the new behaviour (a custom
  `token_env`, auth coming from the view while a sibling profile's config stays out of
  the child, and one `ksm` child per home).
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
- Record titles can be resolved again, via an explicit prefix: `ksm://title:<title>`.
  `ksm secret get` treats a positional argument as a record UID and sends it to Keeper
  as a server-side record filter, so the previously documented bare-title form could
  never match — it failed with `Cannot find requested record(s).` for every install.
  Titles are matched client-side by `-t/--title`, which the prefix selects; a UID never
  contains `:`, so the prefix cannot be confused with one. References without the
  prefix keep meaning a UID.
- A title that matches more than one record is refused with a warning naming the
  candidates, instead of binding the variable to whichever record came back first.
- A UID lookup that matches nothing now appends the fix to the warning
  (`... looked up as a record UID; to resolve it by title use ksm://title:<title>`),
  and an empty title (`ksm://title:`) is reported as the config error it is.
- A bootstrap token held under a custom `token_env` name now reaches `ksm`. `ksm`
  reads its credential from `KSM_TOKEN` (after `KSM_CONFIG` and
  `KSM_CONFIG_BASE64_1`), but the plugin only forwarded the configured variable
  *name* through the child-environment allowlist and let the host resolve its value
  from its own environment. So with `token_env: MY_KEEPER_TOKEN` the child inherited a
  variable the CLI ignores, and every lookup failed to authenticate. The token value
  is now passed explicitly: `KSM_TOKEN` always, plus the configured name when it
  differs.
- The fake `ksm` fixture now refuses to resolve anything without a credential, like the
  CLI, so token delivery is exercised by every test that reaches the child; a test
  asserts a custom `token_env` resolves, and another asserts other credentials
  (`OPENAI_API_KEY`, another vault's token) never reach the child.
- The fake `ksm` fixture now resolves references the way the CLI does — positionals
  against UIDs, `--title` against titles — and is a readable template instead of
  line-by-line string concatenation. It also returns one object for a single match and
  an array for several, as `ksm secret get --json` does.

### Changed

- CI runs against a bumped Hermes pin, `5d5e7637` (2026-10-09), and installs the
  packages Hermes' own import chain needs (`agent.secret_sources._cache` →
  `utils` → `hermes_yaml` → `ruamel.yaml`) instead of claiming none are needed. An
  advisory `hermes-main` job runs the same suite against unpinned `main`, so a contract
  change on Hermes' side is visible in CI instead of only in a user's install.
- The CI step that copied the plugin into `~/.hermes/plugins/` is gone. It ran no
  Hermes code and asserted nothing; a real loader-level discovery test is the follow-up.

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
