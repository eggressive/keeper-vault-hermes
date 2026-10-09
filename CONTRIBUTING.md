# Contributing

Thanks for taking the time to look at this. It is a small plugin with one
maintainer, so the guidance is short.

## Before you open an issue

Check the README first. Three failure modes account for most reports:

- **`ksm CLI not found`**: install it (`pip3 install keeper-secrets-manager-cli`)
  or set `secrets.keeper.binary_path` to the absolute path.
- **`enabled: true` with an empty `env:` map**: nothing to resolve, so nothing is
  applied. Add `ENV_VAR: ksm://record-ref` entries.
- **A record title without the `title:` prefix**: `ANTHROPIC_API_KEY: "My Login
  Record"` is looked up as a record **UID**, because that is what `ksm secret get`
  resolves positionally. It warns and resolves nothing. Write `ksm://title:My Login
  Record` instead.

When you do open one, use the bug report template. Redact every credential, token
and secret value. Warnings and error kinds are what matter, never resolved values.

## Pull requests

1. Open an issue first for anything beyond a small fix, so we agree on the shape.
2. Keep the change inside the `SecretSource` contract. This plugin must not
   require core Hermes modifications, and it must not import Hermes at module
   scope, since it is loaded as a directory plugin.
3. Add or update tests. The suite runs against a fake `ksm` CLI so it needs no
   live vault; keep it that way, because a test that needs a real vault will not
   run in CI.
4. Run the suite locally:

   ```bash
   PYTHONPATH=/path/to/hermes-agent pytest tests/ -v
   ```

5. State in the pull request what you changed and how you verified it. A claim
   without a command or an output is not verification.

## Security rules for contributions

These are not negotiable, because the plugin handles credentials:

- Never pass `os.environ` to a child process. Extend the allowlist in
  `_ksm_child_env` instead.
- Never log, print or embed a resolved secret value.
- Never let a resolved value reach `os.environ` for a protected variable,
  including the bootstrap token.
- Do not shell out with `shell=True`, and do not add a code path that executes
  input as code.
- Do not weaken the cache key. It must stay a hash of the auth material and the
  reference map, never the material itself.

A report that a contribution breaks one of these will be treated as a bug and
reverted first, discussed second.

## Licence

Contributions are accepted under Apache-2.0, the licence this project already
carries.
