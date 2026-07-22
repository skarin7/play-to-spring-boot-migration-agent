"""LangGraph-based migration engine.

Reuses the deterministic components under ``play-to-spring-kit/scripts``
(CompileErrorFixer, ErrorClusterer, IncrementalCompiler, PromptBuilder)
as plain graph nodes; LLM agents run via an OpenRouter-compatible endpoint.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the legacy deterministic modules importable (compile_error_fixer,
# error_clusterer, incremental_compiler, prompt_builder).
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
