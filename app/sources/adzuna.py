from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

from app.models.job import JobOffer


API_URL_TEMPLATE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def fetch_adzuna(
    query: str,
    country: str = "fr",
    where: str | None = None,
    page: int = 1,
    results_per_page: int = 50,
) -> list[JobOffer]:
    load_dotenv()

    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")

    if not app_id or not app_key:
        raise RuntimeError(
            "Missing Adzuna credentials. Set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env."
        )

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "results_per_page": results_per_page,
        "content-type": "application/json",
    }
    if where:
        params["where"] = where

    response = requests.get(
        API_URL_TEMPLATE.format(country=country, page=page),
        params=params,
        timeout=20,
    )
    response.raise_for_status()

    payload = response.json()
    items = payload.get("results", [])

    jobs: list[JobOffer] = []
    for item in items:
        url = item.get("redirect_url")
        title = item.get("title")
        company = (item.get("company") or {}).get("display_name", "Unknown company")

        if not url or not title:
            continue

        location = (item.get("location") or {}).get("display_name")

        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")
        salary = None
        if salary_min or salary_max:
            salary = f"{salary_min or '?'} - {salary_max or '?'}"

        jobs.append(
            JobOffer(
                source="adzuna",
                source_id=str(item.get("id")) if item.get("id") else None,
                title=title,
                company=company,
                location=location,
                url=url,
                description=item.get("description") or "",
                published_at=item.get("created"),
                tags=[],
                salary=salary,
                raw_json=item,
            )
        )

    return jobs
