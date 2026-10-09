"""Keeper Secrets Manager (KSM) secret source for Hermes Agent.

Resolve provider credentials from a Keeper vault at process startup so they
don't have to live in plaintext in ``~/.hermes/.env``.  Works exactly like the
built-in Bitwarden and 1Password sources: it subclasses
``agent.secret_sources.base.SecretSource``, and the orchestrator owns
precedence, conflict handling, provenance, and the ``os.environ`` writes.

This is a *mapped* source: the user explicitly binds each env-var name to a
Keeper record reference under ``secrets.keeper.env``::

    secrets:
      sources: [keeper, bitwarden]   # run Keeper alongside Bitwarden
      keeper:
        enabled: true
        token_env: KSM_TOKEN          # bootstrap secret (one-time access token)
        env:
          OPENAI_API_KEY:    "ksm://XKQd9AbC...123#password"
          ANTHROPIC_API_KEY: "ksm://title:My Login Record#password"
          OPENAI_ORG_ID:     "ksm://XKQd9AbC...123#login"

Reference grammar::

    [ksm://]<record-uid>[#<field>]
    [ksm://]title:<record-title>[#<field>]

* ``record-uid`` — a Keeper record UID, passed positionally to ``ksm secret get``.
* ``title:<record-title>`` — resolve the record by title.  This is opt-in because
  ``ksm secret get`` treats a positional argument as a UID (``-u/--uid``) and sends
  it to Keeper as a server-side record filter, so a title passed positionally can
  never match (``Cannot find requested record(s).``).  Titles are matched by
  ``-t/--title``, which the ``title:`` form selects.  A UID never contains ``:``, so
  the prefix cannot be mistaken for one.
* ``field``      — optional field label/type to extract; defaults to
  ``password`` (the common case for API keys).  Use the record's field
  label or field type (e.g. ``login``, ``password``, ``url``, a custom
  field label).

Auth is whatever the user's ``ksm`` CLI already uses — a one-time access token
in ``KSM_TOKEN`` (headless / container), a base64 config in ``KSM_CONFIG``,
or an already-initialized profile (``keeper.ini`` / OS keyring).  Hermes
never authenticates on the user's behalf; it shells out to an already-trusted,
already-authenticated CLI.

Failures NEVER block startup.  A missing ``ksm`` binary, expired token, bad
reference, or permission error surfaces a one-line warning and Hermes
continues with whatever credentials ``.env`` already had.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

# Import from the defining modules, not from the compat shims: FetchResult and
# is_valid_env_name moved from `_cache` to `base`, and the old paths emit a
# HermesPluginCompatWarning and are removed after 2026-09-14.
from agent.secret_sources._cache import CachedFetch, DiskCache
from agent.secret_sources.base import (
    ErrorKind,
    FetchResult,
    SecretSource,
    is_valid_env_name,
    run_secret_cli,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# How long to wait for a single `ksm secret get`, in seconds.
_KSM_RUN_TIMEOUT = 30

# Default env var carrying the one-time access token (KSM's own bootstrap
# secret).  Users can point `token_env` at a different name; we always export
# the value to the child under its KSM-named equivalent (KSM_TOKEN) because
# that is what `ksm` actually reads.
_DEFAULT_TOKEN_ENV = "KSM_TOKEN"

# KSM_* env vars the child is allowed to inherit.  These never include any
# other provider credential — just Keeper's own auth/material vars.
_KSM_ENV_PREFIX = "KSM_"

# Env names the ksm CLI resolves its *credentials, endpoint and credential
# storage* from.  The CLI's precedence is
# ``KSM_CONFIG`` -> ``KSM_CONFIG_BASE64_1`` -> ``KSM_TOKEN``
# (keeper_secrets_manager_cli/profile.py:52-72), so a secret bound to either of
# the first two outranks the token Hermes authenticated with: protecting only
# the token env would leave the higher-precedence names open.  Enumerated from
# the CLI (keeper-secrets-manager-cli 1.5.0 / core 17.3.0) rather than guessed:
# ``grep -rhoE 'KSM_[A-Z0-9_]+' keeper_secrets_manager*/ | sort -u``, plus
# profile.py:281-299 for the indexed families below.
_PROTECTED_KSM_NAMES = frozenset({
    "KSM_CONFIG",          # base64 config blob — highest precedence
    "KSM_CONFIG_FILE",     # config file to read instead
    "KSM_TOKEN",           # the bootstrap token we authenticate with
    "KSM_CLI_TOKEN",       # the CLI's own stored-token name
    "KSM_CLI_PROFILE",     # which profile is active
    "KSM_HOSTNAME",        # Keeper server/region used with the token
    "KSM_INI_DIR",         # where credentials are read from / written to
    "KSM_INI_FILE",
    "KSM_CACHE_DIR",
    "KSM_SKIP_VERIFY",     # TLS verification off
    "KSM_SKIP_PREFLIGHT",
    "KSM_CONFIG_SKIP_MODE",
    "KSM_CONFIG_SKIP_MODE_WARNING",
    "KSM_INI_DIR_SKIP_CONFLICT_WARNING",
})

# ``KSM_CONFIG_BASE64_<n>`` holds profile <n>'s credential material and
# ``KSM_CONFIG_BASE64_DESC_<n>`` its name (<n>=1 also picks the *active* profile).
# Profile._auto_config_from_env_var() walks the index until the first gap, so the
# family has no fixed size — but a numbered slot only takes effect when every
# lower index is already present, and index 1 is protected above, so the slots a
# hand-configured profile list realistically reaches are covered here.
_KSM_PROFILE_SLOTS = 10


def _protected_ksm_names() -> frozenset:
    """Every KSM-owned env name a resolved secret must never be allowed to occupy."""
    names = set(_PROTECTED_KSM_NAMES)
    for index in range(1, _KSM_PROFILE_SLOTS + 1):
        names.add(f"KSM_CONFIG_BASE64_{index}")
        names.add(f"KSM_CONFIG_BASE64_DESC_{index}")
    return frozenset(names)


def _ksm_owned_name(name: str) -> bool:
    """True when *name* is inside the KSM namespace, which configures the CLI itself.

    ``protected_env_vars`` can only name the vars that exist today and the indexed
    slots above; the namespace itself is what the plugin refuses to write into.
    """
    return name.startswith(_KSM_ENV_PREFIX)


# Disk-persisted cache so back-to-back short-lived `hermes` invocations
# (cron, gateway forks, per-message subagents) don't re-shell `ksm` for every
# reference.  Holds only resolved secret *values*; auth material is fingerprinted.
_DISK_CACHE_BASENAME = "ksm_cache.json"

# The field arrays `ksm secret get --json` can put a field in: standard fields
# under "fields", custom fields under "custom".  "custom_fields" was the pre-KSM-820
# spelling and "customFields" is the input/record-create spelling; both are accepted
# so a record shape we fail to scan can never silently drop a variable again.
_FIELD_ARRAYS = ("fields", "custom", "customFields", "custom_fields")

# Reference prefix that selects a title lookup instead of a UID lookup.  See
# _split_ref_kind for why a title needs an explicit opt-in.
_TITLE_PREFIX = "title:"


# Cache key: (auth fingerprint, refs fingerprint, home).  The home is in the IN-PROCESS
# key only -- a HERMES_HOME switch inside one long-lived process (the gateway) must not
# return another profile's secrets -- while the on-disk cache is already stored per home,
# so it stays out of the disk key and old cache files keep working.
_CacheKey = Tuple[str, str, str]


def _disk_key_str(cache_key: _CacheKey) -> str:
    auth_fp, refs_fp, _home = cache_key
    return f"{auth_fp}|{refs_fp}"


_DISK_CACHE: DiskCache = DiskCache(_DISK_CACHE_BASENAME, key_serializer=_disk_key_str)


# In-process cache (per process, keyed by auth + refs + home).
_CACHE: Dict[_CacheKey, CachedFetch] = {}


# ---------------------------------------------------------------------------
# Reference parsing + field extraction
# ---------------------------------------------------------------------------


def _parse_ref(reference: str) -> Tuple[str, Optional[str]]:
    """Split ``[ksm://]<record-ref>[#<field>]`` into (record_ref, field).

    ``field`` is ``None`` when no ``#`` is present (caller defaults to
    ``password``).  The optional ``ksm://`` scheme prefix is stripped; a
    ``title:`` prefix stays part of ``record_ref`` and is interpreted by
    :func:`_split_ref_kind`.
    """
    ref = reference.strip()
    if ref.startswith("ksm://"):
        ref = ref[len("ksm://"):]
    # Split on the LAST '#' so a title containing '#' keeps everything but
    # the trailing field token.  Titles with a trailing '#field' are uncommon.
    if "#" in ref:
        record_ref, _, field = ref.rpartition("#")
        record_ref = record_ref.strip()
        field = field.strip() or None
    else:
        record_ref, field = ref, None
    return record_ref, field


def _split_ref_kind(record_ref: str) -> Tuple[str, str]:
    """``("title"|"uid", value)`` for a parsed record reference.

    ``ksm secret get`` resolves a positional argument as a record UID and sends it to
    Keeper as a server-side filter, so titles must go through ``-t/--title``.  The
    ``title:`` prefix selects that (case-insensitively); everything else is a UID.
    A Keeper UID is URL-safe base64 and never contains ``:``, so the prefix is
    unambiguous.
    """
    if record_ref.lower().startswith(_TITLE_PREFIX):
        return "title", record_ref[len(_TITLE_PREFIX):].strip()
    return "uid", record_ref


def _scalar(value) -> Optional[str]:
    """Coerce a KSM field value to a single string, or None if unusable."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        # e.g. fileRef arrays — join if short, else JSON-encode.
        if len(value) == 1:
            return _scalar(value[0])
        try:
            import json
            return json.dumps(value)
        except Exception:
            return None
    return str(value)


def _label_of(raw) -> str:
    """Normalised field ``label``/``type`` text for matching, or "" when unusable.

    Keeper field metadata is normally a string, but an unexpected record shape must
    not raise out of ``_field_value``: the caller only handles ``RuntimeError``, so an
    ``AttributeError`` here would sink every variable in the fetch.
    """
    return raw.strip().lower() if isinstance(raw, str) else ""


def _field_value(record: dict, field: Optional[str]) -> Optional[str]:
    """Extract a field value from a parsed `ksm secret get --json` record."""
    field = (field or "password").lower()

    # 1. Well-known top-level scalar fields.
    known = ("password", "login", "username", "url", "notes", "title", "name")
    if field in known and field in record:
        val = _scalar(record.get(field))
        if val is not None:
            return val

    # 2. Scan the structured field arrays (standard fields + custom fields).
    for arr_name in _FIELD_ARRAYS:
        for item in record.get(arr_name) or []:
            if not isinstance(item, dict):
                continue
            label = _label_of(item.get("label"))
            typ = _label_of(item.get("type"))
            if field in (label, typ):
                val = _scalar(item.get("value"))
                if val is not None:
                    return val

    # 3. Fallback: username is often a label on a login-type field.
    if field in ("login", "username"):
        for arr_name in _FIELD_ARRAYS:
            for item in record.get(arr_name) or []:
                if isinstance(item, dict) and _label_of(item.get("type")) == "login":
                    val = _scalar(item.get("value"))
                    if val is not None:
                        return val
    return None


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def find_ksm(binary_path: str = "") -> Optional[Path]:
    """Resolve a usable ``ksm`` binary, or None.

    When ``binary_path`` is set it is used verbatim and PATH is NOT consulted
    (pinning avoids trusting whatever ``ksm`` shows up first on PATH).  A
    pinned-but-missing path returns None (caller surfaces a clear error).
    """
    if binary_path:
        pinned = Path(binary_path)
        if pinned.exists() and os.access(pinned, os.X_OK):
            return pinned
        return None
    import shutil
    found = shutil.which("ksm")
    return Path(found) if found else None


# ---------------------------------------------------------------------------
# Per-fetch environment + allowlisted child environment
# ---------------------------------------------------------------------------

# Hermes exposes a per-fetch environment view to sources (set_source_environment /
# get_source_environment in agent.secret_sources.base).  Builds without it are still
# supported -- the plugin's own README allows any Hermes with the SecretSource API --
# so fall back to the process environment rather than requiring the hook.
try:  # pragma: no cover - the fallback is exercised by the pinned-compat test run
    from agent.secret_sources.base import get_source_environment as _host_env_view
except ImportError:  # pragma: no cover - older host
    _host_env_view = None  # type: ignore[assignment]


def _source_environment() -> Mapping[str, str]:
    """The host's per-fetch environment view, or ``os.environ`` when it has none.

    The orchestrator installs a per-profile view around ``fetch()``, so ``os.environ``
    belongs to whichever profile owns the process.  Under a routed profile (the gateway
    serves several) reading auth from it would both miss a token that lives only in the
    profile's environment and hand the child a *sibling* profile's ``KSM_CONFIG``, which
    outranks the correct token in the CLI's own precedence order.
    """
    if _host_env_view is not None:
        return _host_env_view()
    return os.environ


def _bootstrap_token(token_env: str, view: Mapping[str, str]) -> str:
    """Current value of the bootstrap token variable in ``view``, or "".

    Read from the per-fetch environment view (see :func:`_source_environment`) and handed
    to the child by value: the child is started with an allowlist of *names*, and the
    host resolves those names from its own environment, so a token under a non-``KSM_*``
    name — the documented ``token_env`` override — would otherwise never reach ``ksm``.
    ``view`` is required so no call path can quietly fall back to the process environment.
    """
    return (view.get(token_env or _DEFAULT_TOKEN_ENV) or "").strip()


def _ksm_child_env(view: Mapping[str, str], token_env: str,
                   token_value: str) -> Dict[str, str]:
    """Environment the ``ksm`` child receives, built from the per-fetch env view.

    Only Keeper's own auth/material vars (anything starting with ``KSM_``) plus the
    bootstrap token — never a copy of the host environment, which by now holds every
    credential Hermes knows about.  ``run_secret_cli`` still adds PATH/HOME/locale and
    set NO_COLOR.

    Values are passed **by value** rather than as an allowlisted name list.
    ``run_secret_cli`` resolves allowlisted names from the host's own environment, so
    under a routed profile the child would inherit that environment instead of the
    profile's — and a sibling profile's ``KSM_CONFIG`` outranks the correct
    ``KSM_TOKEN`` in the CLI's own precedence order (``KSM_CONFIG`` →
    ``KSM_CONFIG_BASE64_1`` → ``KSM_TOKEN``).  ``ksm`` reads the token from
    ``KSM_TOKEN``; the configured name is added as an alias when it differs.
    """
    env = {key: value for key, value in view.items() if key.startswith(_KSM_ENV_PREFIX)}
    if token_value:
        env[_DEFAULT_TOKEN_ENV] = token_value
        if token_env and token_env != _DEFAULT_TOKEN_ENV:
            env[token_env] = token_value
    return env


# ---------------------------------------------------------------------------
# Fetch one record
# ---------------------------------------------------------------------------


def _run_ksm_get(ksm: Path, record_ref: str, *, view: Mapping[str, str],
                 token_env: str = _DEFAULT_TOKEN_ENV, token_value: str = "") -> dict:
    """Resolve one record reference to its parsed JSON object.

    Raises RuntimeError on any failure (missing binary handled by caller,
    auth/network/parse errors here).  Returns the single record dict.
    """
    # Do NOT pass --no-color here: the ksm CLI declares --color/--no-color on the
    # ROOT group only, so `ksm secret get --no-color ...` makes click reject the
    # whole command line ("Error: No such option '--no-color'.", exit 2) before any
    # vault call.  Colour is already off -- run_secret_cli sets NO_COLOR=1 and the
    # child has no TTY to colourize for.
    #
    # `--title=<title>` (not `--title <title>`) so a title that starts with '-' is
    # still the option's value and can never be read as another flag.
    kind, value = _split_ref_kind(record_ref)
    cmd = ([str(ksm), "secret", "get", "--json", f"--title={value}"] if kind == "title"
           else [str(ksm), "secret", "get", "--json", "--", value])
    try:
        proc = run_secret_cli(
            cmd,
            extra_env=_ksm_child_env(view, token_env, token_value),
            timeout=_KSM_RUN_TIMEOUT,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"ksm invocation failed for {record_ref!r}: {exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        hint = ""
        # A positional reference is a UID filter, so the commonest way to get here is
        # a title used without the `title:` prefix.  Say so instead of leaving the
        # user with the CLI's bare "Cannot find requested record(s).".
        if kind == "uid" and "cannot find requested record" in err.lower():
            hint = (f"  ({value!r} was looked up as a record UID; to resolve it by "
                    f"title use ksm://{_TITLE_PREFIX}<title>)")
        raise RuntimeError(f"ksm failed for {record_ref!r}: {err[:200]}{hint}")

    import json
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError(f"ksm returned no output for {record_ref!r}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ksm returned non-JSON for {record_ref!r}: {exc}") from exc

    # `ksm secret get` returns a list of records for multiple matches and a bare
    # object for exactly one (unless --force-array is passed), so accept both.
    records = payload if isinstance(payload, list) else [payload]
    if not records:
        raise RuntimeError(f"ksm found no record for {record_ref!r}")
    if len(records) > 1:
        # Only a title can match several records (a UID is unique).  Picking the first
        # would silently bind a variable to whichever record sorted first, so refuse
        # and name the candidates.
        titles = ", ".join(repr((r.get("title") if isinstance(r, dict) else None) or "?")
                           for r in records[:5])
        remedy = ("use a record UID to select exactly one" if kind == "title"
                  else "a UID is unique, so re-check the reference")
        raise RuntimeError(f"ksm matched {len(records)} records for {record_ref!r} "
                           f"({titles}); {remedy}")
    record = records[0]
    if not isinstance(record, dict):
        raise RuntimeError(f"ksm returned unexpected record shape for {record_ref!r}")
    return record


# ---------------------------------------------------------------------------
# Resolve a full ENV_VAR -> reference map
# ---------------------------------------------------------------------------


def fetch_keeper_secrets(
    *,
    references: Dict[str, str],
    token_env: str = _DEFAULT_TOKEN_ENV,
    binary: Optional[Path] = None,
    binary_path: str = "",
    use_cache: bool = True,
    cache_ttl_seconds: float = 300,
    home_path: Optional[Path] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Resolve ``references`` (ENV_VAR -> ksm ref) to ``(secrets, warnings)``.

    Distinct record references are fetched once each (cached), then each
    requested field is extracted.  A per-reference failure is collected as a
    warning and that var is dropped — one bad entry never sinks the rest.
    """
    # The environment this fetch runs against: the host's per-profile view when it has
    # one, else the process environment.  Read once, so the token, the child env and the
    # cache key can never disagree about which profile they belong to.
    view = _source_environment()

    # (record_ref) -> list of (env_var, field)
    by_record: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    warnings: List[str] = []
    for name, ref in references.items():
        if not is_valid_env_name(name):
            warnings.append(f"Skipping {name!r}: not a valid env-var name")
            continue
        if _ksm_owned_name(name):
            warnings.append(
                f"Skipping {name!r}: the {_KSM_ENV_PREFIX}* namespace configures the ksm CLI "
                f"itself (credential, profile, server), so a Keeper record is never written "
                f"there — bind the value to a different env-var name"
            )
            continue
        if name == token_env:
            warnings.append(
                f"Skipping {name!r}: that is the bootstrap-token variable this source "
                f"authenticates with (secrets.keeper.token_env) — a vault value there would "
                f"replace the credential used to reach the vault"
            )
            continue
        if not isinstance(ref, str) or not ref.strip():
            warnings.append(f"Skipping {name!r}: reference is not a string")
            continue
        record_ref, field = _parse_ref(ref)
        if not record_ref:
            warnings.append(f"Skipping {name!r}: empty record reference")
            continue
        kind, value = _split_ref_kind(record_ref)
        if kind == "title" and not value:
            warnings.append(
                f"Skipping {name!r}: {ref!r} has an empty record title "
                f"(write ksm://{_TITLE_PREFIX}<title>)"
            )
            continue
        by_record.setdefault(record_ref, []).append((name, field))

    if not by_record:
        return {}, warnings

    # home_path is part of the in-process key: the gateway is one long-lived process
    # serving several profiles, so without it a profile switch could be served the
    # previous profile's values (the same reason the 1Password source folds it in).
    # The on-disk cache keeps its own per-home file, so it stays out of the disk key.
    auth_fp, refs_fp = _auth_refs_fingerprint(token_env, view, references)
    cache_key: _CacheKey = (auth_fp, refs_fp,
                            str(home_path) if home_path is not None else "")
    if use_cache:
        cached = _CACHE.get(cache_key)
        if cached and cached.is_fresh(cache_ttl_seconds):
            secrets, miss = _select_from_cache(cached.secrets, by_record)
            warnings.extend(miss)
            return secrets, warnings
        disk_cached = _DISK_CACHE.read(cache_key, cache_ttl_seconds, home_path)
        if disk_cached is not None:
            _CACHE[cache_key] = disk_cached
            secrets, miss = _select_from_cache(disk_cached.secrets, by_record)
            warnings.extend(miss)
            return secrets, warnings

    ksm = binary or find_ksm(binary_path)
    if ksm is None:
        raise RuntimeError(
            "ksm CLI not found.  Install the Keeper Secrets Manager CLI "
            "(pip3 install keeper-secrets-manager-cli, or download the binary "
            "from https://github.com/Keeper-Security/secrets-manager/releases) "
            "or set secrets.keeper.binary_path to its absolute location."
        )

    # Cache of raw record JSON per record_ref for this fetch pass.  The bootstrap token
    # is read once and handed to every child explicitly (see _ksm_child_env).
    token_value = _bootstrap_token(token_env, view)
    raw_records: Dict[str, dict] = {}
    fetch_errors: Dict[str, str] = {}
    for record_ref in by_record:
        try:
            raw_records[record_ref] = _run_ksm_get(
                ksm, record_ref, view=view, token_env=token_env, token_value=token_value
            )
        except RuntimeError as exc:
            fetch_errors[record_ref] = str(exc)

    # Build the {ENV_VAR: value} map, only from successfully fetched records.
    secrets: Dict[str, str] = {}
    for record_ref, bindings in by_record.items():
        record = raw_records.get(record_ref)
        if record is None:
            warnings.append(fetch_errors.get(record_ref, f"Failed to fetch {record_ref!r}"))
            continue
        for name, field in bindings:
            value = _field_value(record, field)
            if value is None or not value.strip():
                warnings.append(
                    f"Keeper record {record_ref!r} has no value for field "
                    f"{field or 'password'} (requested by {name!r})"
                )
                continue
            secrets[name] = value

    if use_cache and secrets and not fetch_errors:
        entry = CachedFetch(secrets=dict(secrets), fetched_at=time.time())
        _CACHE[cache_key] = entry
        _DISK_CACHE.write(cache_key, entry, cache_ttl_seconds, home_path)

    return secrets, warnings


def _auth_refs_fingerprint(token_env: str, view: Mapping[str, str],
                           references: Dict[str, str]) -> Tuple[str, str]:
    """(auth_fp, refs_fp) — fingerprint auth material + the refs map.

    Auth material is fingerprinted from the per-fetch view, so a profile switch that
    changes the token or ``KSM_CONFIG`` can never hit another profile's cache entry.
    """
    auth_parts = [f"token={_bootstrap_token(token_env, view)}"]
    for key in sorted(view):
        if key.startswith(_KSM_ENV_PREFIX):
            auth_parts.append(f"{key}={view[key]}")
    auth_fp = hashlib.sha256("\n".join(auth_parts).encode("utf-8")).hexdigest()[:16]
    refs_fp = hashlib.sha256(
        "\n".join(f"{n}={references[n]}" for n in sorted(references)).encode("utf-8")
    ).hexdigest()[:16]
    return (auth_fp, refs_fp)


def _select_from_cache(
    cached_secrets: Dict[str, str],
    by_record: Dict[str, List[Tuple[str, Optional[str]]]],
) -> Tuple[Dict[str, str], List[str]]:
    """Re-extract requested fields from a cached full secret map.

    The cached map is keyed by ENV_VAR (post-extraction), so we can return it
    directly for any var the user still requests.  Vars dropped from config
    since caching are simply not returned.
    """
    secrets: Dict[str, str] = {}
    warnings: List[str] = []
    requested = {name for bindings in by_record.values() for name, _ in bindings}
    for name in requested:
        if name in cached_secrets:
            secrets[name] = cached_secrets[name]
    return secrets, warnings


# ---------------------------------------------------------------------------
# SecretSource adapter — the registry-facing wrapper
# ---------------------------------------------------------------------------


class KeeperSource(SecretSource):
    """Keeper Secrets Manager as a registered secret source.

    Thin adapter over the module's fetch machinery.  ``fetch()`` only
    *fetches* — precedence, override semantics, conflict warnings, and the
    ``os.environ`` writes are the orchestrator's job (see
    ``agent.secret_sources.registry.apply_all``).

    Keeper is a **mapped** source: the user explicitly binds each env var to a
    ``ksm://`` reference under ``secrets.keeper.env``, so its claims outrank
    bulk sources on contested vars.
    """

    name = "keeper"
    label = "Keeper Secrets Manager"
    shape = "mapped"
    scheme = "ksm"

    def override_existing(self, cfg: dict) -> bool:
        # Default True: an explicit VAR->ksm:// binding is the strongest user
        # intent there is — leaving a stale .env line in place should not
        # silently defeat it (same rotation rationale as the bundled sources).
        return bool(isinstance(cfg, dict) and cfg.get("override_existing", True))

    def protected_env_vars(self, cfg: dict):
        token_env = _DEFAULT_TOKEN_ENV
        if isinstance(cfg, dict):
            token_env = str(cfg.get("token_env") or token_env)
        # Never let a resolved secret clobber the bootstrap token used to reach the
        # vault — nor any of the CLI's other auth/material vars, because KSM_CONFIG and
        # KSM_CONFIG_BASE64_1 are resolved *before* KSM_TOKEN: a secret bound to those
        # would not merely collide with the credential, it would replace it (or point
        # the CLI at another profile, host, file, or a TLS-unverified session).
        return _protected_ksm_names() | {token_env}

    def config_schema(self) -> dict:
        return {
            "enabled": {"description": "Master switch", "default": False},
            "env": {
                "description": "Map of ENV_VAR -> ksm://record-ref[#field] reference",
                "default": {},
            },
            "token_env": {
                "description": "Env var holding the KSM one-time access token "
                               "(unset = KSM_CONFIG / initialized profile)",
                "default": _DEFAULT_TOKEN_ENV,
            },
            "binary_path": {
                "description": "Pin the ksm binary (empty = resolve via PATH)",
                "default": "",
            },
            "cache_ttl_seconds": {
                "description": "Disk+memory cache TTL; 0 disables",
                "default": 300,
            },
            "override_existing": {
                "description": "Resolved values overwrite .env/shell values",
                "default": True,
            },
        }

    def fetch(self, cfg: dict, home_path: Path) -> FetchResult:
        cfg = cfg if isinstance(cfg, dict) else {}
        result = FetchResult()

        env_map = cfg.get("env")
        valid: Dict[str, str] = {}
        if isinstance(env_map, dict):
            for name, ref in env_map.items():
                if not is_valid_env_name(name):
                    result.warnings.append(f"Skipping {name!r}: not a valid env-var name")
                    continue
                if not isinstance(ref, str) or not ref.strip():
                    result.warnings.append(f"Skipping {name!r}: reference is not a string")
                    continue
                valid[name] = ref.strip()

        if not valid:
            if not result.warnings:
                result.error = (
                    "secrets.keeper.enabled is true but the env: map is empty. "
                    "Add ENV_VAR: ksm://record-ref[#field] entries."
                )
                result.error_kind = ErrorKind.NOT_CONFIGURED
            return result

        binary_path = str(cfg.get("binary_path") or "")
        binary = find_ksm(binary_path)
        result.binary_path = binary
        if binary is None:
            if binary_path:
                result.error = (
                    f"secrets.keeper.binary_path ({binary_path!r}) is not an "
                    "executable ksm binary."
                )
            else:
                result.error = (
                    "secrets.keeper.enabled is true but the ksm CLI was not "
                    "found on PATH.  Install it (pip3 install "
                    "keeper-secrets-manager-cli) or set secrets.keeper.binary_path."
                )
            result.error_kind = ErrorKind.BINARY_MISSING
            return result

        try:
            ttl = float(cfg.get("cache_ttl_seconds", 300))
        except (TypeError, ValueError):
            ttl = 300.0

        try:
            secrets, fetch_warnings = fetch_keeper_secrets(
                references=valid,
                token_env=str(cfg.get("token_env") or _DEFAULT_TOKEN_ENV),
                binary=binary,
                binary_path=binary_path,
                cache_ttl_seconds=ttl,
                home_path=home_path,
            )
        except RuntimeError as exc:
            result.error = str(exc)
            result.error_kind = _classify_ksm_error(str(exc))
            return result

        result.secrets = secrets
        result.warnings.extend(fetch_warnings)
        return result


def _classify_ksm_error(message: str) -> ErrorKind:
    """Best-effort mapping of ksm failure text onto the shared taxonomy."""
    lowered = message.lower()
    if "timed out" in lowered:
        return ErrorKind.TIMEOUT
    if "not found on path" in lowered or "not an executable" in lowered \
            or "invocation failed" in lowered:
        return ErrorKind.BINARY_MISSING
    if any(tok in lowered for tok in ("unauthorized", "not signed in",
                                      "session expired", "authentication",
                                      "401", "403", "invalid token",
                                      "access token")):
        return ErrorKind.AUTH_FAILED
    if "no value" in lowered or "no record" in lowered:
        return ErrorKind.EMPTY_VALUE
    if any(tok in lowered for tok in ("network", "connection", "resolve host",
                                      "dns", "timeout")):
        return ErrorKind.NETWORK
    return ErrorKind.INTERNAL


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register the Keeper secret source with Hermes."""
    ctx.register_secret_source(KeeperSource())
