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

The options, in increasing order of effort (options 1, 2 and 4 are tracked, with their
acceptance criteria, in [TODO.md](TODO.md); option 3 is written out below):

1. **Keep the Commander plugin** and treat this repository as a separate, KSM-specific
   plugin.
2. **Run both side by side.** The plugin directory, `KeeperSource.name` and the
   `secrets.<name>` section all have to change, because both register the source name
   `keeper`.
3. **Migrate to Secrets Manager.** Create the application, put its one-time token in
   `KSM_TOKEN` (or another name via `token_env`), share the records with the application,
   then verify binding by binding — [step by step below](#migrating-to-secrets-manager-step-by-step).
4. **Port these fixes to the Commander plugin.** The CLI-argv contract, the per-fetch
   environment, bootstrap-token delivery, the protected-variable namespace and the error
   classification apply to a Commander backend too.

### Migrating to Secrets Manager, step by step

Option 3 above, in the order the steps have to happen. Steps 1–3 are the same one-time
setup a fresh install needs ([One-time Keeper setup](#one-time-keeper-setup) has the
detail); steps 4–5 are the ones a Commander deployment adds. The config section stays
`secrets.keeper` throughout, but step 1 needs a plan that includes Secrets Manager, and
steps 4–5 have no Commander equivalent.

1. **Create the application and a client device.** Keeper vault → **Secrets Manager** →
   *Create Application*, then add a **Client Device** and copy its **One-Time Access
   Token**. Commander working is not evidence that the plan includes this — Secrets
   Manager is not on every plan (Keeper offers a trial).
2. **Install the CLI on every host that runs Hermes**: `pip3 install
   keeper-secrets-manager-cli`, or the release binary linked below. `ksm` and `keeper` are
   different programs: the `keeper` you already have does not provide `ksm`.
3. **Give `ksm` a credential.** Put the token in the profile's `.env` as `KSM_TOKEN` — or
   under any other name, with `secrets.keeper.token_env` pointing at it; the value is
   still exported to the child as `KSM_TOKEN` — or set `KSM_CONFIG`, or run
   `ksm profile init --token "<token>"` once where a keyring exists. This is the bootstrap
   secret, not one of the bindings.
4. **Share the records with the application.** Share each record your `env:` map
   references, or move them into a folder the application can read. This is the step
   Commander never had: its login could read the whole vault, an application sees only
   what is shared with it, and a record left unshared is not found at all.
5. **Verify one binding, then the rest.** Start with a single entry in `env:`, restart
   Hermes, and read the startup report — it names the variables it applied or could not
   bind, and never prints a value. Add the rest once the first one resolves.

Startup prints a line for this source when there is something to say: what it applied, the
error if the whole fetch failed (followed by a `→` fix-it hint), and one warning per binding
that did not resolve. Every line carries the source label, so the message column below is
the part after `Keeper Secrets Manager: `:

| Message | `ErrorKind` | What it means |
|---|---|---|
| `applied <n> secrets` | — | every binding resolved |
| `secrets.keeper.enabled is true but the env: map is empty. Add ENV_VAR: ksm://record-ref[#field] entries.` | `NOT_CONFIGURED` | step 5 — nothing is bound yet |
| `ksm failed for all <n> record reference(s); first failure: ksm failed for '<ref>': Error: The Keeper SDK client has not been loaded.` | `NOT_CONFIGURED` | step 3 — no credential reached `ksm` |
| `ksm failed for all <n> record reference(s); first failure: ksm failed for '<ref>': Error: … 401 …` | `AUTH_FAILED`, or `AUTH_EXPIRED` once the session has expired | step 3 — wrong, stale, or another application's token |
| `secrets.keeper.enabled is true but the ksm CLI was not found on PATH.  Install it (pip3 install keeper-secrets-manager-cli) or set secrets.keeper.binary_path.` | `BINARY_MISSING` | step 2 (a `binary_path` set to something that is not executable says so instead) |
| `Keeper record '<ref>' has no value for field 'org' (requested by 'OPENAI_ORG_ID')` | none — a warning, the other bindings still apply | the record resolved; that field or custom-field label does not exist on it |
| `ksm failed for all <n> record reference(s); first failure: ksm failed for '<ref>': Error: Cannot find requested record(s).  ('<ref>' was looked up as a record UID; to resolve it by title use ksm://title:<title>)` | `REF_INVALID` | step 4 — the application cannot see the record — or a title written without the `title:` prefix |
| `ksm timed out after <s>s of the <budget>s fetch budget, before this reference was tried` | `TIMEOUT` | raise `secrets.keeper.timeout_seconds`; each `ksm` call gets a share of it, capped at 30 s |

A failed binding is simply not applied: the variable keeps whatever `.env` or the shell
already had, one bad entry never sinks the rest of the map, and none of this blocks
startup.

### Auditing the map before you swap

`scripts/audit_bindings.py` answers the label question for the whole map at once, before
anything is installed: one `ksm secret get --json` per distinct record reference, a verdict
per binding, and the labels each record *does* expose whenever a requested one is missing. It
runs the plugin's own reference parsing, child environment and field matching, so its answer
is what startup would give; it writes no cache and prints no resolved value.

It needs the same environment the test suite needs — Hermes importable, which brings
`ruamel.yaml` with it (see [Develop / test](#develop--test)):

```bash
git clone https://github.com/eggressive/keeper-vault-hermes /tmp/keeper-vault
PYTHONPATH=/path/to/hermes-agent python3 /tmp/keeper-vault/scripts/audit_bindings.py
# --help lists the knobs; add --home, --config, --ksm or --token-env for another profile
```

```console
$ PYTHONPATH=~/.hermes/hermes-agent python3 /tmp/keeper-vault/scripts/audit_bindings.py
Keeper binding audit -- read-only
  config    /home/you/.hermes/config.yaml  (5 binding(s) over 3 record reference(s))
  ksm       /usr/local/bin/ksm
  token     KSM_TOKEN, from /home/you/.hermes/.env

  XKQd9AbCdef123456789
    ok    OPENAI_API_KEY           #password
    ok    OPENAI_ORG_ID            #org
    MISS  WRONG_FIELD              #apikey           record exposes: title, login, password, org, text

  title:My Login Record
    ok    ANTHROPIC_API_KEY        #password

  deadbeefdeadbeef
    MISS  GONE_RECORD              #password         ksm failed for 'deadbeefdeadbeef': ksm had a problem: Cannot find requested record(s).

  3 of 5 binding(s) would resolve; 2 would not.
```

It reads `secrets.keeper` (including `token_env` and `binary_path`) from
`<hermes home>/config.yaml`, takes the credential from the process environment plus the
profile's `.env` — the view a single-profile fetch reads — and leaves the `ksm` binary to
decide whether that credential works. Exit status `0` means every binding would resolve, `1`
means one would not (so a script can gate the swap on it), and `2` is a usage or configuration
error. `--home`, `--config`, `--ksm` and `--token-env` point it at another profile or another
CLI build.

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
    timeout_seconds: 120            # optional; whole-fetch budget Hermes enforces.
                                    # Each `ksm` call gets a share of it (headroom
                                    # reserved for parsing/loop, capped at 30 s), and
                                    # a call is not started once the budget is gone,
                                    # so the fetch is reported instead of being
                                    # discarded for overrunning.
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
  stored. The cache is consulted **before** the `ksm` binary is resolved: the CLI is
  needed to reach the vault, not to read what this home already resolved, so a PATH
  without `ksm` (cron, a trimmed gateway environment) still gets fresh cached values
  instead of `BINARY_MISSING`. `clear_caches(home)` drops both layers after a token or
  record rotation.
- Auth is read from the **per-fetch environment** Hermes installs for the profile being
  served (`agent.secret_sources.base.get_source_environment`), not from the process
  environment. So a token that lives only in a profile's `.env` works, and under
  multiplexing the `ksm` child can never be handed a sibling profile's credential —
  which matters, because `KSM_CONFIG` outranks `KSM_TOKEN` in the CLI's own precedence
  order. The in-process cache key carries the home path for the same reason.
- **Failures never block startup.** A missing `ksm` binary, expired token, bad
  reference, or permission error surfaces a one-line warning and Hermes
  continues with whatever `.env` already had. Failures are classified with the shared
  `ErrorKind` vocabulary: a reference matching no record is `REF_INVALID`, the CLI's
  "SDK client has not been loaded" is `NOT_CONFIGURED`, an expired session is
  `AUTH_EXPIRED`. A UID lookup that matches nothing is the one failure the warning
  explains further, since a bare title looks exactly like a bad UID to the CLI, and the
  remediation hint names this plugin's own knobs rather than the bundled
  `hermes secrets <name> setup` command, which does not exist for Keeper.
  A fetch in which **every** reference failed is reported as an error (not just as
  warnings) with its classified kind, which is what makes the host print the error line
  and the fix-it hint; a mixed result still only warns, so one bad reference never sinks
  a good one.

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
| `scripts/audit_bindings.py` | Read-only audit of a `secrets.keeper.env` map against `ksm` (planning the migration) |
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
