"""SqliteSaver checkpointing — source of truth for resumability."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .config import AgentConfig


def thread_id_for(config: AgentConfig) -> str:
    return hashlib.sha1(str(config.spring_repo).encode("utf-8")).hexdigest()


def make_checkpointer(config: AgentConfig):
    from langgraph.checkpoint.sqlite import SqliteSaver

    config.migration_dir.mkdir(parents=True, exist_ok=True)
    db_path: Path = config.migration_dir / "langgraph-checkpoints.sqlite"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    return SqliteSaver(conn)
