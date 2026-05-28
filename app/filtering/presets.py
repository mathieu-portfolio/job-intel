from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.filtering.rules import RuleScoringConfig


SCORING_PRESET_DIR = Path(__file__).resolve().parents[2] / "config" / "scoring_presets"


@dataclass(frozen=True)
class ScoringPreset:
    id: str
    name: str
    description: str
    weights: RuleScoringConfig
    is_builtin: bool = True
    enabled: bool = True
    order: int = 100


class ScoringPresetFile(BaseModel):
    id: str
    name: str
    description: str = ""
    weights: RuleScoringConfig
    is_builtin: bool = True
    enabled: bool = True
    order: int = 100


def load_builtin_scoring_presets(directory: Path = SCORING_PRESET_DIR) -> tuple[ScoringPreset, ...]:
    presets: list[ScoringPreset] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw_preset = json.loads(path.read_text(encoding="utf-8"))
            preset_file = ScoringPresetFile.model_validate(raw_preset)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise RuntimeError(f"Scoring preset file is invalid: {path}") from error
        presets.append(
            ScoringPreset(
                id=preset_file.id,
                name=preset_file.name,
                description=preset_file.description,
                weights=preset_file.weights,
                is_builtin=preset_file.is_builtin,
                enabled=preset_file.enabled,
                order=preset_file.order,
            )
        )
    if not presets:
        raise RuntimeError(f"No scoring presets found in {directory}")
    return tuple(sorted(presets, key=lambda preset: (preset.order, preset.name.lower())))


# Backwards-compatible name for old imports. Do not load presets at import time:
# callers must use load_builtin_scoring_presets() so config/scoring_presets is
# the runtime source of truth.
BUILTIN_SCORING_PRESETS: tuple[ScoringPreset, ...] = ()
