"""Compatibility facade for workflow orchestration.

New code can import focused workflow surfaces from app.workflow_parts.
"""

from app.workflow_parts.common import *  # noqa: F401,F403
from app.workflow_parts.common import __all__ as _common_all
from app.workflow_parts.fetch import fetch_offers  # noqa: F401
from app.workflow_parts.review import rank_offers  # noqa: F401


__all__ = [*_common_all, "fetch_offers", "rank_offers"]
