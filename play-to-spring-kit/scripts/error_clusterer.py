#!/usr/bin/env python3
"""
Error clusterer: groups compile errors by root cause before LLM dispatch.

Reduces N separate error objects to K clusters (K << N), dramatically
shrinking prompt size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
#  Taxonomy hint lookup (maps normalised template keys to suggested fixes)
# ---------------------------------------------------------------------------

_TAXONOMY_HINTS: dict[str, str] = {
    "cannot_find_symbol:ObjectMapper": "Add import com.fasterxml.jackson.databind.ObjectMapper",
    "cannot_find_symbol:JsonNode": "Add import com.fasterxml.jackson.databind.JsonNode",
    "cannot_find_symbol:RestTemplate": "Add @Bean RestTemplate config class or import",
    "cannot_find_symbol:Provider": "Replace javax.inject.Provider<T> with ObjectProvider<T>",
    "package_does_not_exist:play": "Remove play.* import line",
}


@dataclass
class ErrorCluster:
    """A group of errors sharing the same root cause."""

    root_cause: str
    representative: dict
    affected_files: list[str] = field(default_factory=list)
    count: int = 1
    suggested_fix: str | None = None


class ErrorClusterer:
    """
    Groups compile errors by normalised message template.

    Invariants:
    - ``sum(c.count for c in clusters) == len(errors)``
    - Each error appears in exactly one cluster
    - Clusters are sorted descending by ``count``
    """

    def cluster(self, errors: list[dict]) -> list[ErrorCluster]:
        if not errors:
            return []

        template_map: dict[str, ErrorCluster] = {}

        for error in errors:
            template = self._extract_template(error.get("message", ""))

            if template in template_map:
                c = template_map[template]
                c.count += 1
                f = error.get("file", "")
                if f and f not in c.affected_files:
                    c.affected_files.append(f)
            else:
                suggested = self._lookup_taxonomy_hint(template)
                c = ErrorCluster(
                    root_cause=template,
                    representative=error,
                    affected_files=[error.get("file", "")] if error.get("file") else [],
                    count=1,
                    suggested_fix=suggested,
                )
                template_map[template] = c

        clusters = sorted(template_map.values(), key=lambda c: -c.count)
        return clusters

    # -------------------------------------------------------------------
    #  Template extraction
    # -------------------------------------------------------------------

    # Patterns to strip when normalising error messages
    _FILE_PATH_RE = re.compile(r"(/[\w./-]+\.java|[\w.]+\.java)")
    _LINE_NUMBER_RE = re.compile(r":\d+")
    _QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

    def _extract_template(self, message: str) -> str:
        """
        Normalise an error message into a clustering key.

        Steps:
        1. Strip file paths
        2. Strip line numbers
        3. Extract the symbol name from "cannot find symbol" messages
        4. Lowercase
        """
        msg = message.strip()

        # Extract symbol name for "cannot find symbol" errors
        symbol_match = re.search(
            r"cannot find symbol.*(?:class|method|variable)\s+(\w+)", msg, re.IGNORECASE
        )
        if symbol_match:
            symbol = symbol_match.group(1)
            return f"cannot_find_symbol:{symbol}"

        # Extract package for "package X does not exist"
        pkg_match = re.search(r"package\s+([\w.]+)\s+does not exist", msg, re.IGNORECASE)
        if pkg_match:
            pkg = pkg_match.group(1)
            # Group play.* packages together
            if pkg.startswith("play."):
                return "package_does_not_exist:play"
            return f"package_does_not_exist:{pkg}"

        # Extract "incompatible types" pattern
        if "incompatible types" in msg.lower():
            return "incompatible_types"

        # Extract "method is already defined"
        if "is already defined" in msg.lower():
            return "method_already_defined"

        # Generic: clean up and use as-is
        cleaned = self._FILE_PATH_RE.sub("<FILE>", msg)
        cleaned = self._LINE_NUMBER_RE.sub("", cleaned)
        cleaned = self._QUOTED_RE.sub("<Q>", cleaned)
        return cleaned.lower().strip()

    def _lookup_taxonomy_hint(self, template: str) -> str | None:
        """Look up a suggested fix from the taxonomy table."""
        return _TAXONOMY_HINTS.get(template)
