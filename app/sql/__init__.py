from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.resources import resource_path


SQL_DIR = resource_path("app", "sql")


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")
