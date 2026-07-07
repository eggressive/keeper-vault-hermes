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

# A small but realistic Keeper record set, keyed by record-UID and by title
# (ksm secret get accepts either).
_FAKE_RECORDS = {
    "XKQd9AbCdef123456789": {
        "recordUid": "XKQd9AbCdef123456789",
        "title": "OpenAI Prod",
        "type": "login",
        "fields": [
            {"type": "login", "label": "login", "value": "sk-user-abc"},
            {"type": "password", "label": "password", "value": "sk-prod-KEY-12345"},
        ],
        "customFields": [
            {"type": "text", "label": "org", "value": "org-xyz789"},
        ],
    },
    "My Login Record": {
        "recordUid": "aaaa1111",
        "title": "My Login Record",
        "fields": [
            {"type": "password", "label": "password", "value": "anthropic-secret-999"},
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
        "import sys, json\n"
        "ref = sys.argv[-1]\n"
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
def keeper_source():
    """Import the plugin module and return a KeeperSource instance."""
    import importlib.util

    here = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("keeper_plugin_under_test", here / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.KeeperSource(), mod
