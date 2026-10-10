# keeper-vault-hermes

**Keeper Secrets Manager (KSM) secret source plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).**

Pull provider credentials from a Keeper vault at Hermes startup — exactly like
the built-in Bitwarden and 1Password sources. Run Keeper **side-by-side with any
other vault** (multi-vault at once) with the same precedence ladder, conflict
warnings, and `(from Keeper Secrets Manager)` provenance labels.

> Part of the Hermes Agent **`#plugins-skills-and-skins`** channel.

---

## Why

Hermes Agent (v2026.7.1+) can pull secrets from **multiple vaults at once**.
Bitwarden and 1Password ship in-tree; every other backend — including Keeper —
is a **plugin** that implements the `SecretSource` contract and registers via
`ctx.register_secret_source()`. This plugin adds Keeper to that mix.

## Install

Let Hermes install it from git:

```bash
hermes plugins install https://github.com/eggressive/keeper-vault-hermes --enable
```

or copy the two files in and enable them yourself:

```bash
mkdir -p ~/.hermes/plugins/keeper-vault
cp __init__.py plugin.yaml ~/.hermes/plugins/keeper-vault/
hermes plugins enable keeper-vault
hermes plugins list      # should show: keeper-vault, enabled
```

**The enable step is not optional.** Copying the files only makes the plugin
*known*. `plugins.enabled` is an opt-in allow-list, so an unenabled plugin loads
with `enabled=False` and

```
error="not enabled in config (run `hermes plugins enable keeper-vault` to activate)"
```

registers no secret source, and leaves `secrets.sources: [keeper]` naming an
unknown source — no secrets load, while `hermes plugins list` still lists the
plugin as if it were installed.

Notes on the git route:

- This repo is not in the Hermes plugin catalog, so Hermes treats it as a *custom
  (unreviewed) source* and, on the versions that scan installs, scans the tree first. The
  findings it reports are nearly all in this repo's own documentation and test fixtures,
  not in the plugin code, and what the scan does with them depends on the scanner your
  Hermes ships:

  - **The newest build measured** (scanner `plugin-guard-v10`, 2026-10-09) allows this
    tree outright — the command above prints its findings and finishes:

    ```
    Cloning https://github.com/eggressive/keeper-vault-hermes...
    ✓ Plugin keeper-vault enabled.
    ```

  - **Older builds** (scanner `plugin-guard-v9`, e.g. 0.21.6) score the same tree a
    **caution** verdict and ask you to confirm. In a terminal, answer the prompt:

    ```
    ⚠ Security scan flagged this plugin:
    Scan: plugin (…)  Verdict: CAUTION
    Decision: BLOCKED — Blocked (community source + caution verdict, 21 findings).
      Install anyway? Only continue if you trust the source. [y/N]:
    ```

    Answer `y` and the install continues and enables the plugin. Note that the "Decision:
    BLOCKED … Use --force to override" line is printed in both modes, so a prompt looks
    like a hard failure.

    Non-interactively (CI, a script, a tool without a TTY) there is nobody to answer, so
    that build refuses before installing — `Review the findings above. Install only
    plugins from sources you trust.` — and you have to re-run with `--force` once you have
    read the findings:

    ```bash
    hermes plugins install https://github.com/eggressive/keeper-vault-hermes --enable --force
    ```

- `--force` only ever overrides the *caution* verdict; Hermes still refuses a *dangerous*
  one. To drop the install-time scan entirely, set `plugins.scan_on_install: false` in
  `config.yaml` (Hermes' own hint in that message).

- Nothing else is asked: `plugin.yaml` declares `python_runtime: external`, so there is
  no Python dependency step to consent to and the install enables the plugin
  non-interactively.

`pip install` is **not** an install path. This is a Hermes *directory* plugin: the
distribution ships no importable plugin module and no `hermes_agent.plugins` entry
point, so Hermes installs it from git or from `~/.hermes/plugins/<name>/` only.

**Version requirement:** `plugin.yaml` declares `requires_hermes: ">=0.18.1"` — the
`v2026.7.7` release, which is the first with the pluggable `SecretSource` API this
plugin implements (`v2026.7.1`, version 0.18.0, predates it). On an older host the
plugin is skipped cleanly with `requires hermes >=0.18.1, running X` instead of
failing inside `register()` with an `ImportError`.

## Already using a Commander-based `keeper-vault` plugin?

Some Hermes setups already carry a hand-written plugin with this name that shells out to
Keeper **Commander** (`keeper --batch-mode export`, bootstrap `KEEPER_PASSWORD`) rather
than to the Secrets Manager CLI (`ksm`, bootstrap `KSM_TOKEN`). This repository is the
KSM lineage — its own `v1.0.0` tag is already `ksm`-based — so the two are **not** an
upgrade path for each other, even though they share a name and a `secrets.keeper`
section.

Swapping one for the other changes the Keeper **product** in use, not just the binary:

| | Commander-based plugin | this plugin |
|---|---|---|
| CLI | `keeper` (`keepercommander`) | `ksm` (`keeper-secrets-manager-cli`) |
| bootstrap | `KEEPER_PASSWORD` (account password) | `KSM_TOKEN` (Secrets Manager application token) |
| reach | every record the login can read | only the records shared with the application |
| extra config keys | `user`, `server` | `token_env`, `timeout_seconds` |
| cache file | `<cache>/keeper_cache.json` | `<cache>/ksm_cache.json` |

The section is `secrets.keeper` either way, and `env`, `binary_path`, `override_existing`
and `cache_ttl_seconds` mean the same thing in both.

Keeper's Secrets Manager applications are not available on every plan (Keeper offers a
trial), so an account that works with Commander may still need a plan change before `ksm`
can read anything. With no application and no token, every binding fails as
`not_configured`, this source applies nothing, and Hermes continues with whatever `.env`
already had — the startup report says so and prints the fix-it hint.

Existing `secrets.keeper.env` values survive the switch when they are bare record UIDs
(both backends default to the record's `password` field). Values written as
`UID#<label>` keep working only if the KSM record exposes the same field or custom-field
label; Commander lower-cases the label, and so does this plugin.

Nothing swaps by itself:

```console
$ hermes plugins install https://github.com/eggressive/keeper-vault-hermes --enable
Error: Plugin 'keeper-vault' already exists. Use force reinstall or run `hermes plugins update keeper-vault`.

$ hermes plugins update keeper-vault            # a hand-copied plugin has no .git
Error: Plugin 'keeper-vault' was not installed from git (no .git directory). Cannot update.
```

`--force` does replace the directory, so copy `__init__.py` and `plugin.yaml` first —
restoring those two files is the whole rollback.

The options, in increasing order of effort (each one, with its acceptance criteria, is
tracked in [TODO.md](TODO.md)):

1. **Keep the Commander plugin** and treat this repository as a separate, KSM-specific
   plugin.
2. **Run both side by side.** The plugin directory, `KeeperSource.name` and the
   `secrets.<name>` section all have to change, because both register the source name
   `keeper`.
3. **Migrate to Secrets Manager**: create the application, put its one-time token in
   `KSM_TOKEN`, share the records with the application, then verify binding by binding.
4. **Port these fixes to the Commander plugin.** The CLI-argv contract, the per-fetch
   environment, bootstrap-token delivery, the protected-variable namespace and the error
   classification apply to a Commander backend too.

## One-time Keeper setup

1. In the Keeper vault, create a **Secret Manager Application** and add a
   **Client Device** → copy the **One-Time Access Token**.
2. Install the KSM CLI on every host that runs Hermes:
   ```bash
   pip3 install keeper-secrets-manager-cli        # or download the binary:
   # https://github.com/Keeper-Security/secrets-manager/releases?q=cli
   ```
3. Put the one-time token in `~/.hermes/.env` as your bootstrap secret:
   ```bash
   KSM_TOKEN="XX:XXXX"      # the one-time access token
   ```
   - **Headless/container:** this is all you need — `ksm` auto-creates a
     profile from `KSM_TOKEN`.
   - **Desktop:** run `ksm profile init --token "XX:XXXX"` once (uses keyring).
   - Or set `KSM_CONFIG` (base64) for a fully file-less bootstrap.

## Configure

In `~/.hermes/config.yaml`:

```yaml
secrets:
  sources: [keeper, bitwarden]     # run Keeper alongside other vaults
  keeper:
    enabled: true
    token_env: KSM_TOKEN            # optional; any var name (default KSM_TOKEN),
                                    # read from the profile's environment. Its value
                                    # is exported to ksm as KSM_TOKEN.
    override_existing: true         # optional; default true (rotation-friendly)
    cache_ttl_seconds: 300          # optional; 0 disables on-disk cache
    binary_path: ""                 # optional; pin the ksm binary
    env:
      # [ksm://]<record-uid>[#<field>]  or  [ksm://]title:<record-title>[#<field>]
      OPENAI_API_KEY:    "ksm://XKQd9AbCdef123456789#password"      # by record UID
      ANTHROPIC_API_KEY: "ksm://title:My Login Record"             # by title, password
      OPENAI_ORG_ID:     "ksm://XKQd9AbCdef123456789#org"          # custom field "org"
      DB_PASSWORD:       "ksm://title:prod/db#password"
```

## Reference grammar

```
[ksm://]<record-uid>[#<field>]
[ksm://]title:<record-title>[#<field>]
```

- **record-uid** — a Keeper **record UID**. This is what a bare reference means,
  and it is what `ksm secret get` resolves positionally.
- **`title:`** — resolve the record by **title** instead. The prefix is required,
  because `ksm secret get` treats a positional argument as a UID and sends it to
  Keeper as a server-side record filter: a title given positionally can never
  match, and fails with `Cannot find requested record(s).`. Titles are matched
  client-side by `-t/--title`, which this form selects. A UID is URL-safe base64
  and never contains `:`, so the prefix is unambiguous. A title that matches more
  than one record is refused with a warning — bind the record UID to select
  exactly one.
- **field** — optional field to extract. Defaults to `password` (the common
  case for API keys). Use the field **label** or **type** (`login`, `url`,
  a custom-field label). A title containing `#` is fine — only the last `#`
  is treated as the field delimiter.

## How it behaves (the contract)

- Keeper is a **mapped** source — you bind each env var explicitly, so its
  claims are the strongest user intent and **win over bulk sources** like
  Bitwarden BSM on contested vars.
- First source to claim a var wins; later sources get a conflict warning and
  never silently clobber it.
- `override_existing` (default `true`) lets Keeper beat `.env`/shell — but
  **never** another secret source, and **never** a variable that configures the `ksm`
  CLI itself: the bootstrap token, `KSM_CONFIG`, `KSM_CONFIG_FILE`, the
  `KSM_CONFIG_BASE64_<n>` profile slots with their `KSM_CONFIG_BASE64_DESC_<n>` names,
  `KSM_HOSTNAME`, `KSM_CLI_PROFILE`, the `KSM_INI_*`/`KSM_CACHE_DIR` paths and
  `KSM_SKIP_VERIFY`. Keeper refuses those names in its own `env` map (the `KSM_*`
  namespace configures the CLI; it never carries your application's variables) and
  declares them to Hermes, so no other source can take them either. That matters
  because `ksm` resolves `KSM_CONFIG` and `KSM_CONFIG_BASE64_1` *before* `KSM_TOKEN`:
  a value landing there would replace the credential Hermes authenticated with.
- Every applied var is labelled `(from Keeper Secrets Manager)` in `hermes model`
  and provenance reports.
- Records are fetched once per distinct reference, cached in-process and on disk under
  `<hermes_home>/cache/ksm_cache.json` (`~/.hermes/cache/ksm_cache.json` for the default
  home, mode 0600). Only values are cached; auth material is fingerprinted, never
  stored.
- Auth is read from the **per-fetch environment** Hermes installs for the profile being
  served (`agent.secret_sources.base.get_source_environment`), not from the process
  environment. So a token that lives only in a profile's `.env` works, and under
  multiplexing the `ksm` child can never be handed a sibling profile's credential —
  which matters, because `KSM_CONFIG` outranks `KSM_TOKEN` in the CLI's own precedence
  order. The in-process cache key carries the home path for the same reason.
- **Failures never block startup.** A missing `ksm` binary, expired token, bad
  reference, or permission error surfaces a one-line warning and Hermes
  continues with whatever `.env` already had. A UID lookup that matches nothing
  is the one failure the warning explains further, since a bare title looks
  exactly like a bad UID to the CLI.

> Note: plugin discovery runs *after* the first `.env` load, so the Keeper
> source feeds **gateway children, cron, and subagents** — not the very first
> bootstrap process. That's by design (bundled sources cover first-process
> bootstrap).

## Develop / test

Tests run against the **real** Hermes secret-source contract, cloned at the
pinned commit. Hermes is imported via `PYTHONPATH`, but its own import chain is not
dependency-free — `agent.secret_sources._cache` pulls `utils` → `hermes_yaml` →
`ruamel.yaml`:

```bash
git clone https://github.com/NousResearch/hermes-agent.git /tmp/hermes-agent
python3 -m pip install pytest ruamel.yaml
PYTHONPATH=/tmp/hermes-agent python3 -m pytest tests/ -v
```

CI (`.github/workflows/verify.yml`) does this automatically on every push/PR: the
`conformance` job clones Hermes `main` at the pinned commit
`5d5e7637` (2026-10-09) and runs the suite; the advisory `hermes-main` job runs the
same suite against unpinned `main` so a contract change on Hermes' side shows up in
CI instead of in a user's install.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | The plugin: `KeeperSource(SecretSource)` + `register(ctx)` |
| `plugin.yaml` | Manifest (`name`, `requires_hermes`, `python_runtime`) |
| `tests/` | Conformance + integration tests (fake `ksm` fixture) |
| `pyproject.toml` | Distribution metadata only — no importable module; CI builds it, and a plugin that ships one is a package-manager workspace member Hermes must be able to build |
| `.github/workflows/verify.yml` | CI against the pinned Hermes commit (+ advisory run against unpinned `main`) |
| `CHANGELOG.md` | Release history |
| `SECURITY.md` | Threat model, what is in scope, how to report privately |
| `CONTRIBUTING.md` | How to contribute, and the security rules contributions must keep |
| `TODO.md` | Open items for further evaluation, each with what was already measured |

## Security

Resolved values are cached under `~/.hermes/cache/ksm_cache.json` with mode 0600,
and auth material is fingerprinted rather than stored. The `ksm` child process
receives Keeper's own `KSM_*` variables plus the bootstrap token variable, read from
the per-fetch environment — not a copy of the parent's environment, which by then holds
every credential resolved for the profile. No resolved value can land on a variable that
configures the `ksm` CLI (see the contract above): `KSM_*` bindings are refused, and the
CLI's credential/profile/endpoint variables are protected against every other source.
The bootstrap token's value is passed under `KSM_TOKEN`
— the name the CLI reads — as well as under `token_env` when that is a different
name, so a custom name keeps working. To report a vulnerability, use the **Security** tab
rather than a public issue; see `SECURITY.md` for the threat model and what is in
scope.

## License

Apache-2.0 (same as Hermes Agent). See `LICENSE`.
