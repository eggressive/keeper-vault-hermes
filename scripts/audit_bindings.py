#!/usr/bin/env python3
"""Read-only audit of a ``secrets.keeper.env`` map against the ``ksm`` CLI.

Answers the one question a Commander -> Keeper Secrets Manager migration turns on, before
anything is installed or swapped: for each binding, does the record resolve, and does it
carry the field the binding asks for?  Bare record UIDs carry over from the Commander
plugin unchanged (both default to the record's ``password`` field), but a labelled
reference only resolves when the KSM record exposes the same field or custom-field label,
so the answer is worth having as a report instead of as a startup surprise.

For each distinct record reference the audit runs ``ksm secret get --json`` once, with the
same argv, the same allowlisted child environment, the same field-matching rules and the same
fetch budget the plugin uses -- imported from ``__init__.py`` next to this script, so the two
cannot drift.  It prints the labels the record *does* expose whenever a requested one is
missing, and names the entries the plugin refuses to bind at all (a destination in the
``KSM_*`` namespace, or the bootstrap-token variable), which count as unresolved for the exit
status.  It writes nothing, changes no cache and never prints a resolved value.

The credential comes from the profile it audits -- the global process names plus ``<home>/.env``
and ``<home>/.op.env`` -- which is the view Hermes installs for a served profile.  A token
exported only in the calling shell is deliberately not part of it: ``KSM_CONFIG`` outranks
``KSM_TOKEN`` in the CLI's own order, so a sibling profile's variable could otherwise answer
the audit while startup never sees it.

The plugin imports Hermes at module scope, so the Hermes package has to be importable: the
same requirement the test suite has.

    PYTHONPATH=~/.hermes/hermes-agent python3 scripts/audit_bindings.py

Exit status: 0 when every binding would resolve, 1 when one or more would not (so a script
can gate the swap on it), 2 on a usage or configuration error.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

PLUGIN_DIR = Path(__file__).resolve().parent.parent
DEFAULT_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
KSM_PREFIX = "KSM_"      # the namespace that configures the `ksm` CLI itself

# The record-object keys `_field_value` reads directly; anything else has to be a label or
# a type inside one of `_FIELD_ARRAYS`.  Kept in step with `_field_value` and asserted by
# tests/test_audit_bindings.py, which fails if the plugin gains a field this misses.
TOP_LEVEL_FIELDS = ("password", "login", "username", "url", "notes", "title", "name")


class Binding(NamedTuple):
    var: str
    field: Optional[str]
    ok: bool
    detail: str  # "" when the binding resolves, else the plugin's own failure text


class RecordAudit(NamedTuple):
    record_ref: str
    exposed: List[str]
    bindings: List[Binding]
    error: str  # "" when the record resolved at all


def load_plugin():
    """Import the plugin module this script sits next to."""
    spec = importlib.util.spec_from_file_location("keeper_vault_plugin", PLUGIN_DIR / "__init__.py")
    if spec is None or spec.loader is None:  # pragma: no cover - the file is next to us
        raise SystemExit(f"cannot load {PLUGIN_DIR / '__init__.py'}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise SystemExit(
            f"audit: cannot import the plugin ({exc}). Hermes has to be importable by this\n"
            f"interpreter, e.g.\n"
            f"  PYTHONPATH=/path/to/hermes-agent python3 {Path(__file__).name}\n"
            f"or run it with the interpreter Hermes itself uses, which already carries\n"
            f"Hermes' dependencies (ruamel.yaml among them)."
        )
    return module


def read_section(config_path: Path) -> Tuple[Dict, str]:
    """``(secrets.keeper section, error)`` from a Hermes config file.

    Read with ``ruamel.yaml``, the parser Hermes' own config layer uses, and the one
    dependency the plugin's import chain already needs -- so this adds none.
    """
    from ruamel.yaml import YAML

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"cannot read {config_path}: {exc}"
    try:
        data = YAML(typ="safe").load(raw) or {}
    except Exception as exc:
        return {}, f"{config_path} is not valid YAML: {exc}"
    secrets_cfg = data.get("secrets") if isinstance(data, dict) else None
    section = secrets_cfg.get("keeper") if isinstance(secrets_cfg, dict) else None
    if not isinstance(section, dict):
        return {}, f"{config_path} has no `secrets.keeper` section"
    return section, ""


def build_view(home: Path, mod) -> Dict[str, str]:
    """The environment view Hermes installs for the profile this audit targets.

    Hermes builds it from the process environment's *global* names plus the profile's
    ``.env`` (``agent.secret_scope._is_global_env`` keeps PATH/HOME/locale and drops
    credentials -- the construction ``hermes_cli.env_loader`` uses and this repository's own
    test helper documents).  Taking the process environment wholesale instead would hand
    `ksm` whatever credential the calling shell happens to hold, and ``KSM_CONFIG`` outranks
    ``KSM_TOKEN`` in the CLI's own order: a sibling profile's credential could then answer
    the audit and report an application that startup never queries.

    ``load_env_file`` is the host's own ``.env`` tokenizer, so a value parses here exactly as
    it parses in Hermes.
    """
    from agent.secret_scope import _is_global_env, load_env_file

    view = {name: value for name, value in os.environ.items() if _is_global_env(name)}
    view.update(load_env_file(home / ".env"))
    # The `.op.env` bootstrap, layered the way the loader does it (`.env` wins).
    for name, value in load_env_file(home / ".op.env").items():
        view.setdefault(name, value)
    view["HERMES_HOME"] = str(home)
    return view


def credential_line(home: Path, view: Dict[str, str], token_env: str) -> str:
    """Which credential the child will get, and where from -- names only, never values."""
    env_file = home / ".env"
    if view.get(token_env):
        where = env_file if token_env in _dotenv_names(env_file) else home / ".op.env"
        return f"{token_env}, from {where}"
    held = sorted(name for name in view if name.startswith(KSM_PREFIX))
    if held:
        extra = f" (and {len(held) - 1} more)" if len(held) > 1 else ""
        return f"{held[0]}{extra}, from the profile"
    return (f"none in {env_file}; a served fetch reads the profile, not this shell, so `ksm` "
            f"falls back to its own profile (keyring or keeper.ini)")


def _dotenv_names(env_file: Path) -> set:
    from agent.secret_scope import load_env_file

    return set(load_env_file(env_file))


def resolves(mod, record: dict, field: Optional[str]) -> bool:
    """Whether a binding to ``field`` would be applied -- the same test the fetch makes.

    The extracted value stays inside this call: the audit needs the yes/no, never a value.
    """
    return bool((mod._field_value(record, field) or "").strip())


def exposed_labels(record: dict, mod) -> List[str]:
    """Every field name the record can be bound by -- labels and types, never values.

    A name is listed only when a binding to it would resolve, so the inventory is directly
    usable: whatever it names is what ``_field_value`` can extract.
    """
    named: List[str] = []
    for key in TOP_LEVEL_FIELDS:
        if key in record and resolves(mod, record, key):
            named.append(key)
    for array in mod._FIELD_ARRAYS:
        for item in record.get(array) or []:
            if not isinstance(item, dict):
                continue
            for raw in (item.get("label"), item.get("type")):
                label = mod._label_of(raw)
                if label and label not in named and resolves(mod, record, label):
                    named.append(label)
    return named


def _unread_record(record_ref: str, bindings: List[Tuple[str, Optional[str]]],
                   detail: str) -> RecordAudit:
    """A record the audit could not read: every binding on it unresolved, with `detail`."""
    return RecordAudit(record_ref, [], [Binding(v, f, False, detail) for v, f in bindings], detail)


def audit(mod, ksm: Path, view: Dict[str, str], token_env: str,
          by_record: Dict[str, List[Tuple[str, Optional[str]]]],
          budget_seconds: float) -> List[RecordAudit]:
    """One ``ksm`` call per distinct record reference, under the fetch's own deadline.

    A cold ``fetch()`` does not give every record the 30 s per-call cap: it divides what is
    left of ``secrets.keeper.timeout_seconds`` among the references still to try and stops
    starting children once the budget is gone, so a map wider than its budget never resolves
    every binding.  The audit walks the same deadline in the same order with the plugin's own
    arithmetic (``_per_call_timeout``, ``_budget_exhausted_error``), so a binding it reports
    as resolvable is one that fetch would have reached.
    """
    token = mod._bootstrap_token(token_env, view)
    started = time.monotonic()
    pending = len(by_record)
    results: List[RecordAudit] = []
    for record_ref, bindings in by_record.items():
        run_timeout = mod._per_call_timeout(budget_seconds, pending,
                                            time.monotonic() - started)
        pending -= 1
        if run_timeout <= 0:
            detail = mod._budget_exhausted_error(budget_seconds, time.monotonic() - started)
            results.append(_unread_record(record_ref, bindings, detail))
            continue
        try:
            record = mod._run_ksm_get(ksm, record_ref, view=view, token_env=token_env,
                                      token_value=token, run_timeout=run_timeout)
        except RuntimeError as exc:
            results.append(_unread_record(record_ref, bindings, str(exc)))
            continue
        exposed = exposed_labels(record, mod)
        results.append(RecordAudit(
            record_ref, exposed,
            [Binding(v, f, resolves(mod, record, f), "") for v, f in bindings], ""))
    return results


def render(results: List[RecordAudit], config_path: Path, ksm: Optional[Path],
           credential: str, skipped: int = 0) -> Tuple[str, int]:
    """``(report text, number of bindings that would not resolve)``.

    ``skipped`` counts the entries the plugin refused outright.  They are not in ``results``
    -- there is no record to read -- but startup will not apply them either, so they count
    towards the failure total: a gate that ignored them would let a map through with
    destinations that can never be bound (the `KSM_*` namespace, the bootstrap-token
    variable).
    """
    audited = sum(len(r.bindings) for r in results)
    ok = sum(1 for r in results for b in r.bindings if b.ok)
    total = audited + skipped
    lines = [
        "Keeper binding audit -- read-only",
        f"  config    {config_path}  ({audited} binding(s) over {len(results)} record reference(s))",
        f"  ksm       {ksm if ksm else 'not found; set secrets.keeper.binary_path'}",
        f"  token     {credential}",
        "",
    ]
    for record in results:
        lines.append(f"  {record.record_ref}")
        for binding in record.bindings:
            mark = "ok  " if binding.ok else "MISS"
            field = f"#{binding.field or 'password'}"
            detail = f"    record exposes: {', '.join(record.exposed)}" if record.exposed and not binding.ok else ""
            if binding.detail and not detail:
                detail = f"    {binding.detail}"
            lines.append(f"    {mark}  {binding.var:<24} {field:<14}{detail}")
        lines.append("")
    if total:
        refused = f" ({skipped} refused by the plugin, see above)" if skipped else ""
        lines.append(f"  {ok} of {total} binding(s) would resolve; "
                     f"{total - ok} would not{refused}.")
    lines.append("  A `#field` that MISSes is the one thing a Commander map cannot answer: the")
    lines.append("  label has to exist on the KSM record (or be a field type like `password`).")
    return "\n".join(lines) + "\n", total - ok


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit `secrets.keeper.env` against the `ksm` CLI without installing "
                    "or changing anything.",
    )
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME,
                        help=f"Hermes home to read (default: {DEFAULT_HOME})")
    parser.add_argument("--config", type=Path, default=None,
                        help="config.yaml to read (default: <home>/config.yaml)")
    parser.add_argument("--ksm", type=Path, default=None,
                        help="ksm binary to use (default: secrets.keeper.binary_path, else PATH)")
    parser.add_argument("--token-env", default=None,
                        help="bootstrap-token variable (default: secrets.keeper.token_env, "
                             "else KSM_TOKEN)")
    args = parser.parse_args(argv)

    home = args.home.expanduser()
    config_path = (args.config.expanduser() if args.config else home / "config.yaml")

    mod = load_plugin()
    section, error = read_section(config_path)
    if error:
        print(f"audit: {error}", file=sys.stderr)
        return 2

    references = section.get("env")
    if not isinstance(references, dict) or not references:
        print(f"audit: {config_path} has `secrets.keeper` but no `env:` map to audit",
              file=sys.stderr)
        return 2

    view = build_view(home, mod)
    token_env = args.token_env or str(section.get("token_env") or mod._DEFAULT_TOKEN_ENV)

    # The plugin decides what may be bound at all: a destination in the `KSM_*` namespace,
    # or the bootstrap-token variable, would replace the credential the child authenticates
    # with, and `fetch()` refuses both.  A binding the fetch refuses must not be reported as
    # one that would resolve, so the audit plans the map with the same function, and prints
    # the same reasons.
    by_record, skipped = mod._plan_bindings(references, token_env)
    for reason in skipped:
        print(f"audit: {reason}", file=sys.stderr)
    if not by_record:
        print("audit: nothing to audit: every entry was skipped (see the reasons above)",
              file=sys.stderr)
        return 2

    binary_path = str(args.ksm) if args.ksm else str(section.get("binary_path") or "")
    ksm = mod.find_ksm(binary_path)
    if ksm is None:
        print(f"audit: no usable `ksm` binary ({binary_path or 'PATH'}); install it or pass "
              "--ksm", file=sys.stderr)
        return 2

    budget = mod.KeeperSource().fetch_timeout_seconds(section)
    results = audit(mod, ksm, view, token_env, by_record, budget)
    report, unresolved = render(results, config_path, ksm,
                                credential_line(home, view, token_env),
                                skipped=len(skipped))
    sys.stdout.write(report)
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
