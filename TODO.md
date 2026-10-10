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

## 2. Binding audit for a Commander → KSM migration

An existing `secrets.keeper.env` map mixes bare UIDs (`UID`) and labelled references
(`UID#label`). Bare UIDs carry over unchanged (both backends default to the record's
`password` field). Labelled references resolve only when the KSM record exposes the same
field or custom-field label.

- [ ] Add a read-only audit script (or a `--audit` flag on a status path) that, for each
      binding, calls `ksm secret get --json` once per distinct record and reports whether
      the requested label resolves, printing the labels the record *does* expose.
- Acceptance: a migration can be planned from one report instead of discovered at startup;
      the audit makes no writes and prints no secret values.
- Related: custom fields arrive under the `custom` key (the fix in #2), and the extraction
      rules are in `_field_value` (`__init__.py`).

## 3. Should a Commander backend be supported at all?

- [ ] Decide between (a) staying KSM-only and documenting the lineage split, (b) adding a
      Commander backend (`keeper --batch-mode export --format json`, `KEEPER_PASSWORD`,
      `user`/`server`), or (c) maintaining the Commander plugin separately.
- If (b): the contract tests this repository already has apply — argv asserted against the
      real CLI, the bootstrap token delivered by value, `KSM_*`-style protected names for
      whatever configures `keeper`, and a classified `ErrorKind` on every failure path.
- Note the reach difference: Commander can read the whole vault; a KSM application only
      sees what is shared with it. A Commander backend is therefore not a drop-in
      substitute for the KSM one, nor the reverse.

## 4. Install-path safety around a same-slug plugin

Measured, in a sandbox `HERMES_HOME` that already contained a `keeper-vault` plugin:

```console
$ hermes plugins install <git-url> --ref <sha>
Error: Plugin 'keeper-vault' already exists. Use force reinstall or run `hermes plugins update keeper-vault`.

$ hermes plugins update keeper-vault
Error: Plugin 'keeper-vault' was not installed from git (no .git directory). Cannot update.

$ hermes plugins install <git-url> --ref <sha> --force
(installs over the existing directory; a marker file in it is gone)
```

- [ ] Document the backup step for `--force` (the README section above does this now).
- [ ] Optional upstream report: `--force` gives no summary of what it replaced, and a
      hand-copied plugin has no `.git`, so `plugins update` can never update it. A warning
      that names the removed directory contents would make an accidental replacement
      visible.

## 5. Migration checklist

- [ ] Turn the README's Commander → KSM paragraph into a runnable checklist: create the
      Secrets Manager application, copy its one-time access token into `KSM_TOKEN` (or keep
      another name via `token_env`), share the records with the application, install
      `keeper-secrets-manager-cli`, verify one binding, then verify the rest.
- Acceptance: someone can follow it on a fresh host without reading the source, and every
  step that can fail has the error text and the `ErrorKind` it produces.
