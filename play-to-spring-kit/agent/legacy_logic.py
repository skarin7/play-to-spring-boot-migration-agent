"""Deterministic logic ported from scripts/migration_orchestrator.py.

Copied rather than imported so the graph engine does not depend on the
2900-line legacy module. Semantics must stay identical to:
  classify_compile_errors   (orchestrator :1156)
  try_deterministic_pom_fix (orchestrator :1238)
  is_looping                (orchestrator :1289)
  normalize_errors / error_signature (orchestrator :1139)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

LOG = logging.getLogger("agent.legacy_logic")

MVN_PACKAGE_MISSING_RE = re.compile(
    r"package\s+([\w.]+)\s+does\s+not\s+exist",
    re.IGNORECASE,
)


def normalize_errors(errors: list[dict[str, Any]]) -> list[str]:
    sigs = []
    for e in errors:
        fp = e.get("file", "")
        line = e.get("line", 0)
        msg = (e.get("message") or "").strip()
        sigs.append(f"{fp}:{line}:{msg}")
    return sorted(sigs)


def error_signature(e: dict[str, Any]) -> str:
    fp = e.get("file", "")
    line = e.get("line", 0)
    msg = (e.get("message") or "").strip()
    return f"{fp}:{line}:{msg}"


def classify_compile_errors(
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split Maven-style errors into infrastructure / missing-dependency / code buckets."""
    infra: list[dict[str, Any]] = []
    dep: list[dict[str, Any]] = []
    code: list[dict[str, Any]] = []
    for e in errors:
        fp = str(e.get("file", "") or "")
        msg = (e.get("message") or "").strip()
        low = msg.lower()
        if (
            "fatal error compiling" in low
            or "exceptionininitializererror" in low
            or ("typetag" in low and "unknown" in low)
        ) and (fp == "unknown" or not fp.endswith(".java")):
            infra.append(e)
            continue
        m = MVN_PACKAGE_MISSING_RE.search(msg)
        if m:
            pkg = m.group(1).strip()
            if pkg.startswith(
                (
                    "javax.inject",
                    "javax.annotation",
                    "jakarta.annotation",
                    "org.neo4j.driver.v1",
                    "com.google.common",
                    "play.",
                )
            ):
                dep.append(e)
            else:
                code.append(e)
            continue
        code.append(e)
    return infra, dep, code


# package prefix -> (groupId, artifactId, version or "" to omit version — BOM manages it)
_KNOWN_DEP_BY_PACKAGE_PREFIX: list[tuple[str, str, str, str]] = [
    ("com.google.common", "com.google.guava", "guava", "33.3.1-jre"),
    ("javax.inject", "javax.inject", "javax.inject", "1"),
    ("javax.annotation", "jakarta.annotation", "jakarta.annotation-api", ""),
    ("org.neo4j.driver.v1", "org.neo4j.driver", "neo4j-java-driver", ""),
]


def _pom_declares_dependency(text: str, group_id: str, artifact_id: str) -> bool:
    if f"<artifactId>{artifact_id}</artifactId>" not in text:
        return False
    i = 0
    while True:
        j = text.find(f"<artifactId>{artifact_id}</artifactId>", i)
        if j < 0:
            return False
        window = text[max(0, j - 400) : j + 80]
        if f"<groupId>{group_id}</groupId>" in window:
            return True
        i = j + 1


def _dependency_xml_block(group_id: str, artifact_id: str, version: str) -> str:
    ver_line = f"\n            <version>{version}</version>" if version else ""
    return f"""
        <dependency>
            <groupId>{group_id}</groupId>
            <artifactId>{artifact_id}</artifactId>{ver_line}
        </dependency>"""


def try_deterministic_pom_fix(
    spring_repo: Path,
    dep_errors: list[dict[str, Any]],
    dry_run: bool,
) -> int:
    """Add known Maven coordinates for missing-package errors. Returns count added."""
    if not dep_errors or dry_run:
        return 0
    pom = spring_repo / "pom.xml"
    if not pom.is_file():
        return 0
    text = pom.read_text(encoding="utf-8", errors="replace")
    needed: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for e in dep_errors:
        m = MVN_PACKAGE_MISSING_RE.search((e.get("message") or ""))
        if not m:
            continue
        pkg = m.group(1).strip()
        for prefix, gid, aid, ver in _KNOWN_DEP_BY_PACKAGE_PREFIX:
            if pkg == prefix or pkg.startswith(prefix + "."):
                key = (gid, aid)
                if key not in seen:
                    seen.add(key)
                    needed.append((gid, aid, ver))
                break
    blocks: list[str] = []
    for gid, aid, ver in needed:
        if _pom_declares_dependency(text, gid, aid):
            continue
        blocks.append(_dependency_xml_block(gid, aid, ver))
        LOG.info("pom fix: adding dependency %s:%s%s", gid, aid, f":{ver}" if ver else "")
    if not blocks:
        return 0
    close = "</dependencies>"
    idx = text.find(close)
    if idx < 0:
        LOG.warning("pom fix: no %s in pom.xml", close)
        return 0
    new_text = text[:idx] + "".join(blocks) + "\n    " + text[idx:]
    pom.write_text(new_text, encoding="utf-8")
    return len(blocks)


def is_looping(current: list[str], history: list[list[str]]) -> bool:
    if len(history) < 1:
        return False
    if current == history[-1]:
        return True
    if len(history) >= 2 and current == history[-2]:
        return True
    prev_n = len(history[-1])
    cur_n = len(current)
    if prev_n == 0:
        return False
    if cur_n < prev_n:
        return False
    spike_threshold = max(int(prev_n * 1.5 + 0.999), prev_n + 5)
    if cur_n > spike_threshold:
        return True
    return False
