"""Load the plugin the way Hermes does, instead of calling the test helpers.

The rest of this suite imports ``__init__.py`` and calls ``register_source()``
directly.  That proves the source works, but not that *Hermes* finds the plugin,
honours ``plugins.enabled``, runs ``register()`` and applies the result — which is
what decides whether the documented install does anything at all.

The pattern follows Hermes' own plugin tests
(``tests/hermes_cli/test_secret_source_bootstrap.py``): a private ``HERMES_HOME``
holding ``plugins/<name>/``, a ``config.yaml`` that enables the plugin, then a real
``PluginManager().discover_and_load()``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

try:
    from agent.secret_sources import registry as reg
    from hermes_cli.plugins import PluginManager
except Exception as exc:  # pragma: no cover - depends on the Hermes checkout in use
    pytest.skip(f"Hermes' plugin loader is not importable ({exc})",
                allow_module_level=True)

try:
    from hermes_cli.env_loader import reset_secret_source_cache
except ImportError:  # pragma: no cover - older host
    reset_secret_source_cache = None

PLUGIN_NAME = "keeper-vault"
PROBE_VAR = "KEEPER_DISCOVERY_PROBE"
PROBE_RECORD = "XKQd9AbCdef123456789"


def _write_config(home: Path, *, enabled: bool) -> None:
    """``config.yaml`` as the README's install steps leave it."""
    enabled_list = f"[{PLUGIN_NAME}]" if enabled else "[]"
    (home / "config.yaml").write_text(
        "plugins:\n"
        f"  enabled: {enabled_list}\n"
        "secrets:\n"
        "  sources: [keeper]\n"          # as the README's config example spells it
        "  keeper:\n"
        "    enabled: true\n"
        "    env:\n"
        f'      {PROBE_VAR}: "ksm://{PROBE_RECORD}#password"\n',
        encoding="utf-8",
    )


@pytest.fixture
def plugin_home(tmp_path: Path, monkeypatch) -> Path:
    """A Hermes home holding the plugin, created the way the README says to."""
    home = tmp_path / "hermes_home"
    plugin_dir = home / "plugins" / PLUGIN_NAME
    plugin_dir.mkdir(parents=True)
    shutil.copy(REPO / "__init__.py", plugin_dir / "__init__.py")
    shutil.copy(REPO / "plugin.yaml", plugin_dir / "plugin.yaml")
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Keep discovery to this home: no project plugins, and the host's own bundled
    # plugins must not decide the outcome.
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(tmp_path / "no-bundled-plugins"))
    (tmp_path / "no-bundled-plugins").mkdir()
    return home


def _reset() -> None:
    reg._reset_registry_for_tests()
    if reset_secret_source_cache is not None:
        reset_secret_source_cache()


def test_an_enabled_directory_plugin_registers_the_keeper_source(plugin_home):
    """The two-copied-files install works: the plugin loads and the source registers."""
    _write_config(plugin_home, enabled=True)
    _reset()
    try:
        manager = PluginManager()
        manager.discover_and_load()

        loaded = manager._plugins.get(PLUGIN_NAME)
        assert loaded is not None, "Hermes did not discover plugins/keeper-vault"
        assert loaded.enabled, f"plugin not enabled: {loaded.error}"
        assert "keeper" in [s.name for s in reg.list_sources()]
        if hasattr(reg, "list_plugin_sources"):  # current main only
            assert "keeper" in [s.name for s in reg.list_plugin_sources()]
    finally:
        _reset()


def test_a_disabled_plugin_registers_nothing_and_says_how_to_enable(plugin_home):
    """``plugins.enabled`` is an opt-in allow-list — the README has to say so.

    Copying the files without enabling leaves the plugin loaded but inert, and
    ``secrets.sources: [keeper]`` then names an unknown source: no secrets load.
    """
    _write_config(plugin_home, enabled=False)
    _reset()
    try:
        manager = PluginManager()
        manager.discover_and_load()

        loaded = manager._plugins[PLUGIN_NAME]
        assert loaded.enabled is False
        assert "hermes plugins enable keeper-vault" in (loaded.error or "")
        assert "keeper" not in [s.name for s in reg.list_sources()]
    finally:
        _reset()


def test_the_documented_install_resolves_a_secret_end_to_end(plugin_home, fake_ksm_bin,
                                                             monkeypatch):
    """Enable the plugin and a mapped reference lands in the environment.

    This is the whole install path in one assertion: discovery -> ``register()``
    -> the bootstrap pass resolving ``secrets.keeper.env`` through the real
    ``KeeperSource.fetch`` (with the fake ``ksm`` on PATH).
    """
    _write_config(plugin_home, enabled=True)
    monkeypatch.delenv(PROBE_VAR, raising=False)
    _reset()
    try:
        PluginManager().discover_and_load()
        if hasattr(PluginManager, "_refresh_secret_sources_after_discovery"):
            # Current main re-pulls plugin sources as discovery finishes, so the value
            # is in the environment of the process that loaded the plugin.
            assert os.environ.get(PROBE_VAR) == "sk-prod-KEY-12345", (
                "discovery did not re-apply the plugin's secret source")
        else:  # pragma: no cover - hosts without the post-discovery re-pull
            # Plugin discovery runs after the first `.env` load, so on these builds the
            # value lands on the NEXT env load (the documented first-process gap).
            try:
                from hermes_cli.env_loader import load_hermes_dotenv
            except ImportError as exc:
                pytest.skip(f"host env loader is not importable ({exc})")
            # Reset only the once-per-home latch: the registry keeps the source that
            # discovery just registered.
            if reset_secret_source_cache is not None:
                reset_secret_source_cache()
            load_hermes_dotenv(hermes_home=str(plugin_home))
        assert os.environ.get(PROBE_VAR) == "sk-prod-KEY-12345"
    finally:
        os.environ.pop(PROBE_VAR, None)
        _reset()
