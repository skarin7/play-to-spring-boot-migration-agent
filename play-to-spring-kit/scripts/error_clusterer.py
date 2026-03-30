#!/usr/bin/env python3
"""
Group Maven compile errors by root cause before LLM dispatch.

Reduces N separate error objects to K clusters (K << N), dramatically shrinking
prompt size. Each cluster carries a suggested_fix hint when the root cause matches
a known taxonomy entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Taxonomy hints (subset of E01–E12 that are useful as LLM hints)
# ---------------------------------------------------------------------------

_TAXONOMY_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cannot find symbol.*class ObjectMapper", re.I),
     "Add import com.fasterxml.jackson.databind.ObjectMapper"),
    (re.compile(r"cannot find symbol.*class JsonNode", re.I),
     "Add import com.fasterxml.jackson.databind.JsonNode"),
    (re.compile(r"package play\.\w+ does not exist", re.I),
     "Remove the play.* import line"),
    (re.compile(r"cannot find symbol.*method ok\(", re.I),
     "Replace ok( with ResponseEntity.ok("),
    (re.compile(r"incompatible types.*Result.*ResponseEntity", re.I),
     "Change method return type to ResponseEntity<JsonNode>"),
    (re.compile(r"cannot find symbol.*class Provider", re.I),
     "Replace javax.inject.Provider<T> with ObjectProvider<T>"),
    (re.compile(r"method .* is already defined", re.I),
     "Remove the duplicate method, keep the Spring-annotated version"),
    (re.compile(r"cannot find symbol.*class RestTemplate", re.I),
     "Add @Bean RestTemplate config class or import org.springframework.web.client.RestTemplate"),
]


def _taxonomy_hint(message: str) -> Optional[str]:
    for pattern, hint in _TAXONOMY_HINTS:
        if pattern.search(message):
            return hint
    return None


# ---------------------------------------------------------------------------
# Template extraction
# ---------------------------------------------------------------------------

# Patterns to strip from error messages to produce a normalised template
_STRIP_QUOTED = re.compile(r'"[^"]*"')          # quoted identifiers
_STRIP_SYMBOL = re.compile(r"symbol:\s*\S+\s+")  # "symbol:   class Foo" → ""
_STRIP_LOCATION = re.compile(r"location:\s*.+")  # "location: class Bar" → ""
_STRIP_FILE_PATH = re.compile(r"[\w/\\.-]+\.java")  # file paths
_STRIP_LINE_NUM = re.compile(r"\[\d+,\d+\]")     # [10,20]
_STRIP_DIGITS = re.compile(r"\b\d+\b")           # bare numbers


def extract_template(message: str) -> str:
    """
    Normalise an error message into a clustering key.

    Strips file paths, line numbers, quoted identifiers, and specific symbol
    names so that the same logical error across different files/symbols maps
    to the same template.
    """
    t = message.strip().lower()
    t = _STRIP_QUOTED.sub("<SYM>", t)
    t = _STRIP_FILE_PATH.sub("<FILE>", t)
    t = _STRIP_LINE_NUM.sub("", t)
    t = _STRIP_SYMBOL.sub("", t)
    t = _STRIP_LOCATION.sub("", t)
    t = _STRIP_DIGITS.sub("<N>", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ErrorCluster:
    root_cause: str                          # normalised template key
    representative: dict                     # one canonical error dict for the LLM
    affected_files: list[str] = field(default_factory=list)
    count: int = 0
    suggested_fix: Optional[str] = None


# ---------------------------------------------------------------------------
# Clusterer
# ---------------------------------------------------------------------------

class ErrorClusterer:
    """
    Groups compile errors by root cause.

    Postconditions:
    - sum(c.count for c in result) == len(errors)  (lossless)
    - Each error appears in exactly one cluster
    - Clusters sorted descending by count
    """

    def cluster(self, errors: list[dict]) -> list[ErrorCluster]:
        """
        Cluster *errors* by normalised message template.

        Returns clusters sorted descending by count.
        """
        template_map: dict[str, ErrorCluster] = {}

        for error in errors:
            msg = error.get("message", "")
            template = extract_template(msg)
            fp = error.get("file", "unknown")

            if template in template_map:
                c = template_map[template]
                c.count += 1
                if fp not in c.affected_files:
                    c.affected_files.append(fp)
            else:
                hint = _taxonomy_hint(msg)
                c = ErrorCluster(
                    root_cause=template,
                    representative=error,
                    affected_files=[fp],
                    count=1,
                    suggested_fix=hint,
                )
                template_map[template] = c

        return sorted(template_map.values(), key=lambda c: -c.count)
