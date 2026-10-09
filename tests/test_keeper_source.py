"""Conformance + integration tests for the Keeper secret source.

These run against the REAL Hermes secret-source contract (agent.secret_sources.*),
cloned from NousResearch/hermes-agent at the pinned tag.  They prove the plugin
implements SecretSource correctly and that it behaves inside the multi-vault
orchestrator exactly like the bundled Bitwarden/1Password sources.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Ensure the Hermes agent package is importable. CI sets PYTHONPATH=/opt/hermes-agent;
# locally you can run: PYTHONPATH=/root/.hermes/hermes-agent pytest tests/
import agent.secret_sources.base as base  # noqa: F401  (imports the contract)
from agent.secret_sources.base import FetchResult, SecretSource
from agent.secret_sources.registry import (
    _reset_registry_for_tests,
    apply_all,
    register_source,
)
from tests.secret_sources.conformance import SecretSourceConformance


# ---------------------------------------------------------------------------
# Conformance: provide `source` via a fixture so Hermes's own
# SecretSourceConformance base class is collected and run directly by pytest
# (its 10 checks, with the real tmp_path/minimal_cfg/monkeypatch fixtures).
# ---------------------------------------------------------------------------


@pytest.fixture
def source(keeper_source):
    _src, mod = keeper_source
    return mod.KeeperSource()


class TestKeeperConformance(SecretSourceConformance):
    @pytest.fixture
    def source(self, keeper_source):
        _src, mod = keeper_source
        return mod.KeeperSource()


# ---------------------------------------------------------------------------
# Field extraction + warnings (real fetch path via fake ksm)
# ---------------------------------------------------------------------------


def test_fetch_resolves_mapped_refs(keeper_source, fake_ksm_bin):
    src, mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = {}
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {
                "OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password",
                "OPENAI_ORG": "XKQd9AbCdef123456789#org",
                "ANTHROPIC_API_KEY": "My Login Record",  # default field = password
                "OPENAI_USER": "XKQd9AbCdef123456789#login",
                "BAD_REF": "DoesNotExistRecord#password",
                "BAD_FIELD": "XKQd9AbCdef123456789#nope",
            },
        }
    }
    report = apply_all(cfg, Path("/tmp/keeper-vous-test"), environ=env)
    sr = report.sources[0]
    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"
    assert env["OPENAI_ORG"] == "org-xyz789"
    assert env["ANTHROPIC_API_KEY"] == "anthropic-secret-999"
    assert env["OPENAI_USER"] == "sk-user-abc"
    assert "BAD_REF" not in env and "BAD_FIELD" not in env
    assert any("no value for field" in w for w in sr.result.warnings)
    assert any("record not found" in w for w in sr.result.warnings)
    _reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Multi-vault precedence ladder (Keeper mapped vs a bulk source)
# ---------------------------------------------------------------------------


def test_keeper_beats_bulk_and_protects_token(keeper_source, fake_ksm_bin):
    src, mod = keeper_source

    class _Bulk(SecretSource):
        name = "otherbulk"
        label = "OtherBulk"
        shape = "bulk"

        def protected_env_vars(self, cfg):
            return frozenset({"OTHERBULK_TOKEN"})

        def fetch(self, cfg, home_path):
            r = FetchResult()
            r.secrets = {
                "OPENAI_API_KEY": "BULK-override-attempt",
                "OTHERBULK_TOKEN": "should-never-be-applied",
                "BULK_ONLY": "bulk-value",
            }
            return r

    _reset_registry_for_tests()
    register_source(_Bulk())
    register_source(src)

    cfg = {
        "sources": ["keeper", "otherbulk"],
        "keeper": {
            "enabled": True,
            "env": {
                "OPENAI_API_KEY": "XKQd9AbCdef123456789#password",
                "KEEPER_ONLY": "XKQd9AbCdef123456789#org",
            },
        },
        "otherbulk": {"enabled": True},
    }
    env = {"OPENAI_API_KEY": "preexisting-dotenv"}
    report = apply_all(cfg, Path("/tmp/keeper-vous-test"), environ=env)

    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"  # mapped beats bulk
    assert env.get("OTHERBULK_TOKEN") is None  # protected token never applied
    assert env["BULK_ONLY"] == "bulk-value"  # non-contested bulk applies
    assert env.get("KEEPER_ONLY") == "org-xyz789"
    assert any("first source wins" in c for c in report.conflicts)
    _reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Failure isolation: missing ksm binary -> one-line warning, no crash
# ---------------------------------------------------------------------------


def test_missing_binary_reports_error_kind(keeper_source, monkeypatch):
    src, mod = keeper_source
    # Force find_ksm to see no binary by clearing PATH of ksm.
    monkeypatch.setenv("PATH", "/usr/bin")
    _reset_registry_for_tests()
    register_source(src)
    env = {}
    cfg = {"keeper": {"enabled": True, "env": {"X": "ksm://somerecord"}}}
    report = apply_all(cfg, Path("/tmp/keeper-vous-test"), environ=env)
    sr = report.sources[0]
    assert sr.result.error is not None
    assert sr.result.error_kind is mod.ErrorKind.BINARY_MISSING
    assert "X" not in env
    _reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Child argv == the real ksm CLI contract
# ---------------------------------------------------------------------------


def test_ksm_child_argv_matches_cli_contract(keeper_source, fake_ksm_bin, ksm_argv_log, tmp_path):
    """The child argv must be exactly ``ksm secret get --json -- <ref>``.

    Regression guard for the shipped defect: the invocation used to carry
    ``--no-color``, but the ksm CLI declares ``--color/--no-color`` on the ROOT
    group only.  ``ksm secret get --no-color --json -- <ref>`` therefore died in
    click argument parsing -- ``Error: No such option '--no-color'.``, exit 2 --
    before any vault call, so no secret ever resolved for any configuration.  A
    fake ``ksm`` that ignored argv is why the suite stayed green.
    """
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = {}
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {"OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password"},
        }
    }
    try:
        apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    calls = [json.loads(line) for line in ksm_argv_log.read_text(encoding="utf-8").splitlines()]
    assert calls == [["secret", "get", "--json", "--", "XKQd9AbCdef123456789"]]
    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"
