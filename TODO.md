# Open items

Things worth evaluating before anyone builds them. Each item records what was measured,
so the next person does not have to rediscover it. Nothing here is a commitment.

## 1. Coexistence: Commander-based `keeper-vault` and this plugin at once

Measured: both plugins declare `name = "keeper"`, and the orchestrator keys sources by
that name (`secrets.<name>` in config, `secrets.sources: [<name>]`), so two of them cannot
coexist as installed. The registry refuses the second registration on two axes: a duplicate
name (`Secret source '<name>' already registered; ignoring duplicate` — the first one wins)
and a `scheme` already owned by a *differently named* source (`Ignoring secret source
'<name>': scheme '<scheme>://' is already owned by source '<owner>'`), and this plugin
declares `scheme = "ksm"`. The `(from …)` provenance text the acceptance asks about is
rendered from `label`, not `name` (`hermes_cli/env_loader.py`).

- [ ] Decide whether to support a second instance: directory `<HERMES_HOME>/plugins/keeper-ksm`,
      `KeeperSource.name = "keeper-ksm"`, config section `secrets.keeper-ksm`, an entry in
      `plugins.enabled`. The rename has to cover `scheme` and `label` as well as `name`, or
      the registry still refuses the second source and both report the same provenance.
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
- Measured as well: `--force` carries user files only when the CLI has install metadata for
  the same source (`plugins_cmd_install.py`'s `_carry_user_files` / `_local_changes`). A
  hand-copied directory has none, which is why the replacement above is wholesale and the
  marker file is gone — the carry is not a safety net for the case this item is about.

## 4. Reach the audit from the CLI instead of a checkout

`scripts/audit_bindings.py` is a script because a Commander user cannot enable this plugin
without replacing their own (item 1), which is exactly the case the audit exists for. Anyone
who is already running it would rather type a command.

- [ ] Register a subcommand with `ctx.register_cli_command` (e.g. `hermes keeper-audit`) that
      wraps the same functions. Hermes discovers plugin commands before parsing argv, and the
      hook exists at the declared floor (`v2026.7.7`) as well as on `main`.
- Acceptance: the command reports what the script reports for the same config, and
  `register()` still loads on a host whose plugin API has no such hook.
- Constraint: a plugin command is reachable only while the plugin is **enabled**
  (`plugins.enabled`), and it never appears in a top-level `hermes --help` listing.
