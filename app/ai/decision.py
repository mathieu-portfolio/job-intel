from __future__ import annotations

from dataclasses import dataclass

from app.models.evaluation import AiJobEvaluation, FinalDecision, Recommendation, RuleEvaluation


@dataclass(frozen=True)
class DecisionWeights:
    rule: float = 0.35
    ai: float = 0.65
    wording_risk: float = 0.20
    seniority_mismatch: float = 0.35


def recommendation_from_score(score: int) -> Recommendation:
    if score >= 75:
        return "high"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "low"
    return "skip"


def _clamp_score(score: float) -> int:
    return max(0, min(100, round(score)))


def _recommendation_score_cap(recommendation: Recommendation) -> int:
    if recommendation == "high":
        return 100
    if recommendation == "medium":
        return 74
    if recommendation == "low":
        return 59
    return 39


def make_final_decision(
    *,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation | None = None,
    weights: DecisionWeights = DecisionWeights(),
) -> FinalDecision:
    rule_component = rule_evaluation.normalized_score
    if ai_evaluation is None:
        final_score = rule_component
        return FinalDecision(
            final_score=final_score,
            recommendation=recommendation_from_score(final_score),
            rule_component=rule_component,
            penalty_component=0,
            seniority_mismatch_penalty=0,
            reasoning=[
                f"Rule-only ranking from weighted term score {rule_evaluation.score}.",
                *rule_evaluation.reasoning,
            ],
        )

    ai_component = ai_evaluation.fit_score
    wording_penalty = round(ai_evaluation.wording_risk_score * weights.wording_risk)
    seniority_penalty = round((100 - ai_evaluation.seniority_fit_score) * weights.seniority_mismatch)
    penalty_component = min(100, wording_penalty + seniority_penalty)
    final_score = _clamp_score(
        (rule_component * weights.rule)
        + (ai_component * weights.ai)
        - penalty_component
    )

    reasoning = [
        f"Rule component {rule_component}/100 from weighted term score {rule_evaluation.score}.",
        f"AI component {ai_component}/100.",
    ]
    if wording_penalty:
        reasoning.append(f"Applied wording-risk penalty of {wording_penalty}.")
    if seniority_penalty:
        reasoning.append(f"Applied seniority-mismatch penalty of {seniority_penalty}.")

    policy_adjustments: list[str] = []
    ai_cap = _recommendation_score_cap(ai_evaluation.recommendation)
    if final_score > ai_cap:
        final_score = ai_cap
        policy_adjustments.append(
            f"Capped final score to match AI recommendation `{ai_evaluation.recommendation}`."
        )

    if (
        ai_evaluation.seniority_fit_score < 35
        and ai_evaluation.recommendation in {"high", "medium"}
    ):
        cap = 39 if ai_evaluation.seniority_fit_score < 25 else 59
        target = "skip" if cap == 39 else "low"
        if final_score > cap:
            final_score = cap
        policy_adjustments.append(
            "Downgraded final recommendation because AI reported high/medium "
            f"despite seniority_fit_score {ai_evaluation.seniority_fit_score}; "
            f"capped final score for `{target}`."
        )
    elif ai_evaluation.seniority_fit_score < 35 and final_score > 59:
        final_score = 59
        policy_adjustments.append(
            "Capped final score at low because seniority fit is a clear mismatch."
        )

    reasoning.extend(policy_adjustments)

    return FinalDecision(
        final_score=final_score,
        recommendation=recommendation_from_score(final_score),
        rule_component=rule_component,
        ai_component=ai_component,
        penalty_component=penalty_component,
        seniority_mismatch_penalty=seniority_penalty,
        policy_adjustments=policy_adjustments,
        reasoning=reasoning,
    )
