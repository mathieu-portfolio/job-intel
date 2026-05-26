from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.models.profile import MustMatchRule

DEFAULT_RULE_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "scoring_presets" / "balanced.json"
)

class RuleScoringConfig(BaseModel):
    positive_terms: dict[str, int] = Field(default_factory=dict)
    negative_terms: dict[str, int] = Field(default_factory=dict)
    must_match: MustMatchRule = Field(default_factory=MustMatchRule)
    category_weights: dict[str, float]
    no_signal_score: int
    positive_score_scale: float
    negative_score_scale: float
    strong_negative_threshold: float
    strong_negative_score_cap: int


def load_rule_scoring_config(path: Path | None = None) -> RuleScoringConfig:
    if path is None:
        path = DEFAULT_RULE_CONFIG_PATH
    try:
        raw_config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"Rule weights file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Rule weights file is not valid JSON: {path}") from error
    return parse_rule_scoring_config(raw_config, source=str(path))


def parse_rule_scoring_config(raw_config: object, *, source: str = "rule scoring config") -> RuleScoringConfig:
    if isinstance(raw_config, dict) and isinstance(raw_config.get("weights"), dict):
        raw_config = raw_config["weights"]
    try:
        return RuleScoringConfig.model_validate(raw_config)
    except ValidationError as error:
        raise RuntimeError(f"Rule weights config has invalid fields: {source}") from error
