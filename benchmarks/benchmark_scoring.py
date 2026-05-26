from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.filtering.rules import evaluate_job, precompute_rule_matching
from app.models.job import JobOffer
from app.sources.adzuna import fetch_adzuna
from app.sources.arbeitnow import fetch_arbeitnow
from app.storage.files import load_profile
from app.storage.connection import DEFAULT_DB_PATH, init_db
from app.storage.scoring import list_scoring_presets


@dataclass(frozen=True)
class ScoringBenchmark:
    elapsed: float
    offers: int
    pages: int

    @property
    def offers_per_second(self) -> float:
        return self.offers / self.elapsed if self.elapsed > 0 else 0.0


def _sample_jobs(count: int) -> list[JobOffer]:
    return [
        JobOffer(
            source="benchmark",
            source_id=f"sample-{index}",
            title=f"C++ Simulation Systems Engineer {index}",
            company="Benchmark",
            url=f"https://example.com/jobs/{index}",
            location="France",
            description=(
                "C++ systems engineering, simulation, infrastructure, tooling, "
                "software architecture and numerical computing."
            ),
            tags=["C++", "simulation", "systems"],
            raw_json={},
        )
        for index in range(count)
    ]


def _fetch_jobs(provider: str, pages: int, query: str, country: str, where: str | None) -> list[JobOffer]:
    jobs: list[JobOffer] = []
    for page in range(1, pages + 1):
        if provider == "arbeitnow":
            jobs.extend(fetch_arbeitnow(page=page))
        else:
            jobs.extend(fetch_adzuna(query=query, country=country, where=where, page=page))
    return jobs


def score_jobs(jobs: list[JobOffer], *, profile_path: Path, db_path: Path = DEFAULT_DB_PATH) -> ScoringBenchmark:
    init_db(db_path)
    profile = load_profile(profile_path)
    presets = list_scoring_presets(db_path, enabled_only=True)
    configs = [preset.weights for preset in presets]
    precompute_rule_matching(profile, configs)

    started_at = time.perf_counter()
    for job in jobs:
        for config in configs:
            evaluate_job(job, profile=profile, config=config)
    elapsed = time.perf_counter() - started_at
    return ScoringBenchmark(elapsed=elapsed, offers=len(jobs) * max(len(configs), 1), pages=0)


def score_fetched_pages(
    provider: str,
    pages: int,
    query: str,
    country: str,
    where: str | None,
    *,
    profile_path: Path = Path("profiles/default.json"),
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[int, int]:
    jobs = _fetch_jobs(provider, pages, query, country, where)
    score_jobs(jobs, profile_path=profile_path, db_path=db_path)
    return len(jobs), pages


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark fast-rule scoring.")
    parser.add_argument("--provider", choices=["arbeitnow", "adzuna", "synthetic"], default="synthetic")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--offers", type=int, default=500)
    parser.add_argument("--profile", type=Path, default=Path("profiles/default.json"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--query", default="c++ simulation")
    parser.add_argument("--country", default="fr")
    parser.add_argument("--where", default=None)
    args = parser.parse_args()

    if args.repeats < 1:
        raise SystemExit("--repeats must be positive.")

    total_elapsed = 0.0
    total_offers = 0
    total_pages = 0
    for _ in range(args.repeats):
        if args.provider == "synthetic":
            jobs = _sample_jobs(args.offers)
            pages = 0
        else:
            jobs = _fetch_jobs(args.provider, args.pages, args.query, args.country, args.where)
            pages = args.pages
        result = score_jobs(jobs, profile_path=args.profile, db_path=args.db)
        total_elapsed += result.elapsed
        total_offers += result.offers
        total_pages += pages

    offers_per_second = total_offers / total_elapsed if total_elapsed > 0 else 0.0
    print(
        f"Scoring: {total_elapsed:.2f}s ({offers_per_second:.1f} offers/s, "
        f"{total_offers} scored offer/preset pairs, {total_pages} pages)"
    )


if __name__ == "__main__":
    main()
