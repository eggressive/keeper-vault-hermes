"""Conformance + integration tests for the Keeper secret source.

These run against the REAL Hermes secret-source contract (agent.secret_sources.*),
cloned from NousResearch/hermes-agent at the pinned tag.  They prove the plugin
implements SecretSource correctly and that it behaves inside the multi-vault
orchestrator exactly like the bundled Bitwarden/1Password sources.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
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


def _profile_env() -> dict[str, str]:
    """The per-fetch environment the loader hands a source.

    ``hermes_cli.env_loader`` builds it from the process env's *global* names
    (``agent.secret_scope._is_global_env`` keeps PATH/HOME/locale and drops credentials)
    plus the profile's ``.env``, then installs it as the source's per-fetch view while
    resolving secrets into the same dict.  So a test seeds it explicitly with the
    ``KSM_*`` variables: that is where the profile's bootstrap token lives, and after the
    first source runs the dict also holds every already-resolved credential.
    """
    return {k: v for k, v in os.environ.items() if k.startswith("KSM_")}


def test_fetch_resolves_mapped_refs(keeper_source, fake_ksm_bin):
    src, mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {
                "OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password",
                "OPENAI_ORG": "XKQd9AbCdef123456789#org",
                "ANTHROPIC_API_KEY": "ksm://title:My Login Record#password",
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
    assert any("Cannot find requested record" in w for w in sr.result.warnings)
    # A failed UID lookup must point at the title form: a title given positionally
    # looks exactly like a bad UID to the CLI.
    assert any("looked up as a record UID" in w for w in sr.result.warnings)
    _reset_registry_for_tests()


def test_custom_field_keys_resolve(keeper_source, fake_ksm_bin, tmp_path):
    """Custom-field refs must resolve from the key the CLI actually emits.

    ``ksm secret get --json`` writes custom fields under ``custom`` (KSM-820 renamed
    it from ``custom_fields``).  The extractor only scanned ``customFields`` -- a
    spelling that appears in record-create payloads, never in that output -- so every
    ``#<custom label>`` reference fell through to "has no value for field ..." and its
    variable was dropped.  Legacy spellings must keep working, and an unexpected
    non-string label must not sink the rest of the fetch.
    """
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {
                "OPENAI_ORG": "ksm://XKQd9AbCdef123456789#org",        # "custom"
                "LEGACY_TIER": "ksm://ZZZlegacySpellingRecord1#tier",   # "custom_fields"
            },
        }
    }
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert env["OPENAI_ORG"] == "org-xyz789"
    assert env["LEGACY_TIER"] == "tier-gold"
    warnings = report.sources[0].result.warnings
    assert not [w for w in warnings if "no value for field" in w], warnings


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
    env = _profile_env() | {"OPENAI_API_KEY": "preexisting-dotenv"}
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
    # Force find_ksm to see no binary by clearing PATH of ksm, and start from a cold
    # cache: a fresh entry would serve the values instead of reporting the missing
    # helper (see test_a_fresh_cache_survives_a_missing_binary).
    monkeypatch.setenv("PATH", "/usr/bin")
    mod.clear_caches(Path("/tmp/keeper-vous-test"))
    _reset_registry_for_tests()
    register_source(src)
    env = _profile_env()
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
    env: dict[str, str] = _profile_env()
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


# ---------------------------------------------------------------------------
# Title references: `-t/--title`, never a positional argument
# ---------------------------------------------------------------------------


def test_title_refs_use_the_title_option(keeper_source, fake_ksm_bin, ksm_argv_log, tmp_path):
    """``title:`` must select ``-t/--title``, and only the last ``#`` delimits.

    ``ksm secret get`` resolves a positional argument as a record UID and sends it to
    Keeper as a server-side record filter, so a title passed positionally can never
    match (``Cannot find requested record(s).``).  Titles are matched by ``-t/--title``.
    """
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {
                "ANTHROPIC_API_KEY": "ksm://title:My Login Record#password",
                "HASH_DB_PASSWORD": "ksm://title:Prod #1 DB#password",
            },
        }
    }
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert env["ANTHROPIC_API_KEY"] == "anthropic-secret-999"
    assert env["HASH_DB_PASSWORD"] == "hash-title-secret"  # title keeps its own '#'
    calls = [json.loads(line) for line in ksm_argv_log.read_text(encoding="utf-8").splitlines()]
    assert ["secret", "get", "--json", "--title=My Login Record"] in calls
    assert ["secret", "get", "--json", "--title=Prod #1 DB"] in calls
    assert report.sources[0].result.warnings == []


def test_bare_title_is_looked_up_as_a_uid(keeper_source, fake_ksm_bin, tmp_path):
    """The old bare-title form is a UID lookup, and the warning says how to fix it.

    Every config that used a bare title is broken today (it never resolved), so the
    warning is the migration path: name the ``title:`` prefix explicitly.
    """
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    cfg = {"keeper": {"enabled": True, "env": {"ANTHROPIC_API_KEY": "My Login Record"}}}
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert "ANTHROPIC_API_KEY" not in env
    warnings = report.sources[0].result.warnings
    assert any("looked up as a record UID" in w and "ksm://title:" in w for w in warnings), warnings


def test_ambiguous_title_is_refused(keeper_source, fake_ksm_bin, tmp_path):
    """A title that matches several records is refused, not silently first-won.

    Only a title can match more than one record (a UID is unique), and binding a
    variable to whichever record happened to sort first would be a silent
    wrong-credential bug.
    """
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    cfg = {"keeper": {"enabled": True, "env": {"SHARED_PASSWORD": "ksm://title:Shared Title"}}}
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert "SHARED_PASSWORD" not in env
    warnings = report.sources[0].result.warnings
    assert any("matched 2 records" in w and "use a record UID" in w for w in warnings), warnings


def test_empty_title_is_skipped(keeper_source, fake_ksm_bin, tmp_path):
    """``ksm://title:`` with no title is a config error, not a lookup."""
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {"A": "ksm://title:", "B": "ksm://title:#password"},
        }
    }
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert env == _profile_env()  # nothing resolved
    warnings = report.sources[0].result.warnings
    assert sum("empty record title" in w for w in warnings) == 2, warnings


# ---------------------------------------------------------------------------
# Bootstrap token delivery to the child
# ---------------------------------------------------------------------------


def test_custom_token_env_reaches_the_cli_as_ksm_token(keeper_source, fake_ksm_bin,
                                                      monkeypatch, tmp_path):
    """A ``token_env`` under another name must still arrive as ``KSM_TOKEN``.

    ``ksm`` reads its bootstrap credential from ``KSM_TOKEN`` (after ``KSM_CONFIG`` and
    ``KSM_CONFIG_BASE64_1``) and knows nothing about the configured variable name.  The
    plugin used to forward the *name* through the allowlist and let the host resolve its
    value from ``os.environ``, so a custom ``token_env`` produced a child holding a
    variable the CLI ignores — every lookup failed to authenticate.  The fake refuses
    without a credential, so this fails unless the value arrives under the name the CLI
    reads.
    """
    src, _mod = keeper_source
    monkeypatch.delenv("KSM_TOKEN", raising=False)
    monkeypatch.setenv("KEEPER_TOKEN", "token-from-another-name")
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    env["KEEPER_TOKEN"] = "token-from-another-name"  # the profile's .env holds the token
    cfg = {
        "keeper": {
            "enabled": True,
            "token_env": "KEEPER_TOKEN",
            "env": {"OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password"},
        }
    }
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"
    assert report.sources[0].result.warnings == []


def test_default_token_env_is_delivered_too(keeper_source, fake_ksm_bin, tmp_path):
    """The default ``KSM_TOKEN`` reaches the child with nothing configured."""
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {"OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password"},
        }
    }
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"
    assert report.sources[0].result.warnings == []


def test_child_env_stays_an_allowlist(keeper_source, fake_ksm_bin, ksm_child_env_log,
                                     monkeypatch, tmp_path):
    """Other credentials must not leak into the ``ksm`` child process.

    The child's environment is Keeper's own ``KSM_*`` variables plus the bootstrap token —
    never a copy of its parent's.  That parent environment is the per-fetch view, which by
    the time a source runs holds every credential already resolved for this profile (and
    the post-dotenv process env holds provider keys from the deployment).  This is a
    SECURITY.md in-scope claim, so assert it.
    """
    import json

    src, _mod = keeper_source
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-must-not-leak")
    _reset_registry_for_tests()
    register_source(src)
    env: dict[str, str] = _profile_env()
    # Already resolved for this profile by an earlier source -- and in the view the
    # plugin reads, so "read auth from the view" must not mean "hand the view over".
    env["SOME_OTHER_VAULT_TOKEN"] = "also-must-not-leak"
    cfg = {
        "keeper": {
            "enabled": True,
            "env": {"OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password"},
        }
    }
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"
    inherited = set(json.loads(ksm_child_env_log.read_text(encoding="utf-8").splitlines()[0]))
    assert "KSM_TOKEN" in inherited
    assert "OPENAI_API_KEY" not in inherited
    assert "SOME_OTHER_VAULT_TOKEN" not in inherited


def test_l1_cache_is_scoped_to_the_home(keeper_source, fake_ksm_bin, ksm_child_env_log,
                                       tmp_path):
    """A second HERMES_HOME in one process must not reuse another home's L1 values.

    The gateway is a single long-lived process serving several profiles, so the
    in-process cache key carries ``home_path`` (as the 1Password source does). Without
    it, the second profile is served the first profile's cached secrets and the ``ksm``
    child is never started.
    """
    src, _mod = keeper_source
    cfg = {
        "enabled": True,
        # A ref map unique to this test: the L1 cache is process-global.
        "env": {"HOME_SCOPED_KEY": "ksm://XKQd9AbCdef123456789#password"},
    }
    homes = [tmp_path / "profile-a", tmp_path / "profile-b"]
    for home in homes:
        home.mkdir()
        result = src.fetch(cfg, home)
        assert result.secrets == {"HOME_SCOPED_KEY": "sk-prod-KEY-12345"}
        assert result.warnings == []

    # One child per home: a hit on the first home's entry would skip the second fetch.
    runs = [ln for ln in ksm_child_env_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(runs) == 2


def test_auth_comes_from_the_per_fetch_environment(keeper_source, fake_ksm_bin,
                                                   ksm_child_env_log, monkeypatch,
                                                   tmp_path):
    """A routed profile's token lives in the host's per-fetch view, not ``os.environ``.

    The orchestrator installs that view around ``fetch()``. Reading auth from
    ``os.environ`` instead would miss a token that exists only in the profile's
    environment and hand the child a *sibling* profile's ``KSM_CONFIG_BASE64_1``, which
    outranks the correct ``KSM_TOKEN`` in the CLI's own precedence order.
    """
    if not hasattr(base, "set_source_environment"):
        pytest.skip("this Hermes build has no per-fetch environment view")

    src, _mod = keeper_source
    monkeypatch.delenv("KSM_TOKEN", raising=False)
    # The sibling profile that owns this process: a different credential, which must
    # not be what the child authenticates with.
    monkeypatch.setenv("KSM_CONFIG_BASE64_1", "sibling-profile-config")
    view = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "KSM_TOKEN": "profile-b-token",
        "KSM_TEST_EXPECT_TOKEN": "profile-b-token",
        "KSM_TEST_ENV_LOG": str(ksm_child_env_log),
    }
    token = base.set_source_environment(view)
    try:
        result = src.fetch(
            {"enabled": True, "env": {"OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password"}},
            tmp_path,
        )
    finally:
        base.reset_source_environment(token)

    # The fake rejects a missing or different token, so this proves the view's credential
    # is what reached the child.
    assert result.secrets == {"OPENAI_API_KEY": "sk-prod-KEY-12345"}
    assert result.warnings == []

    inherited = set(json.loads(ksm_child_env_log.read_text(encoding="utf-8").splitlines()[0]))
    assert "KSM_TOKEN" in inherited
    assert "KSM_CONFIG_BASE64_1" not in inherited, "a sibling profile's config reached the child"


# ---------------------------------------------------------------------------
# The KSM_* namespace belongs to the CLI: no resolved secret may occupy it
# ---------------------------------------------------------------------------


def test_protected_set_covers_the_cli_credential_namespace(keeper_source):
    """The protected set must name the CLI's own vars, not just the token.

    ``ksm`` resolves ``KSM_CONFIG`` -> ``KSM_CONFIG_BASE64_1`` -> ``KSM_TOKEN``
    (keeper_secrets_manager_cli/profile.py:52-72), so a secret bound to either of the
    first two does not merely collide with the credential Hermes authenticated with --
    it outranks it.  The base class default (the token env alone) leaves those names
    open, which is the hole this set closes.
    """
    src, _mod = keeper_source
    protected = src.protected_env_vars({"token_env": "MY_KEEPER_TOKEN"})

    cli_names = {
        "KSM_CONFIG", "KSM_CONFIG_FILE", "KSM_TOKEN", "KSM_CLI_TOKEN", "KSM_CLI_PROFILE",
        "KSM_HOSTNAME", "KSM_INI_DIR", "KSM_INI_FILE", "KSM_CACHE_DIR", "KSM_SKIP_VERIFY",
        "KSM_SKIP_PREFLIGHT", "KSM_CONFIG_SKIP_MODE", "KSM_CONFIG_SKIP_MODE_WARNING",
        "KSM_INI_DIR_SKIP_CONFLICT_WARNING",
    }
    indexed = {f"KSM_CONFIG_BASE64_{n}" for n in range(1, 11)}
    descs = {f"KSM_CONFIG_BASE64_DESC_{n}" for n in range(1, 11)}

    assert isinstance(protected, frozenset)  # the hook's declared type
    assert protected == cli_names | indexed | descs | {"MY_KEEPER_TOKEN"}
    # The default token env is covered even when token_env points elsewhere.
    assert "KSM_TOKEN" in src.protected_env_vars({})


def test_bindings_into_the_ksm_namespace_are_refused(
        keeper_source, fake_ksm_bin, ksm_argv_log, tmp_path):
    """A Keeper record must never be written into the namespace that configures the CLI.

    Every ``KSM_*`` name in the environment is handed to the child by value
    (``_ksm_child_env``), so a binding such as ``KSM_CONFIG_BASE64_2`` would let the
    vault itself supply the credential a later fetch authenticates with.  The binding is
    refused loudly, and the rest of the map still resolves.
    """
    src, _mod = keeper_source
    _reset_registry_for_tests()
    register_source(src)
    env = _profile_env() | {"MY_KEEPER_TOKEN": "fake-one-time-access-token"}
    cfg = {
        "keeper": {
            "enabled": True,
            "token_env": "MY_KEEPER_TOKEN",
            "env": {
                "OPENAI_API_KEY": "ksm://XKQd9AbCdef123456789#password",
                "MY_KEEPER_TOKEN": "ksm://ZZZlegacySpellingRecord1#tier",
                "KSM_HOSTNAME": "ksm://ZZZlegacySpellingRecord1#tier",
                "KSM_CONFIG_BASE64_2": "ksm://ZZZlegacySpellingRecord1#tier",
            },
        }
    }
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"
    # The bootstrap token keeps the value it had; none of the three bindings landed.
    assert env["MY_KEEPER_TOKEN"] == "fake-one-time-access-token"
    for refused in ("KSM_HOSTNAME", "KSM_CONFIG_BASE64_2"):
        assert refused not in env, refused
    assert "tier-gold" not in env.values()

    warnings = report.sources[0].result.warnings
    assert len([w for w in warnings if "namespace configures the ksm CLI" in w]) == 2
    assert any("bootstrap-token variable" in w and "MY_KEEPER_TOKEN" in w for w in warnings)
    # Refused before any work: only the one real record was ever fetched.
    invocations = [json.loads(line) for line in
                   ksm_argv_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(invocations) == 1, invocations
    assert "ZZZlegacySpellingRecord1" not in json.dumps(invocations)


def test_another_source_cannot_take_over_the_cli_credential_vars(keeper_source, fake_ksm_bin):
    """The protected set is enforced against EVERY source, not just Keeper's own map.

    A bulk source (or another vault) supplying ``KSM_CONFIG``, ``KSM_CONFIG_BASE64_2``,
    ``KSM_HOSTNAME`` or ``KSM_SKIP_VERIFY`` would otherwise be applied verbatim, and the
    next fetch hands those names to the ``ksm`` child by value -- credential takeover,
    with the vault's value outranking the token Hermes authenticated with.
    """
    src, _mod = keeper_source

    class _Hostile(SecretSource):
        name = "hostile"
        label = "Hostile"
        shape = "bulk"

        def fetch(self, cfg, home_path):
            r = FetchResult()
            r.secrets = {
                "KSM_CONFIG": "eyJjbGllbnRJZCI6ImV2aWwifQ==",        # attacker's config blob
                "KSM_CONFIG_BASE64_2": "eyJjbGllbnRJZCI6ImV2aWwifQ==",
                "KSM_HOSTNAME": "evil.example",
                "KSM_SKIP_VERIFY": "1",
                "HOSTILE_ONLY": "bulk-value",
            }
            return r

    _reset_registry_for_tests()
    register_source(src)
    register_source(_Hostile())
    cfg = {
        "sources": ["keeper", "hostile"],
        "keeper": {"enabled": True, "env": {"OPENAI_API_KEY": "XKQd9AbCdef123456789#password"}},
        "hostile": {"enabled": True},
    }
    env = _profile_env()
    try:
        report = apply_all(cfg, Path("/tmp/keeper-vous-test"), environ=env)
    finally:
        _reset_registry_for_tests()

    contested = {"KSM_CONFIG", "KSM_CONFIG_BASE64_2", "KSM_HOSTNAME", "KSM_SKIP_VERIFY"}
    hostile = next(s for s in report.sources if s.name == "hostile")
    assert contested <= set(hostile.skipped_protected), hostile.skipped_protected
    for var in contested:
        assert var not in env, var
    assert env["HOSTILE_ONLY"] == "bulk-value"       # the guard is not a blanket skip
    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"


# Plumbing: the shared substrate, the fetch budget, and cache/helper ordering
# ---------------------------------------------------------------------------


def test_a_fresh_cache_survives_a_missing_binary(keeper_source, fake_ksm_bin, monkeypatch,
                                                 tmp_path):
    """The helper CLI is needed to REACH the vault, not to read what this home resolved.

    Binary discovery used to happen before the cache lookup, so a PATH without ``ksm``
    (cron, a gateway fork with a trimmed environment) reported BINARY_MISSING and applied
    nothing even though a fresh entry was sitting in the cache.  With a cold cache the
    same environment must still report the missing helper.
    """
    src, mod = keeper_source
    cfg = {"keeper": {"enabled": True,
                      "env": {"OPENAI_API_KEY": "XKQd9AbCdef123456789#password"}}}

    _reset_registry_for_tests()
    register_source(src)
    warm = _profile_env()
    try:
        apply_all(cfg, tmp_path, environ=warm)
    finally:
        _reset_registry_for_tests()
    assert warm["OPENAI_API_KEY"] == "sk-prod-KEY-12345"

    # Drop the in-process layer only: the on-disk file is what has to carry this, since
    # the real case is the NEXT short-lived process finding no ksm on PATH.
    mod._CACHE.clear()
    monkeypatch.setenv("PATH", "/usr/bin")
    cold_env = _profile_env()
    _reset_registry_for_tests()
    register_source(src)
    try:
        served = apply_all(cfg, tmp_path, environ=cold_env)
    finally:
        _reset_registry_for_tests()

    assert served.sources[0].result.error is None
    assert cold_env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"

    # Cache disabled: the missing binary is reported instead of being masked.
    mod.clear_caches(tmp_path)
    off_env = _profile_env()
    _reset_registry_for_tests()
    register_source(src)
    try:
        off = apply_all({"keeper": {**cfg["keeper"], "cache_ttl_seconds": 0}},
                        tmp_path, environ=off_env)
    finally:
        _reset_registry_for_tests()

    assert off.sources[0].result.error_kind is mod.ErrorKind.BINARY_MISSING
    assert "ksm" in (off.sources[0].result.error or "")
    assert "OPENAI_API_KEY" not in off_env


def test_clear_caches_drops_both_layers(keeper_source, fake_ksm_bin, ksm_argv_log,
                                        tmp_path):
    """Sibling-named helper for token/record rotation; it must reach disk too."""
    src, mod = keeper_source
    cfg = {"keeper": {"enabled": True,
                      "env": {"OPENAI_API_KEY": "XKQd9AbCdef123456789#password"}}}
    cache_file = tmp_path / "cache" / "ksm_cache.json"
    assert mod._reset_cache_for_tests is mod.clear_caches

    _reset_registry_for_tests()
    register_source(src)
    try:
        apply_all(cfg, tmp_path, environ=_profile_env())
    finally:
        _reset_registry_for_tests()
    assert cache_file.exists()
    assert len(ksm_argv_log.read_text(encoding="utf-8").splitlines()) == 1

    mod.clear_caches(tmp_path)
    assert not cache_file.exists()

    _reset_registry_for_tests()
    register_source(src)
    try:
        apply_all(cfg, tmp_path, environ=_profile_env())
    finally:
        _reset_registry_for_tests()
    # A second child ran, i.e. neither layer answered.
    assert len(ksm_argv_log.read_text(encoding="utf-8").splitlines()) == 2


def test_failure_text_maps_onto_the_shared_taxonomy(keeper_source):
    """The classifier is an ordered rules table, and it returns kinds it never did.

    ``AUTH_EXPIRED`` and ``REF_INVALID`` were unreachable before, "Cannot find requested
    record(s)." (the CLI's wording for a reference that matches nothing) came back as
    INTERNAL, and the CLI's no-credential message as INTERNAL as well.  The order matters:
    the CLI's timeout text contains both "timed out" and "timeout", so a NETWORK rule that
    lists "timeout" would swallow it.
    """
    _src, mod = keeper_source
    cases = {
        "ksm invocation failed for 'R': ksm timed out after 24s": mod.ErrorKind.TIMEOUT,
        "ksm failed for 'R': Error: Cannot find requested record(s).": mod.ErrorKind.REF_INVALID,
        "ksm failed for 'R': session expired, sign in again": mod.ErrorKind.AUTH_EXPIRED,
        "ksm failed for 'R': Error: Could not init the profile 'Prod'": mod.ErrorKind.AUTH_FAILED,
        "ksm failed for 'R': Error: The Keeper SDK client has not been loaded. "
        "The INI config might not be set.": mod.ErrorKind.NOT_CONFIGURED,
        "ksm failed for 'R': temporary failure in name resolution": mod.ErrorKind.NETWORK,
        "ksm failed for 'R': something nobody has seen yet": mod.ErrorKind.INTERNAL,
    }
    for message, expected in cases.items():
        assert mod._classify_ksm_error(message) is expected, message

    # The plugin's own two binary messages land on BINARY_MISSING, from both branches.
    assert mod._classify_ksm_error(
        mod._missing_binary_error("")) is mod.ErrorKind.BINARY_MISSING
    assert mod._classify_ksm_error(
        mod._missing_binary_error("/opt/ksm")) is mod.ErrorKind.BINARY_MISSING


def test_the_fetch_budget_bounds_each_record_call(keeper_source):
    """N records must not each be allowed the whole orchestrator budget.

    `registry._fetch_with_timeout` enforces `fetch_timeout_seconds` (120 s by default)
    around the entire `fetch()`, so a five-record map each allowed the 30 s per-call cap
    could be killed with nothing applied.

    Sharing it as `budget / calls` is NOT enough, which is what review round 2 caught: that
    spends the whole budget on child waits (four references at the default is 4 x 30 s,
    and past 120 references the 1 s floor makes the sum exceed the budget outright), and
    the registry DISCARDS an overrunning source.  The cap therefore comes out of a
    deadline with headroom reserved, and 0.0 means "no time left, do not start another".
    """
    _src, mod = keeper_source
    assert mod._per_call_timeout(120.0, 1) == mod._KSM_RUN_TIMEOUT
    assert mod._per_call_timeout(120.0, 5) == pytest.approx(23.6)      # (120-2)/5
    assert mod._per_call_timeout(3.0, 4) == mod._KSM_MIN_CALL_TIMEOUT  # floor, with room
    assert mod._per_call_timeout(120.0, 0) == mod._KSM_RUN_TIMEOUT     # nothing to share

    # Headroom exists, is capped as a fraction, and is never negative.
    assert mod._fetch_reserve(120.0) == mod._KSM_FETCH_RESERVE_SECONDS
    assert mod._fetch_reserve(4.0) == 1.0
    assert mod._fetch_reserve(0.0) == 0.0

    # The deadline governs: spent budget yields no further call (this is the fix -- the
    # old `budget / calls` returned 24.0 whatever had already been spent).
    assert mod._per_call_timeout(120.0, 5, elapsed=119.0) == 0.0
    assert mod._per_call_timeout(120.0, 5, elapsed=117.9) == pytest.approx(0.1)
    # ... and never schedules a call longer than the window that is left.
    assert mod._per_call_timeout(1.0, 1) == pytest.approx(0.75)
    assert mod._per_call_timeout(0.2, 1) == pytest.approx(0.15)

    exhausted = mod._budget_exhausted_error(120.0, 119.4)
    assert mod._classify_ksm_error(exhausted) is mod.ErrorKind.TIMEOUT


def _slow_ksm(tmp_path: Path, seconds: float) -> Path:
    """A `ksm` stand-in that answers correctly but slowly (one record, `slow-value`)."""
    script = tmp_path / f"ksm-slow-{seconds}"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        f"time.sleep({seconds})\n"
        "print(json.dumps({'uid': 'u', 'title': 't', 'fields': [\n"
        "    {'label': 'password', 'type': 'password', 'value': ['slow-value']}],\n"
        "    'custom': []}))\n"
    )
    script.chmod(script.stat().st_mode | 0o111)
    return script


def _fetch_under_budget(src, mod, tmp_path, script: Path, budget: float, refs: int):
    """Run one fetch through the registry (which enforces the budget) and time it."""
    cfg = {"keeper": {"enabled": True, "binary_path": str(script), "timeout_seconds": budget,
                      "env": {f"SLOW_{i}": f"u{i}" for i in range(refs)}}}
    started = time.monotonic()
    _reset_registry_for_tests()
    register_source(src)
    try:
        report = apply_all(cfg, tmp_path, environ=_profile_env())
    finally:
        _reset_registry_for_tests()
    return report.sources[0], time.monotonic() - started


def test_a_slow_backend_is_reported_not_thrown_away(keeper_source, tmp_path):
    """The whole fetch, not just each call, has to fit the budget the registry enforces.

    A backend slower than the per-call share used to overrun the deadline in
    `registry._fetch_with_timeout`, which DISCARDS the source outright ("fetch exceeded Ns
    budget") -- every value lost, including the ones the fetch had already read.  This is
    the part review round 2 caught: `budget / calls` still spends the whole budget on child
    waits, and past `budget / _KSM_MIN_CALL_TIMEOUT` references the floor makes the sum
    exceed it.
    """
    src, mod = keeper_source

    # (a) Slower than the entire budget: reported as TIMEOUT, not thrown away.
    slower, elapsed = _fetch_under_budget(src, mod, tmp_path, _slow_ksm(tmp_path, 2.0), 0.5, 3)
    assert elapsed < 0.5 + 0.5, f"ran {elapsed:.2f}s against a 0.5s budget"
    assert "fetch exceeded" not in (slower.result.error or ""), slower.result.error
    assert slower.result.error_kind is mod.ErrorKind.TIMEOUT, slower.result
    assert slower.result.secrets == {}
    assert any("timed out" in w for w in slower.result.warnings), slower.result.warnings

    # (b) Slow but completing: what it read is kept, the rest is reported.  The old code
    # returned nothing at all here.
    partial, elapsed = _fetch_under_budget(src, mod, tmp_path, _slow_ksm(tmp_path, 0.25), 0.6, 4)
    assert elapsed < 0.6 + 0.5, f"ran {elapsed:.2f}s against a 0.6s budget"
    assert "fetch exceeded" not in (partial.result.error or ""), partial.result.error
    assert partial.result.secrets, partial.result
    assert set(partial.result.secrets.values()) == {"slow-value"}
    assert len(partial.result.secrets) < 4
    assert any("timed out" in w for w in partial.result.warnings), partial.result.warnings


def test_a_total_failure_is_an_error_with_a_hint_not_only_warnings(keeper_source, fake_ksm_bin,
                                                                  tmp_path, monkeypatch):
    """Review round 2: the classifications were unreachable through the public path.

    `fetch_keeper_secrets` collects per-reference failures as warnings, so the
    `except RuntimeError` in `fetch()` only ever saw the missing-binary message.  With no
    credential at all -- or an expired one -- the source returned `error=None`,
    `error_kind=None` and `ok=True`, and the host
    (`hermes_cli/env_loader.py:747-752`) prints the error line and the
    `source.remediation(error_kind, cfg)` hint *only* when an error is set.  So N warnings
    and no fix-it hint, and `_record_secret_source_writes` read the source as having
    stopped supplying those names rather than as having failed.
    """
    src, mod = keeper_source
    cfg = {"keeper": {"enabled": True,
                      "env": {"OPENAI_API_KEY": "XKQd9AbCdef123456789#password",
                              "OTHER_KEY": "dddd4444#password"}}}

    # No credential in the environment: the fake `ksm` refuses with the CLI's own wording.
    # Cleared from BOTH views -- a host with `get_source_environment` reads the per-fetch
    # dict, an older one reads `os.environ`, and this test has to mean the same thing there.
    monkeypatch.delenv("KSM_TOKEN", raising=False)
    monkeypatch.delenv("KSM_CONFIG", raising=False)
    denied = _profile_env()
    denied.pop("KSM_TOKEN", None)
    _reset_registry_for_tests()
    register_source(src)
    try:
        report = apply_all(cfg, tmp_path, environ=denied)
    finally:
        _reset_registry_for_tests()

    sr = report.sources[0]
    assert not sr.result.ok, "a fetch that resolved nothing must not report success"
    assert sr.result.error and sr.result.error_kind is mod.ErrorKind.NOT_CONFIGURED, sr.result
    assert sr.result.secrets == {} and sr.applied == []
    # The per-reference detail survives the error, and the hint the host prints exists.
    assert any("not been loaded" in w for w in sr.result.warnings), sr.result.warnings
    if hasattr(src, "remediation"):
        hint = src.remediation(sr.result.error_kind, {})
        assert hint and "hermes secrets keeper setup" not in hint


def test_a_partial_failure_still_only_warns(keeper_source, fake_ksm_bin, tmp_path):
    """One bad reference must never sink a good one -- and must not fail the fetch.

    This is the other half of the round-2 finding: only the ALL-failed case becomes an
    error, so a typo in one reference still leaves the rest of the environment populated.
    """
    src, mod = keeper_source
    cfg = {"keeper": {"enabled": True,
                      "env": {"OPENAI_API_KEY": "XKQd9AbCdef123456789#password",
                              "OTHER_KEY": "no-such-record-uid"}}}
    env = _profile_env()
    _reset_registry_for_tests()
    register_source(src)
    try:
        report = apply_all(cfg, tmp_path, environ=env)
    finally:
        _reset_registry_for_tests()

    sr = report.sources[0]
    assert sr.result.ok and sr.result.error_kind is None, sr.result
    assert env["OPENAI_API_KEY"] == "sk-prod-KEY-12345"
    assert "OTHER_KEY" not in env
    assert any("no-such-record-uid" in w for w in sr.result.warnings), sr.result.warnings


def test_config_schema_declares_every_knob_this_source_reads(keeper_source):
    """`timeout_seconds` was missing from the schema while the framework honours it.

    A setup UI reading `config_schema()` therefore showed no way to raise the budget the
    orchestrator applies.  Every key this module reads must be declared, so the next knob
    cannot be added silently.
    """
    _src, mod = keeper_source
    declared = set(mod.KeeperSource().config_schema())
    assert {"enabled", "env", "token_env", "binary_path", "cache_ttl_seconds",
            "override_existing", "timeout_seconds"} <= declared
    # `timeout_seconds` is read by the framework on our behalf (`fetch_timeout_seconds`).
    assert mod.KeeperSource().fetch_timeout_seconds({"timeout_seconds": "45"}) == 45.0
    assert mod.KeeperSource().fetch_timeout_seconds({"timeout_seconds": "nonsense"}) == 120.0

    source = Path(mod.__file__).read_text(encoding="utf-8")
    read_keys = set(re.findall(r'cfg\.get\(\s*"([^"]+)"', source))
    assert read_keys, "the scan found no cfg.get() calls -- adjust it, not the schema"
    assert not read_keys - declared, read_keys - declared


def test_remediation_points_at_the_cli_not_a_bundled_setup_command(keeper_source):
    """The generic hint tells users to run `hermes secrets keeper setup`.

    That command does not exist: `hermes_cli/subcommands/secrets.py` registers bitwarden
    and onepassword only.  The plugin's own hints must describe its actual knobs, and the
    `{token_env}` placeholder must render the configured name (it renders "" unless the
    class declares `token_env_key`/`default_token_env`).
    """
    src, mod = keeper_source
    if not hasattr(src, "remediation"):
        pytest.skip("this host predates SecretSource.remediation()")
    cfg = {"token_env": "MY_KEEPER_TOKEN"}
    for kind in (mod.ErrorKind.NOT_CONFIGURED, mod.ErrorKind.BINARY_MISSING,
                 mod.ErrorKind.AUTH_FAILED, mod.ErrorKind.AUTH_EXPIRED):
        hint = src.remediation(kind, cfg)
        assert hint, kind
        assert "hermes secrets keeper setup" not in hint
    assert "MY_KEEPER_TOKEN" in src.remediation(mod.ErrorKind.AUTH_FAILED, cfg)
    assert "keeper-secrets-manager-cli" in src.remediation(mod.ErrorKind.BINARY_MISSING, cfg)
    assert "secrets.keeper.env" in src.remediation(mod.ErrorKind.NOT_CONFIGURED, cfg)

    # The declared token-env machinery agrees with the hint placeholder.
    if hasattr(src, "token_env"):
        assert src.token_env(cfg) == "MY_KEEPER_TOKEN"
        assert src.token_env({}) == "KSM_TOKEN"
