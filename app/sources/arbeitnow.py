from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import requests

from app.models.job import JobOffer


API_URL = "https://www.arbeitnow.com/api/job-board-api"


def _clean_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _published_at(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")
    return str(value)


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
                source_id=str(item.get("slug") or item.get("id") or "") or None,
                title=title,
                company=company,
                location=item.get("location"),
                url=url,
                description=_clean_html(item.get("description")),
                published_at=_published_at(item.get("created_at")),
                tags=item.get("tags") or [],
                remote=item.get("remote"),
                raw_json=item,
            )
        )

    return jobs
