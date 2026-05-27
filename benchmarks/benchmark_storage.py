from __future__ import annotations

import argparse
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.filtering.rules import evaluate_job, precompute_rule_matching
from app.models.job import JobOffer
from app.storage.files import load_profile
from app.storage.connection import DEFAULT_DB_PATH, init_db, open_connection
from app.storage.exploration import has_explored_offers_batch, record_explored_jobs_batch
from app.storage.offers import find_existing_offer_ids_batch, upsert_offers_batch
from app.storage.scoring import (
    list_scoring_presets,
    save_offer_scores_batch,
    save_screening_results_batch,
)


@dataclass(frozen=True)
class StorageBenchmark:
    elapsed: float
    offers: int
    pages: int = 0

    @property
    def offers_per_second(self) -> float:
        return self.offers / self.elapsed if self.elapsed > 0 else 0.0


def sample_jobs(count: int, *, source: str = "benchmark") -> list[JobOffer]:
    return [
        JobOffer(
            source=source,
            source_id=f"storage-{index}",
            title=f"C++ Simulation Engineer {index}",
            company="Benchmark",
            url=f"https://example.com/storage/{index}",
            location="France",
            description="C++ systems engineering and simulation infrastructure.",
            tags=["C++", "simulation"],
            raw_json={"index": index},
        )
        for index in range(count)
    ]


def benchmark_storage(
    jobs: list[JobOffer],
    *,
    db_path: Path,
    profile_id: str,
    min_score: int,
) -> StorageBenchmark:
    init_db(db_path)
    profile = load_profile(profile_id)
    presets = list_scoring_presets(db_path, enabled_only=True)
    configs = [preset.weights for preset in presets]
    precompute_rule_matching(profile, configs)

    evaluations_by_url = {
        str(job.url): {
            preset.id: evaluate_job(job, profile=profile, config=preset.weights)
            for preset in presets
        }
        for job in jobs
    }

    started_at = time.perf_counter()
    with open_connection(db_path) as connection:
        has_explored_offers_batch(connection, jobs[0].source if jobs else "benchmark", [(job.source_id, str(job.url)) for job in jobs])
        find_existing_offer_ids_batch(connection, jobs)
        _, offer_ids_by_url = upsert_offers_batch(connection, jobs)
        score_rows = []
        screening_rows = []
        for job in jobs:
            offer_id = offer_ids_by_url[str(job.url)]
            evaluations = evaluations_by_url[str(job.url)]
            selected = evaluations.get("balanced") or next(iter(evaluations.values()))
            score_rows.extend((offer_id, preset_id, evaluation) for preset_id, evaluation in evaluations.items())
            screening_rows.append((offer_id, profile_id, selected, min_score))
        save_offer_scores_batch(connection, score_rows)
        save_screening_results_batch(connection, screening_rows)
        record_explored_jobs_batch(connection, [(job, "inserted", None, False) for job in jobs])
    elapsed = time.perf_counter() - started_at
    return StorageBenchmark(elapsed=elapsed, offers=len(jobs))


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark SQLite persistence for fetch screening.")
    parser.add_argument("--offers", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--min-score", type=int, default=0)
    args = parser.parse_args()

    if args.offers < 1 or args.repeats < 1:
        raise SystemExit("--offers and --repeats must be positive.")

    total_elapsed = 0.0
    total_offers = 0
    for repeat in range(args.repeats):
        if args.db is None:
            with tempfile.TemporaryDirectory() as temp_dir:
                result = benchmark_storage(
                    sample_jobs(args.offers, source=f"benchmark-{repeat}"),
                    db_path=Path(temp_dir) / "storage.sqlite",
                    profile_id=args.profile,
                    min_score=args.min_score,
                )
        else:
            result = benchmark_storage(
                sample_jobs(args.offers, source=f"benchmark-{repeat}"),
                db_path=args.db,
                profile_id=args.profile,
                min_score=args.min_score,
            )
        total_elapsed += result.elapsed
        total_offers += result.offers

    offers_per_second = total_offers / total_elapsed if total_elapsed > 0 else 0.0
    print(f"Storage: {total_elapsed:.2f}s ({offers_per_second:.1f} offers/s, {total_offers} offers)")


if __name__ == "__main__":
    main()
