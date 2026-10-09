"""Test fixtures shared across the keeper-vault test suite.

Provides a fake ``ksm`` CLI that returns Keeper-shaped JSON, so the plugin's
real code path (subprocess -> parse -> field extract) is exercised end to end
without touching a live vault.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

# A small but realistic Keeper record set, keyed by the reference the plugin passes
# through (the fake resolves whatever token arrives; the real CLI resolves a UID).
_FAKE_RECORDS = {
    # Shapes below copy what `ksm secret get --json` really emits: the record key is
    # "uid", standard fields live under "fields" and custom fields under "custom"
    # (KSM-820 renamed that from "custom_fields"), and every field `value` is an
    # ARRAY even for a single scalar.  Fixtures that spell these differently are how
    # the `customFields` extraction bug stayed invisible.
    "XKQd9AbCdef123456789": {
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
    "My Login Record": {
        "uid": "aaaa1111",
        "title": "My Login Record",
        "fields": [
            {"label": "password", "type": "password", "value": ["anthropic-secret-999"]},
        ],
    },
    # A record from an older CLI that still spelled custom fields "custom_fields".
    "ZZZlegacySpellingRecord1": {
        "uid": "ZZZlegacySpellingRecord1",
        "title": "Legacy Spelling",
        "fields": [
            {"label": "password", "type": "password", "value": ["legacy-spelling-secret"]},
        ],
        "custom_fields": [
            {"label": "tier", "type": "text", "value": ["tier-gold"]},
        ],
    },
}


@pytest.fixture
def fake_ksm_bin(tmp_path: Path, monkeypatch) -> Path:
    """Write a fake ``ksm`` script to a temp dir and prepend it to PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ksm"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, json\n"
        "argv = sys.argv[1:]\n"
        "# Record the invocation for tests that assert the real CLI contract.\n"
        "log = os.environ.get('KSM_TEST_ARGV_LOG')\n"
        "if log:\n"
        "    with open(log, 'a', encoding='utf-8') as fh:\n"
        "        fh.write(json.dumps(argv) + '\\n')\n"
        "# `ksm` declares --color/--no-color on the ROOT group only, so any flag\n"
        "# click does not know AFTER `secret get` is a hard usage error (exit 2) and\n"
        "# no record is ever fetched.  Assert the exact invocation the plugin sends\n"
        "# instead of accepting whatever argv arrives (which is how the shipped\n"
        "# `--no-color` bug kept this suite green).\n"
        "expected = ['secret', 'get', '--json', '--', '<record-ref>']\n"
        "if len(argv) != len(expected) or argv[:-1] != expected[:-1]:\n"
        "    sys.stderr.write('fake ksm: unexpected argv %r; expected %r (the real CLI"
        " exits 2 on an unknown flag after `secret get`)\\n' % (argv, expected))\n"
        "    sys.exit(2)\n"
        "ref = argv[-1]\n"
        "records = " + repr(_FAKE_RECORDS) + "\n"
        "if ref in records:\n"
        "    print(json.dumps([records[ref]]))\n"
        "else:\n"
        "    sys.stderr.write('record not found: %s\\n' % ref)\n"
        "    sys.exit(1)\n"
    )
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
def keeper_source():
    """Import the plugin module and return a KeeperSource instance."""
    import importlib.util

    here = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("keeper_plugin_under_test", here / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.KeeperSource(), mod
