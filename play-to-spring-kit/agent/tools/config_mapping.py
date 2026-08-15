"""Play HOCON config-key mapping: seed-table diff against Spring properties.

``scripts/conf_to_application_properties.py`` (invoked by
``agent/tools/setup_ops.py``'s ``run_export_play_conf`` during the ``setup``
graph node) already flattens ``conf/application.conf`` verbatim into
``<spring_repo>/src/main/resources/application.properties`` as dot-keys (e.g.
``mongodb.uri=...``) -- it does not rename keys to Spring's own idiomatic
property names (e.g. ``spring.data.mongodb.uri``). This module recognizes a
small set of well-known Play/generic keys (``SEED_KEY_MAP``) and computes what
still needs LLM attention (``diff_config_keys``'s ``leftover``), feeding the
M4 config-mapping agent (agent/agents/config_mapping.py).

Deterministic, pure where possible -- no subprocess, no LLM. Reimplements just
enough of ``conf_to_application_properties.py``'s object-flattening for the
scalar/nested-object case (list/array values are skipped: that's already
fully solved by the export script and out of scope here); importing that
script's own ``flatten`` would require importing the whole module, which
``sys.exit(2)``s at import time if pyhocon is missing -- not something a
library importer can risk, so pyhocon itself is guarded here instead and
degrades to an empty result, matching ``run_export_play_conf``'s "non-fatal
by design" philosophy.
"""

from __future__ import annotations

from pathlib import Path

try:
    from pyhocon import ConfigFactory
except ImportError:  # pragma: no cover - environment-dependent
    ConfigFactory = None

# Seed table: Play/generic HOCON key -> Spring canonical key. Deliberately
# small (a seed, not exhaustive) -- everything else falls through to the
# config-mapping LLM agent as "leftover".
SEED_KEY_MAP: dict[str, str] = {
    "mongodb.uri": "spring.data.mongodb.uri",
    "mongo.uri": "spring.data.mongodb.uri",
    "neo4j.uri": "spring.neo4j.uri",
    "neo4j.username": "spring.neo4j.authentication.username",
    "neo4j.password": "spring.neo4j.authentication.password",
    "redis.host": "spring.data.redis.host",
    "redis.port": "spring.data.redis.port",
    "kafka.bootstrap.servers": "spring.kafka.bootstrap-servers",
}


def _to_str(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _flatten(obj, prefix: str, out: dict[str, str]) -> None:
    if obj is None:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                _flatten(v, key, out)
            elif isinstance(v, list):
                continue  # array handling out of scope (export script handles it)
            elif v is None:
                continue
            else:
                out[key] = _to_str(v)
        return
    if prefix:
        out[prefix] = _to_str(obj)


def flatten_play_conf(conf_path: Path) -> dict[str, str]:
    """Flatten ``conf/application.conf`` into flat dot-keys -> string values.

    Scalar and nested-object keys only. Returns ``{}`` (never raises) if
    pyhocon isn't importable or the file doesn't exist.
    """
    if ConfigFactory is None or not conf_path.is_file():
        return {}
    conf = ConfigFactory.parse_file(str(conf_path.resolve()))
    flat: dict[str, str] = {}
    _flatten(conf, "", flat)
    return flat


def read_properties_keys(properties_path: Path) -> set[str]:
    """Parse ``application.properties`` into the set of keys currently present."""
    if not properties_path.is_file():
        return set()
    keys: set[str] = set()
    for raw_line in properties_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def diff_config_keys(
    flattened: dict[str, str], existing_keys: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Split ``flattened`` into (seed_mapped, leftover) against ``existing_keys``.

    - A key in ``SEED_KEY_MAP`` whose canonical target isn't already present
      goes into ``seed_mapped`` as ``{canonical: value}``, ready to append.
      If the canonical target is already present, it is skipped entirely
      (neither bucket) -- the seed table already resolved it unambiguously,
      nothing further is needed.
    - A key already starting with ``spring.`` or ``server.`` is already
      Spring-idiomatic -- skipped entirely.
    - Everything else needs LLM attention: goes into ``leftover``.
    """
    seed_mapped: dict[str, str] = {}
    leftover: dict[str, str] = {}
    for key, value in flattened.items():
        if key in SEED_KEY_MAP:
            canonical = SEED_KEY_MAP[key]
            if canonical not in existing_keys:
                seed_mapped[canonical] = value
            continue
        if key.startswith("spring.") or key.startswith("server."):
            continue
        leftover[key] = value
    return seed_mapped, leftover


def append_properties(properties_path: Path, new_entries: dict[str, str]) -> None:
    """Append ``key=value`` lines to ``properties_path`` (creates it if missing)."""
    if not new_entries:
        return
    properties_path.parent.mkdir(parents=True, exist_ok=True)
    block = "".join(f"{k}={v}\n" for k, v in new_entries.items())
    if properties_path.is_file():
        existing = properties_path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
        properties_path.write_text(existing + block, encoding="utf-8")
    else:
        properties_path.write_text(block, encoding="utf-8")
