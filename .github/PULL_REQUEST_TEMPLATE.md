<!--
Thanks for the pull request. Keep it short and factual. Two things decide
whether this can be merged quickly: does it stay inside the SecretSource
contract, and is the security posture unchanged or better.
-->

## What this changes

<!-- One or two sentences. -->

## Why

<!-- The problem it solves. Link the issue if there is one. -->

## How it was verified

<!-- Paste the command you ran and its result. "Tests pass" alone is not verification. -->

```

```

## Security checklist

- [ ] No credential, token or secret value is logged, printed or embedded.
- [ ] The child-process environment is still an allowlist, not `os.environ`.
- [ ] Protected variables (the bootstrap token, `KSM_CONFIG`, `KSM_CONFIG_BASE64_1`) still cannot be overwritten by a resolved value.
- [ ] No `shell=True`, no `eval`, no `exec`, no execution of input as code.
- [ ] The cache key is still a hash of the auth material and the reference map, never the material itself.
- [ ] No new hard dependency on Hermes at import time.

## Compatibility

- [ ] Works against Hermes Agent `main` (post-`v2026.7.1`), or the affected version is stated above.
- [ ] `CHANGELOG.md` updated if this is a user-visible change.
