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

Drop the two files into your Hermes plugins dir:

```bash
mkdir -p ~/.hermes/plugins/keeper-vault
cp __init__.py plugin.yaml ~/.hermes/plugins/keeper-vault/
hermes plugins list     # should show: keeper-vault
```

No pip dependency. Hermes loads the directory plugin via `__init__.py` on
startup. Requires **Hermes ≥ v2026.7.1**.

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
    token_env: KSM_TOKEN            # optional; default KSM_TOKEN
    override_existing: true         # optional; default true (rotation-friendly)
    cache_ttl_seconds: 300          # optional; 0 disables on-disk cache
    binary_path: ""                 # optional; pin the ksm binary
    env:
      # [ksm://]<record-ref>[#<field>]
      OPENAI_API_KEY:    "ksm://XKQd9AbCdef123456789#password"
      ANTHROPIC_API_KEY: "ksm://My Login Record"          # defaults to password
      OPENAI_ORG_ID:     "ksm://XKQd9AbCdef123456789#org" # custom field "org"
      DB_PASSWORD:       "ksm://prod/db#password"
```

## Reference grammar

```
[ksm://]<record-ref>[#<field>]
```

- **record-ref** — Keeper **record UID** *or* **record title** (whatever
  `ksm secret get` resolves).
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
  **never** another secret source, and **never** a bootstrap token
  (`KSM_TOKEN`, `KSM_CONFIG`, `KSM_CONFIG_BASE64_1`).
- Every applied var is labelled `(from Keeper Secrets Manager)` in `hermes model`
  and provenance reports.
- Records are fetched once per distinct UID, cached in-process and on disk under
  `~/.hermes/cache/ksm_cache.json` (mode 0600). Only values are cached; auth
  material is fingerprinted, never stored.
- **Failures never block startup.** A missing `ksm` binary, expired token, bad
  reference, or permission error surfaces a one-line warning and Hermes
  continues with whatever `.env` already had.

> Note: plugin discovery runs *after* the first `.env` load, so the Keeper
> source feeds **gateway children, cron, and subagents** — not the very first
> bootstrap process. That's by design (bundled sources cover first-process
> bootstrap).

## Develop / test

Tests run against the **real** Hermes secret-source contract, cloned at the
pinned tag:

```bash
git clone https://github.com/NousResearch/hermes-agent.git /tmp/hermes-agent
PYTHONPATH=/tmp/hermes-agent python3 -m pytest tests/ -v
```

CI (`.github/workflows/verify.yml`) does this automatically on every push/PR:
clones Hermes `v2026.7.1`, installs it editable, drops the plugin into
`~/.hermes/plugins/`, and runs the suite.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | The plugin: `KeeperSource(SecretSource)` + `register(ctx)` |
| `plugin.yaml` | Manifest (`provides_secret_sources: [keeper]`) |
| `tests/` | Conformance + integration tests (fake `ksm` fixture) |
| `.github/workflows/verify.yml` | CI against the pinned Hermes tag |

## License

Apache-2.0 (same as Hermes Agent).
