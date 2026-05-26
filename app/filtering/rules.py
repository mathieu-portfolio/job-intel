"""Compatibility facade for rule-based job scoring.

Implementation is split into config, matching, and scoring modules.
"""

from app.filtering.rule_config import *  # noqa: F401,F403
from app.filtering.rule_matching import *  # noqa: F401,F403
from app.filtering.rule_scoring import *  # noqa: F401,F403
