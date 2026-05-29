# Job Intel

Job Intel is a personal job discovery and review tool.

It fetches offers from public job sources, screens them using configurable profiles and scoring presets, and can use an LLM to assist with final review decisions.

## Features

- Multi-source job fetching
- Rule-based screening
- AI-assisted review
- Profile editor
- Scoring preset editor
- SQLite storage
- Database save/load
- Profile and preset import/export

## Quick Start

```bash
python -m venv .venv
python -m pip install -e .
jobs ui
```

Open:

```text
http://127.0.0.1:8000
```

## Configuration

Create a `.env` file and configure the providers you use.

Examples:

```env
OPENAI_API_KEY=...
ADZUNA_APP_ID=...
ADZUNA_APP_KEY=...
```

Arbeitnow works without credentials.

## User Configuration

The repository ships with example configuration files:

```text
profiles/example.json
config/scoring_presets/example.json
```

Personal profiles and presets are intentionally ignored by Git.

## Main Workflow

1. Fetch offers
2. Review screened offers
3. Run AI review on selected candidates
4. Save or export results

## Project Structure

```text
app/
config/
profiles/
docs/
tests/
```
