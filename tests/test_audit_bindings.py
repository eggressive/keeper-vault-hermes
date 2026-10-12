"""The read-only binding audit (``scripts/audit_bindings.py``).

It runs in a user's shell *before* anything is installed, so what needs pinning is that it
reads the same map, the same credential and the same field rules the plugin does, asks `ksm`
once per distinct record, returns an exit status a script can gate on, and never puts a
resolved value in its report.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def audit():
    """The audit script, imported as a module (it needs Hermes only when it runs)."""
    spec = importlib.util.spec_from_file_location(
        "keeper_audit_under_test", REPO_ROOT / "scripts" / "audit_bindings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_home(tmp_path: Path, env_map: dict, *, token_env: str = "", dotenv: dict | None = None):
    """A throwaway Hermes home: ``config.yaml`` with `secrets.keeper.env` (+ optional .env)."""
    home = tmp_path / "hermes"
    home.mkdir(exist_ok=True)
    lines = ["secrets:", "  keeper:", "    enabled: true"]
    if token_env:
        lines.append(f"    token_env: {token_env}")
    lines.append("    env:")
    for var, ref in env_map.items():
        lines.append(f'      {var}: "{ref}"')
    config = home / "config.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if dotenv:
        (home / ".env").write_text(
            "".join(f"{name}={value}\n" for name, value in dotenv.items()), encoding="utf-8")
    return home, config


def _run(audit, home: Path, config: Path, ksm: Path, *extra: str) -> int:
    return audit.main(["--home", str(home), "--config", str(config), "--ksm", str(ksm), *extra])


def test_it_reports_what_resolves_and_never_a_value(audit, fake_ksm_bin, tmp_path, capsys):
    home, config = _write_home(tmp_path, {
        "OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789",
        "OPENAI_ORG_ID": "XKQd9AbCdef123456789#org",
        "NO_SUCH_FIELD": "XKQd9AbCdef123456789#nope",
        "RECORD_MISSING": "nosuchrecord000000",
    })

    code = _run(audit, home, config, fake_ksm_bin)
    out = capsys.readouterr().out

    assert code == 1, "one unresolved binding has to be visible in the exit status"
    assert "2 of 4 binding(s) would resolve; 2 would not." in out
    # The record that resolved shows what it can be bound by, so the fix is a label to add.
    assert "record exposes: title, login, password, org, text" in out
    assert "Cannot find requested record(s)." in out, "the CLI's own failure text is kept"
    # Values from the fixture records behind these UIDs: none of them may appear (conftest).
    for value in ("sk-prod-KEY-12345", "sk-user-abc", "org-xyz789"):
        assert value not in out


def test_it_calls_ksm_once_per_distinct_record(audit, fake_ksm_bin, ksm_argv_log, tmp_path):
    home, config = _write_home(tmp_path, {
        "ONE": "XKQd9AbCdef123456789#password",
        "TWO": "XKQd9AbCdef123456789#org",
        "THREE": "aaaa1111",
    })

    _run(audit, home, config, fake_ksm_bin)

    calls = [line for line in ksm_argv_log.read_text().splitlines() if line.strip()]
    assert len(calls) == 2, "two records, one `ksm secret get` each"
    assert calls[0] == '["secret", "get", "--json", "--", "XKQd9AbCdef123456789"]'
    assert calls[1] == '["secret", "get", "--json", "--", "aaaa1111"]'


def test_it_reads_the_credential_the_way_the_plugin_does(audit, fake_ksm_bin, tmp_path,
                                                         monkeypatch, capsys):
    monkeypatch.delenv("KSM_TOKEN", raising=False)
    home, config = _write_home(
        tmp_path, {"OPENAI_API_KEY": "XKQd9AbCdef123456789#password"},
        token_env="MY_KEEPER_TOKEN", dotenv={"MY_KEEPER_TOKEN": "fake-one-time-access-token"})

    code = _run(audit, home, config, fake_ksm_bin)
    out = capsys.readouterr().out

    # `token_env` renames the variable and the profile's .env is where a profile's credential
    # lives; the fake `ksm` refuses to resolve anything without one, so `code == 0` is the
    # end-to-end proof that the token reached the child under the name it reads.
    assert code == 0
    assert f"MY_KEEPER_TOKEN, from {home / '.env'}" in out
    assert "1 of 1 binding(s) would resolve" in out


def test_it_skips_entries_the_plugin_would_skip(audit, fake_ksm_bin, tmp_path, capsys):
    home, config = _write_home(tmp_path, {
        "GOOD": "XKQd9AbCdef123456789",
        "bad-name": "XKQd9AbCdef123456789",
    })

    code = _run(audit, home, config, fake_ksm_bin)
    captured = capsys.readouterr()

    assert code == 0
    assert "audit: skipping 'bad-name': not a valid env-var name" in captured.err
    assert "1 binding(s) over 1 record reference(s)" in captured.out


def test_it_stops_with_a_message_when_it_cannot_run(audit, tmp_path, capsys):
    home, config = _write_home(tmp_path, {"ONE": "XKQd9AbCdef123456789"})

    assert _run(audit, home, config, tmp_path / "no-such-ksm") == 2
    assert "no usable `ksm` binary" in capsys.readouterr().err

    empty_home, empty_config = _write_home(tmp_path, {})
    assert audit.main(["--home", str(empty_home), "--config", str(empty_config)]) == 2
    assert "no `env:` map to audit" in capsys.readouterr().err

    missing = tmp_path / "elsewhere.yaml"
    assert audit.main(["--home", str(empty_home), "--config", str(missing)]) == 2
    assert "cannot read" in capsys.readouterr().err

    other_home = tmp_path / "other"
    other_home.mkdir()
    other_config = other_home / "config.yaml"
    other_config.write_text("model:\n  default: x\n", encoding="utf-8")
    assert audit.main(["--home", str(other_home), "--config", str(other_config)]) == 2
    assert "has no `secrets.keeper` section" in capsys.readouterr().err


def test_every_label_it_reports_can_actually_be_bound(audit, keeper_source):
    """The inventory has to be an answer, not a guess: each name resolves via `_field_value`."""
    _source, mod = keeper_source
    record = {
        "uid": "r1",
        "title": "T",
        "notes": "",                       # present but empty -> not bindable
        "fields": [
            {"label": "login", "type": "login", "value": ["u"]},
            {"label": 5, "type": "text", "value": ["numeric"]},   # unusable label
            {"label": "empty", "type": "text", "value": [""]},    # empty value
        ],
        "custom": [{"label": "org", "type": "text", "value": ["o"]}],
    }

    labels = audit.exposed_labels(record, mod)

    assert {"login", "title", "org"} <= set(labels)
    assert "notes" not in labels and "empty" not in labels
    for label in labels:
        assert audit.resolves(mod, record, label), label
