from __future__ import annotations

import html
import re

import requests

from app.models.job import JobOffer


API_URL = "https://www.arbeitnow.com/api/job-board-api"


def _clean_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_arbeitnow(page: int = 1) -> list[JobOffer]:
    response = requests.get(API_URL, params={"page": page}, timeout=20)
    response.raise_for_status()

    payload = response.json()
    items = payload.get("data", [])

    jobs: list[JobOffer] = []
    for item in items:
        url = item.get("url")
        title = item.get("title")
        company = item.get("company_name")

        if not url or not title or not company:
            continue

        jobs.append(
            JobOffer(
                source="arbeitnow",
                title=title,
                company=company,
                location=item.get("location"),
                url=url,
                description=_clean_html(item.get("description")),
                tags=item.get("tags") or [],
                remote=item.get("remote"),
            )
        )

    return jobs
