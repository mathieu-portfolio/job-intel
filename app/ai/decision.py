from __future__ import annotations

from dataclasses import dataclass

from app.models.evaluation import (
    AiJobEvaluation,
    FinalDecision,
    RuleEvaluation,
    recommendation_from_score,
)


@dataclass(frozen=True)
class DecisionWeights:
    # Rule and AI build the base fit score. Seniority is then blended in as a
    # separate suitability dimension so unclear/mismatched seniority cannot
    # completely dominate an otherwise useful offer.
    rule: float = 0.50
    ai: float = 0.50
    seniority: float = 0.30


def _clamp_score(score: float) -> int:
    return max(0, min(100, round(score)))


def _rule_cap(rule_evaluation: RuleEvaluation) -> tuple[int | None, str | None]:
    if rule_evaluation.decision == "skip":
        return 40, "Capped final score at 40 because rule evaluation rejected the offer."
    if rule_evaluation.normalized_score < 30:
        return 50, "Capped final score at 50 because rule score is very low."
    return None, None


def weighted_base_score(rule_component: int, ai_component: int | None, weights: DecisionWeights) -> int:
    if ai_component is None:
        return rule_component

    total_base_weight = weights.rule + weights.ai
    if total_base_weight <= 0:
        return _clamp_score((rule_component + ai_component) / 2)

    return _clamp_score(
        ((rule_component * weights.rule) + (ai_component * weights.ai)) / total_base_weight
    )


def blend_seniority(base_score: int, seniority_component: int, seniority_weight: float) -> int:
    weight = max(0.0, min(1.0, seniority_weight))
    return _clamp_score(((1.0 - weight) * base_score) + (weight * seniority_component))


def make_final_decision(
    *,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation | None = None,
    weights: DecisionWeights = DecisionWeights(),
) -> FinalDecision:
    rule_component = rule_evaluation.normalized_score
    ai_component = ai_evaluation.fit_score if ai_evaluation is not None else None
    seniority_component = rule_evaluation.seniority.score

    base_score = weighted_base_score(rule_component, ai_component, weights)
    final_score = blend_seniority(base_score, seniority_component, weights.seniority)

    if ai_component is None:
        reasoning = [
            f"Base score {base_score}/100 from calibrated rule score {rule_component}/100.",
            (
                f"Final score blends base score with deterministic seniority "
                f"{seniority_component}/100 using seniority weight {weights.seniority:.0%}."
            ),
            *rule_evaluation.reasoning,
        ]
    else:
        reasoning = [
            (
                f"Base score {base_score}/100 from rule component {rule_component}/100 "
                f"and AI semantic component {ai_component}/100."
            ),
            (
                f"Final score blends base score with deterministic seniority "
                f"{seniority_component}/100 using seniority weight {weights.seniority:.0%}."
            ),
        ]

    policy_adjustments: list[str] = []
    cap, message = _rule_cap(rule_evaluation)
    if cap is not None and final_score > cap:
        final_score = cap
        if message:
            policy_adjustments.append(message)

    reasoning.extend(policy_adjustments)

    return FinalDecision(
        final_score=final_score,
        recommendation=recommendation_from_score(final_score),
        rule_component=rule_component,
        ai_component=ai_component,
        base_component=base_score,
        seniority_component=seniority_component,
        penalty_component=0,
        seniority_mismatch_penalty=max(0, 100 - seniority_component),
        policy_adjustments=policy_adjustments,
        reasoning=reasoning,
    )
