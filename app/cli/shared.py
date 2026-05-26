from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import requests
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.llm.factory import ProviderName
from app.models.evaluation import AiJobEvaluation, FinalDecision, RuleEvaluation, WeightedTermMatch
from app.models.job import JobOffer
from app.storage.connection import DEFAULT_DB_PATH
from app.storage.maintenance import (
    DEFAULT_EXPLORED_CAPACITY,
    DEFAULT_RANKED_CAPACITY,
    DEFAULT_UNRANKED_CAPACITY,
    clear_data,
    get_clear_plan,
)
from app.storage.models import VALID_CLEAR_SCOPES
from app.workflows import (
    FetchSource,
    RankingMode,
    fetch_offers,
    rank_offers,
)


app = typer.Typer(help="Fetch and filter technical job offers.", no_args_is_help=True)
console = Console()



def _format_term_matches(matches: list[WeightedTermMatch]) -> str:
    if not matches:
        return "none"
    formatted: list[str] = []
    for match in matches:
        alias = match.matched_alias or match.term
        language = f"/{match.language}" if match.language else ""
        alias_note = f" via {alias}{language}" if alias != match.term or match.language else ""
        formatted.append(f"{match.term}{alias_note} ({match.weight:+g})")
    return ", ".join(formatted)

def _print_clear_plan(scope: str, db: Path) -> None:
    plan = get_clear_plan(db_path=db, scope=scope)
    console.print(f"[bold]Database:[/bold] {db}")
    console.print(f"[bold]Clear scope:[/bold] {plan.scope}")
    console.print("[bold]Will clear:[/bold]")
    console.print(f"- explored offers: {plan.explored}")
    console.print(f"- offers: {plan.offers}")
    console.print(f"- rankings: {plan.rankings}")
    console.print(f"- ranking runs: {plan.ranking_runs}")
