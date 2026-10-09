# Security Policy

This plugin handles credentials, so security reports are taken seriously.

## Supported versions

The project has no released tags yet. Only the `main` branch is supported.

| Version | Supported |
|---------|-----------|
| `main` | Yes |
| Anything else | No |

## Reporting a vulnerability

Do **not** open a public issue for a security problem. Use GitHub's private
reporting instead: **Security** tab on this repository, then **Report a
vulnerability**. That keeps the report private until there is a fix.

If private reporting is unavailable to you, open an issue that says only that you
have a security matter to report, **with no details**, and the maintainer will
open a private channel with you.

Useful things to include:

- What the flaw is and where it lives (file and function).
- How it can be triggered, and what an attacker gains.
- Whether a credential can be read, written, exfiltrated or overwritten.
- A proof of concept if you have one, with secrets redacted.

## What counts as a security issue here

The plugin's threat model is narrow and worth stating plainly, because it decides
what is in scope:

**In scope**

- A resolved secret value reaching a log, an error message, a printed stream or a
  file the plugin does not intend to write.
- The `ksm` child process receiving environment variables beyond the `KSM_*`
  allowlist and the configured bootstrap token variable.
- A resolved value overwriting a protected variable: the bootstrap token, or any name
  the `ksm` CLI reads its own credential, profile, endpoint or credential store from
  (`KSM_CONFIG`, `KSM_CONFIG_FILE`, `KSM_CONFIG_BASE64_<n>` with its
  `KSM_CONFIG_BASE64_DESC_<n>`, `KSM_HOSTNAME`, `KSM_CLI_PROFILE`, `KSM_INI_DIR`,
  `KSM_INI_FILE`, `KSM_CACHE_DIR`, `KSM_SKIP_VERIFY`). The CLI evaluates
  `KSM_CONFIG`/`KSM_CONFIG_BASE64_1`/`KSM_CONFIG_BASE64_DESC_1` *before* `KSM_TOKEN`, so
  an applied value there replaces the credential rather than merely colliding with it.
- A `secrets.keeper.env` binding inside that namespace being resolved and applied at
  all — including a binding on the configured `token_env`.
- Under multiplexing (one gateway process serving several profiles): a fetch using a
  credential that belongs to a different profile than the one it is fetching for, or
  being served another profile's cached values after a `HERMES_HOME` switch.
- Command injection through a record reference, a field name or an environment
  variable name.
- The cache key or the on-disk `ksm_cache.json` exposing auth material, or the
  cache being readable by another user on the host. (The key is a hash of the auth
  material and the reference map — never the material — plus the home path, which keeps
  profiles apart in the in-process cache.)
- Any path that executes input as code.

**Out of scope**

- Weaknesses in Keeper itself, or in the official `ksm` CLI. Report those to
  Keeper Security.
- Weaknesses in Hermes Agent core. Report those to Nous Research.
- Anything that already requires an attacker to read the user's own
  `~/.hermes/.env`, since that file holds the bootstrap token by design.
- A local user with the same account privileges reading the Hermes home
  directory, which is the trust boundary the cache already sits inside.

## Response expectations

Best effort, from a single maintainer. You will get an acknowledgement, an
assessment of whether it is in scope, and a fix or a reasoned explanation of why
it is not, in that order.

## Hardening notes for users

- Prefer `binary_path` over `PATH` resolution so the `ksm` binary cannot be
  substituted by whatever appears first on `PATH`.
- Set `cache_ttl_seconds: 0` to disable the on-disk cache if you do not want
  resolved values written to `<hermes_home>/cache/ksm_cache.json` — that is
  `~/.hermes/cache/ksm_cache.json` for the default home, and the active profile's own
  home otherwise (mode 0600, inside a 0700 directory).
- Restrict the Keeper application to the specific records it needs, so a
  compromised host cannot read the whole vault.
- Rotate the one-time access token if you ever suspect the Hermes home directory
  was read by someone else.
