"""Compatibility shim for older imports.

The FastAPI UI implementation lives in the ``app.ui`` package.
"""

from app.ui import create_app

__all__ = ["create_app"]
