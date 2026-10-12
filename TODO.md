# Open items

Things worth evaluating before anyone builds them. Each item records what was measured,
so the next person does not have to rediscover it. Nothing here is a commitment.

## 1. Coexistence: Commander-based `keeper-vault` and this plugin at once

Measured: both plugins declare `name = "keeper"`, and the orchestrator keys sources by
that name (`secrets.<name>` in config, `secrets.sources: [<name>]`), so two of them cannot
coexist as installed.

- [ ] Decide whether to support a second instance: directory `<HERMES_HOME>/plugins/keeper-ksm`,
      `KeeperSource.name = "keeper-ksm"`, config section `secrets.keeper-ksm`, an entry in
      `plugins.enabled`.
- Acceptance: `secrets.sources: [keeper, keeper-ksm]` resolves both, each applies its own
  bindings, the registry merges both sets of `protected_env_vars`, and provenance labels
  stay distinct.
- Open question: is the rename worth it, or is a migration window (stop, swap, start)
  simpler? A renamed source is also a *user-visible* name in `secrets.sources`.

## 2. Should a Commander backend be supported at all?

- [ ] Decide between (a) staying KSM-only and documenting the lineage split, (b) adding a
      Commander backend (`keeper --batch-mode export --format json`, `KEEPER_PASSWORD`,
      `user`/`server`), or (c) maintaining the Commander plugin separately.
- If (b): the contract tests this repository already has apply — argv asserted against the
      real CLI, the bootstrap token delivered by value, `KSM_*`-style protected names for
      whatever configures `keeper`, and a classified `ErrorKind` on every failure path.
- Note the reach difference: Commander can read the whole vault; a KSM application only
      sees what is shared with it. A Commander backend is therefore not a drop-in
      substitute for the KSM one, nor the reverse.

## 3. Install-path safety around a same-slug plugin

Measured, in a sandbox `HERMES_HOME` that already contained a `keeper-vault` plugin:

```console
$ hermes plugins install <git-url> --ref <sha>
Error: Plugin 'keeper-vault' already exists. Use force reinstall or run `hermes plugins update keeper-vault`.

$ hermes plugins update keeper-vault
Error: Plugin 'keeper-vault' was not installed from git (no .git directory). Cannot update.

$ hermes plugins install <git-url> --ref <sha> --force
(installs over the existing directory; a marker file in it is gone)
```

- [ ] Optional upstream report: `--force` gives no summary of what it replaced, and a
      hand-copied plugin has no `.git`, so `plugins update` can never update it. A warning
      that names the removed directory contents would make an accidental replacement
      visible.
