"""Test fixtures shared across the keeper-vault test suite.

Provides a fake ``ksm`` CLI that returns Keeper-shaped JSON, so the plugin's
real code path (subprocess -> parse -> field extract) is exercised end to end
without touching a live vault.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fake records
# ---------------------------------------------------------------------------
# A flat record list, because the fake resolves references the way the real CLI does:
# a positional argument is filtered against record UIDs, `--title` against titles.  A
# title may match several records; a UID may not.
#
# The record shapes copy what `ksm secret get --json` really emits: the record key is
# "uid", standard fields live under "fields", custom fields under "custom" (KSM-820
# renamed that from "custom_fields"), and every field `value` is an ARRAY even for a
# single scalar.  Fixtures that spell these differently are how the `customFields`
# extraction bug stayed invisible.
_FAKE_RECORDS = [
    {
        "uid": "XKQd9AbCdef123456789",
        "title": "OpenAI Prod",
        "type": "login",
        "notes": "",
        "fields": [
            {"label": "login", "type": "login", "value": ["sk-user-abc"]},
            {"label": "password", "type": "password", "value": ["sk-prod-KEY-12345"]},
        ],
        "custom": [
            {"label": "org", "type": "text", "value": ["org-xyz789"]},
            # A label that is not a string must not raise out of field extraction.
            {"label": 5, "type": "text", "value": ["ignored-numeric-label"]},
        ],
        "files": [],
        "links": [],
    },
    {
        "uid": "aaaa1111",
        "title": "My Login Record",
        "fields": [
            {"label": "password", "type": "password", "value": ["anthropic-secret-999"]},
        ],
    },
    # A title containing '#': only the LAST '#' is the field delimiter.
    {
        "uid": "bbbb2222",
        "title": "Prod #1 DB",
        "fields": [
            {"label": "password", "type": "password", "value": ["hash-title-secret"]},
        ],
    },
    # Two records share a title: a title lookup can match more than one record.
    {
        "uid": "cccc3333",
        "title": "Shared Title",
        "fields": [{"label": "password", "type": "password", "value": ["first-match"]}],
    },
    {
        "uid": "dddd4444",
        "title": "Shared Title",
        "fields": [{"label": "password", "type": "password", "value": ["second-match"]}],
    },
    # A record from an older CLI that still spelled custom fields "custom_fields".
    {
        "uid": "ZZZlegacySpellingRecord1",
        "title": "Legacy Spelling",
        "fields": [
            {"label": "password", "type": "password", "value": ["legacy-spelling-secret"]},
        ],
        "custom_fields": [
            {"label": "tier", "type": "text", "value": ["tier-gold"]},
        ],
    },
]


# ---------------------------------------------------------------------------
# Fake `ksm` CLI
# ---------------------------------------------------------------------------
# __RECORDS__ is substituted with _FAKE_RECORDS.  The script accepts ONLY the two
# invocations the plugin may send (`secret get --json -- <uid>` and
# `secret get --json --title=<title>`), because the real CLI rejects anything else
# during click argument parsing with exit 2 -- that guard is what caught the shipped
# `--no-color` bug, where a fake that took `sys.argv[-1]` happily accepted any flag.
_FAKE_KSM = '''#!/usr/bin/env python3
"""Stand-in for the Keeper Secrets Manager CLI (`ksm secret get --json ...`)."""
import json
import os
import sys

records = __RECORDS__

argv = sys.argv[1:]
log = os.environ.get("KSM_TEST_ARGV_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\\n")
# Names only, never values: lets a test assert what the child did and did not inherit.
envlog = os.environ.get("KSM_TEST_ENV_LOG")
if envlog:
    with open(envlog, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sorted(os.environ)) + "\\n")


def reject(reason):
    sys.stderr.write("fake ksm: %s (argv=%r)\\n" % (reason, argv))
    sys.exit(2)


if argv[:3] != ["secret", "get", "--json"]:
    reject("unexpected command")
tail = argv[3:]
if len(tail) == 2 and tail[0] == "--":
    # Positional argument: the CLI filters on record UIDs server-side, so a title
    # given positionally matches nothing.
    ref, by = tail[1], "uid"
elif len(tail) == 1 and tail[0].startswith("--title="):
    ref, by = tail[0].split("=", 1)[1], "title"
else:
    reject("expected `-- <uid>` or `--title=<title>`")

# `ksm` loads its profile AFTER argument parsing: KSM_CONFIG, else
# KSM_CONFIG_BASE64_1, else KSM_TOKEN, else the keyring / keeper.ini.  Only a token is
# modelled, so a missing credential reproduces the CLI's own error -- which is also how
# a test proves the plugin delivered the token under the name the CLI actually reads.
if not (os.environ.get("KSM_CONFIG") or os.environ.get("KSM_CONFIG_BASE64_1")
        or os.environ.get("KSM_TOKEN")):
    sys.stderr.write("Error: The Keeper SDK client has not been loaded. "
                     "The INI config might not be set.\\n")
    sys.exit(1)

matches = [r for r in records if r.get(by) == ref]
if not matches:
    # Same wording the real CLI uses when a reference matches nothing.
    sys.stderr.write("ksm had a problem: Cannot find requested record(s).\\n")
    sys.exit(1)
# `_adjust_records`: one match is a bare object, several are an array (no --force-array).
print(json.dumps(matches[0] if len(matches) == 1 else matches))
'''


@pytest.fixture(autouse=True)
def ksm_bootstrap_token(monkeypatch) -> str:
    """Provide the bootstrap token every working setup has (``KSM_TOKEN``).

    The fake ``ksm`` refuses to resolve anything without a credential, like the CLI, so
    every test that reaches the child also exercises token delivery.  Tests for a custom
    ``token_env`` delete this variable and set their own.
    """
    token = "fake-one-time-access-token"
    monkeypatch.setenv("KSM_TOKEN", token)
    return token


@pytest.fixture
def fake_ksm_bin(tmp_path: Path, monkeypatch) -> Path:
    """Write the fake ``ksm`` script to a temp dir and prepend it to PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ksm"
    script.write_text(_FAKE_KSM.replace("__RECORDS__", repr(_FAKE_RECORDS)))
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return script


@pytest.fixture
def ksm_argv_log(tmp_path: Path, monkeypatch) -> Path:
    """File the fake ``ksm`` appends its argv to, one JSON list per line.

    ``KSM_TEST_ARGV_LOG`` reaches the child through the plugin's own allowlist
    (anything named ``KSM_*`` is passed through), so tests can observe the exact
    invocation without a production-only hook.
    """
    log = tmp_path / "ksm_argv.jsonl"
    monkeypatch.setenv("KSM_TEST_ARGV_LOG", str(log))
    return log


@pytest.fixture
def ksm_child_env_log(tmp_path: Path, monkeypatch) -> Path:
    """File the fake ``ksm`` appends its inherited variable NAMES to, one list per line."""
    log = tmp_path / "ksm_child_env.jsonl"
    monkeypatch.setenv("KSM_TEST_ENV_LOG", str(log))
    return log


@pytest.fixture
def keeper_source():
    """Import the plugin module and return a KeeperSource instance."""
    import importlib.util

    here = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("keeper_plugin_under_test", here / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.KeeperSource(), mod
