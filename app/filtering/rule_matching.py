from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from app.models.profile import CandidateProfile, ProfileSignalItem
from app.filtering.rule_config import RuleScoringConfig

class Alias:
    text: str
    language: str | None = None


class MatchResult:
    canonical_term: str
    matched_alias: str
    language: str | None = None


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(character for character in decomposed if not unicodedata.combining(character))
    punctuation_spaced = re.sub(r"[^\w\s+#]", " ", without_accents)
    return re.sub(r"\s+", " ", punctuation_spaced).strip()


def _contains_normalized_term(normalized_text: str, normalized_term: str) -> bool:
    if not normalized_term:
        return False
    escaped = re.escape(normalized_term)
    return re.search(rf"(?<![\w+#]){escaped}(?![\w+#])", normalized_text) is not None


def _contains_term(text: str, term: str) -> bool:
    return _contains_normalized_term(normalize_text(text), normalize_text(term))


def _alias_cache_key(item: ProfileSignalItem) -> str:
    return json.dumps(item.aliases, sort_keys=True, ensure_ascii=False)


def _normalized_aliases_for_item(term: str, aliases_key: str) -> tuple[tuple[str, str, str | None], ...]:
    aliases = json.loads(aliases_key) if aliases_key else {}
    normalized_aliases: list[tuple[str, str, str | None]] = []
    if isinstance(aliases, dict):
        for language, values in aliases.items():
            if isinstance(values, list):
                normalized_aliases.extend(
                    (normalize_text(str(alias)), str(alias), str(language))
                    for alias in values
                    if str(alias).strip()
                )
    if not normalized_aliases:
        normalized_aliases.append((normalize_text(term), term, None))
    return tuple(normalized_aliases)


def match_signal_item(text: str, item: ProfileSignalItem) -> MatchResult | None:
    normalized_text = normalize_text(text)
    for normalized_alias, alias_text, language in _normalized_aliases_for_item(item.term, _alias_cache_key(item)):
        if _contains_normalized_term(normalized_text, normalized_alias):
            return MatchResult(
                canonical_term=item.term,
                matched_alias=alias_text,
                language=language,
            )
    return None


def precompute_rule_matching(profile: CandidateProfile | None, configs: list[RuleScoringConfig]) -> None:
    items: list[ProfileSignalItem] = []
    if profile is not None:
        items.extend(profile.must_match.any)
        for category in profile.signals.values():
            items.extend(category.items)
    for config in configs:
        items.extend(config.must_match.any)
    for item in items:
        _normalized_aliases_for_item(item.term, _alias_cache_key(item))
