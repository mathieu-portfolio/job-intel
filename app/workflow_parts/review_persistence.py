from __future__ import annotations

from pathlib import Path

from app.workflow_parts.common import (
    AiJobEvaluation,
    FinalDecision,
    RankingMode,
    RuleEvaluation,
    StoredOffer,
    ranking_result_payload,
    save_ranking,
)


def ai_evaluation_from_saved_review(review_row: dict[str, object]) -> AiJobEvaluation | None:
    raw_review = review_row.get("review")
    if not isinstance(raw_review, dict):
        return None
    candidate = raw_review.get("raw_ai_evaluation")
    if candidate is None:
        candidate = raw_review.get("ai_evaluation")
    if candidate is None:
        candidate = raw_review
    if not isinstance(candidate, dict):
        return None
    try:
        return AiJobEvaluation.model_validate(candidate)
    except Exception:
        return None


def save_ranked_result(
    *,
    db_path: Path,
    run_id: int,
    stored_offer: StoredOffer,
    rule_evaluation: RuleEvaluation,
    ai_evaluation: AiJobEvaluation | None,
    final_decision: FinalDecision,
    ranking_mode: RankingMode,
    model_name: str | None,
    profile_path: Path,
    profile_id: str,
    summary: str,
) -> None:
    save_ranking(
        db_path=db_path,
        run_id=run_id,
        offer_id=stored_offer.id,
        algorithm=ranking_mode,
        model=model_name,
        profile_path=str(profile_path),
        profile_id=profile_id,
        score=final_decision.final_score,
        recommendation=final_decision.recommendation,
        summary=summary,
        result=ranking_result_payload(
            stored_offer=stored_offer,
            rule_evaluation=rule_evaluation,
            ai_evaluation=ai_evaluation,
            final_decision=final_decision,
        ),
    )
