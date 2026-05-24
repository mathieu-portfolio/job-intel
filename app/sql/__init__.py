from __future__ import annotations

from functools import lru_cache
from pathlib import Path


SQL_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")
