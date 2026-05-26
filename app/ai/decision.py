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
    rule: float = 0.45
    ai: float = 0.45
    posting_quality: float = 0.10


def _clamp_score(score: float) -> int:
    return max(0, min(100, round(score)))


def _seniority_cap(score: int) -> tuple[int | None, str | None]:
    if score < 35:
        return 40, "Capped final score at 40 because deterministic seniority fit is a strong mismatch."
    if score < 60:
        return 65, "Capped final score at 65 because deterministic seniority fit is weak."
    return None, None


def _rule_cap(rule_evaluation: RuleEvaluation) -> tuple[int | None, str | None]:
    if rule_evaluation.decision == "skip":
        return 40, "Capped final score at 40 because rule evaluation rejected the offer."
    if rule_evaluation.normalized_score < 30:
        return 50, "Capped final score at 50 because rule score is very low."
    return None, None


def make_final_decision(
    *,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation | None = None,
    weights: DecisionWeights = DecisionWeights(),
) -> FinalDecision:
    rule_component = rule_evaluation.normalized_score
    seniority_component = rule_evaluation.seniority.score

    if ai_evaluation is None:
        final_score = rule_component
        policy_adjustments: list[str] = []
        for cap, message in (_seniority_cap(seniority_component), _rule_cap(rule_evaluation)):
            if cap is not None and final_score > cap:
                final_score = cap
                if message:
                    policy_adjustments.append(message)
        reasoning = [
            f"Rule-only ranking from weighted term score {rule_evaluation.score}.",
            f"Deterministic seniority component {seniority_component}/100.",
            *rule_evaluation.reasoning,
            *policy_adjustments,
        ]
        return FinalDecision(
            final_score=final_score,
            recommendation=recommendation_from_score(final_score),
            rule_component=rule_component,
            seniority_component=seniority_component,
            penalty_component=0,
            seniority_mismatch_penalty=max(0, 100 - seniority_component),
            policy_adjustments=policy_adjustments,
            reasoning=reasoning,
        )

    ai_component = ai_evaluation.fit_score
    posting_quality_component = ai_evaluation.posting_quality_score
    final_score = _clamp_score(
        (rule_component * weights.rule)
        + (ai_component * weights.ai)
        + (posting_quality_component * weights.posting_quality)
    )

    reasoning = [
        f"Rule component {rule_component}/100 from weighted term score {rule_evaluation.score}.",
        f"AI semantic component {ai_component}/100.",
        f"Posting quality component {posting_quality_component}/100.",
        f"Deterministic seniority component {seniority_component}/100.",
    ]

    policy_adjustments: list[str] = []
    for cap, message in (_seniority_cap(seniority_component), _rule_cap(rule_evaluation)):
        if cap is not None and final_score > cap:
            final_score = cap
            if message:
                policy_adjustments.append(message)

    if posting_quality_component < 35 and final_score > 70:
        final_score = 70
        policy_adjustments.append("Capped final score at 70 because posting quality is poor.")

    reasoning.extend(policy_adjustments)

    return FinalDecision(
        final_score=final_score,
        recommendation=recommendation_from_score(final_score),
        rule_component=rule_component,
        ai_component=ai_component,
        posting_quality_component=posting_quality_component,
        seniority_component=seniority_component,
        penalty_component=0,
        seniority_mismatch_penalty=max(0, 100 - seniority_component),
        policy_adjustments=policy_adjustments,
        reasoning=reasoning,
    )
