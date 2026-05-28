from __future__ import annotations


def _normalize_review_result(result: dict[str, object]) -> None:
    """Fill display-only score fields for older saved review JSON.

    Existing databases may contain final decisions created before base_component
    existed. This keeps the UI readable until those offers are re-reviewed.
    """
    final = result.get("final_decision")
    if not isinstance(final, dict):
        return
    if final.get("base_component") is not None:
        return
    rule_component = final.get("rule_component")
    ai_component = final.get("ai_component")
    try:
        if ai_component is None:
            final["base_component"] = int(rule_component)
        else:
            final["base_component"] = round((int(rule_component) + int(ai_component)) / 2)
    except (TypeError, ValueError):
        pass

def _normalize_review_offers(offers: list[dict[str, object]]) -> None:
    for offer in offers:
        result = offer.get("result")
        if isinstance(result, dict):
            _normalize_review_result(result)


__all__ = ["_normalize_review_result", "_normalize_review_offers"]
