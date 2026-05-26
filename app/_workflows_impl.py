"""Compatibility facade for workflow orchestration.

Actual workflow behavior is split across app.workflow_parts.
"""

from app.workflow_parts.common import *  # noqa: F401,F403
from app.workflow_parts.fetch import fetch_offers  # noqa: F401
from app.workflow_parts.review import rank_offers  # noqa: F401
