from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sources.adzuna import API_URL_TEMPLATE as ADZUNA_API_URL_TEMPLATE
from app.sources.adzuna import fetch_adzuna
from app.sources.arbeitnow import API_URL as ARBEITNOW_API_URL
from app.sources.arbeitnow import fetch_arbeitnow
from app.workflows import fetch_offers


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    elapsed: float
    offers: int
    pages: int

    @property
    def offers_per_second(self) -> float:
        return self.offers / self.elapsed if self.elapsed > 0 else 0.0


def _print_result(result: BenchmarkResult) -> None:
    print(
        f"{result.name}: {result.elapsed:.2f}s "
        f"({result.offers_per_second:.1f} offers/s, "
        f"{result.offers} offers, {result.pages} pages)"
    )


def _repeat(name: str, repeats: int, run: Callable[[], tuple[int, int]]) -> BenchmarkResult:
    total_elapsed = 0.0
    total_offers = 0
    total_pages = 0
    for _ in range(repeats):
        started_at = time.perf_counter()
        offers, pages = run()
        total_elapsed += time.perf_counter() - started_at
        total_offers += offers
        total_pages += pages
    return BenchmarkResult(name=name, elapsed=total_elapsed, offers=total_offers, pages=total_pages)


def _raw_fetch_arbeitnow(page: int) -> int:
    response = requests.get(ARBEITNOW_API_URL, params={"page": page}, timeout=20)
    response.raise_for_status()
    return len(response.json().get("data", []))


def _raw_fetch_adzuna(*, page: int, query: str, country: str, where: str | None) -> int:
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("Missing ADZUNA_APP_ID or ADZUNA_APP_KEY for raw Adzuna fetch.")
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "results_per_page": 50,
        "content-type": "application/json",
    }
    if where:
        params["where"] = where
    response = requests.get(
        ADZUNA_API_URL_TEMPLATE.format(country=country, page=page),
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    return len(response.json().get("results", []))


def _fetch_parsed(provider: str, page: int, query: str, country: str, where: str | None) -> int:
    if provider == "arbeitnow":
        return len(fetch_arbeitnow(page=page))
    return len(fetch_adzuna(query=query, country=country, where=where, page=page))


def _raw_fetch(provider: str, page: int, query: str, country: str, where: str | None) -> int:
    if provider == "arbeitnow":
        return _raw_fetch_arbeitnow(page)
    return _raw_fetch_adzuna(page=page, query=query, country=country, where=where)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark provider fetch and fetch pipeline stages.")
    parser.add_argument("--provider", choices=["arbeitnow", "adzuna"], default="arbeitnow")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--stage", choices=["fetch-only", "fetch-parse", "fetch-score", "full", "all"], default="all")
    parser.add_argument("--query", default="c++ simulation")
    parser.add_argument("--country", default="fr")
    parser.add_argument("--where", default=None)
    parser.add_argument("--min-score", type=int, default=0)
    args = parser.parse_args()

    if args.pages < 1 or args.repeats < 1:
        raise SystemExit("--pages and --repeats must be positive.")

    stages = [args.stage] if args.stage != "all" else ["fetch-only", "fetch-parse", "fetch-score", "full"]
    results: list[BenchmarkResult] = []

    if "fetch-only" in stages:
        results.append(
            _repeat(
                "Fetch",
                args.repeats,
                lambda: (
                    sum(_raw_fetch(args.provider, page, args.query, args.country, args.where) for page in range(1, args.pages + 1)),
                    args.pages,
                ),
            )
        )

    if "fetch-parse" in stages:
        results.append(
            _repeat(
                "Fetch + Parse",
                args.repeats,
                lambda: (
                    sum(_fetch_parsed(args.provider, page, args.query, args.country, args.where) for page in range(1, args.pages + 1)),
                    args.pages,
                ),
            )
        )

    if "fetch-score" in stages:
        from benchmarks.benchmark_scoring import score_fetched_pages

        results.append(
            _repeat(
                "Fetch + Parse + Scoring",
                args.repeats,
                lambda: score_fetched_pages(args.provider, args.pages, args.query, args.country, args.where),
            )
        )

    if "full" in stages:
        def run_full() -> tuple[int, int]:
            with tempfile.TemporaryDirectory() as temp_dir:
                result = fetch_offers(
                    source=args.provider,
                    db_path=Path(temp_dir) / "benchmark.sqlite",
                    new_offers=args.pages * 50,
                    max_pages=args.pages,
                    query=args.query,
                    country=args.country,
                    where=args.where,
                    min_score=args.min_score,
                )
                return result.stats.fetched, result.stats.pages_scanned

        results.append(_repeat("Full Pipeline", args.repeats, run_full))

    for result in results:
        _print_result(result)


if __name__ == "__main__":
    main()
