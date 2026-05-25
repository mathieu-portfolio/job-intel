# Job Intel

AI-assisted technical job fetching and filtering prototype.

The current version is intentionally small: it fetches job offers from public APIs, stores them in SQLite, applies a cheap rule-based filter, and can use an LLM provider to rank jobs against a candidate profile.

## Requirements

- Python 3.11+
- `pip`

## Setup

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it.

On Windows Git Bash:

```bash
source .venv/Scripts/activate
```

On PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

Upgrade `pip`:

```bash
python -m pip install --upgrade pip
```

Install the project in editable mode:

```bash
python -m pip install -e .
```

This installs the dependencies from `pyproject.toml` and exposes the `jobs` command.

Fallback install path:

```bash
python -m pip install -r requirements.txt
```

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

The project can fetch from Arbeitnow without credentials.

For Adzuna, fill:

```env
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_app_key
```

For AI ranking, choose a provider:

```env
JOB_INTEL_LLM_PROVIDER=openai
```

For OpenAI ranking, set:

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini
```

For Ollama ranking, set:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
OLLAMA_TIMEOUT=120
```

Adzuna requires an `app_id` and `app_key`. Leave these empty if you only use Arbeitnow.

OpenAI ranking calls the OpenAI API and uses paid API credits. Mock mode avoids API costs.

## Ollama

Install Ollama from the official installer for your platform, then pull a local model:

```bash
ollama pull llama3.2:3b
```

Start the local Ollama server:

```bash
ollama serve
```

Then run ranking with:

```bash
python -m app.cli rank --provider ollama
```

## Commands

Show available commands:

```bash
jobs --help
jobs fetch --help
jobs rank --help
jobs ui --help
```

Fetch and filter jobs from Arbeitnow:

```bash
jobs fetch --source arbeitnow --new-offers 20 --max-pages 10 --min-score 10
```

Fetch and filter jobs from Adzuna:

```bash
jobs fetch --source adzuna --new-offers 20 --max-pages 10 --query "c++ simulation" --country fr --where France --min-score 10
```

You can also run the fetch command as a Python module:

```bash
python -m app.cli fetch --source arbeitnow --new-offers 20 --max-pages 10 --min-score 10
```

Fetch writes to SQLite by default:

```bash
python -m app.cli fetch --source arbeitnow --db data/job_intel.sqlite
```

Preview which database offers would be ranked without requiring provider credentials:

```bash
python -m app.cli rank --dry-run
```

Rank the filtered jobs with the configured provider:

```bash
python -m app.cli rank
```

Choose the ranking mode:

```bash
python -m app.cli rank --ranking-mode rules
python -m app.cli rank --ranking-mode ai
python -m app.cli rank --ranking-mode hybrid
```

Rules mode never calls an LLM. Hybrid mode uses the weighted rule score as a prefilter and combines it with the AI score and penalties for the final decision.

Switch providers explicitly:

```bash
python -m app.cli rank --provider openai
python -m app.cli rank --provider ollama
python -m app.cli rank --provider mock
```

Use a custom profile:

```bash
python -m app.cli rank --profile profiles/default.json --limit 10
```

Use a custom database or only recent offers:

```bash
python -m app.cli rank --db data/job_intel.sqlite --only-recent-days 14 --limit 10
```

Use custom rule weights:

```bash
python -m app.cli rank --weights-path config/rule_weights.example.json
```

Start the local review dashboard:

```bash
python -m app.cli ui --db data/job_intel.sqlite --open-browser
```

The UI lists ranked offers from SQLite, supports sorting and filters for recommendation, status, source, location, ranking mode, and recency, lets you mark offers as `saved`, `skipped`, or `applied`, and can run fetch/rank workflows directly.

## Output files

Fetched jobs are saved to:

```text
data/job_intel.sqlite
```

SQLite is the source of truth. It contains `offers`, `ranking_runs`, and `rankings`.

The saved ranking rows include run metadata, job metadata, weighted rule scoring, raw AI evaluation when used, and the final policy-adjusted decision.

The fetch command also prints the best rule matches in the terminal. The rank command prints an explainable shortlist sorted by final weighted score.

## Current filtering logic

Rule scoring uses weighted positive and negative term matches. The default weights are in code, and `config/rule_weights.example.json` shows the supported configuration shape.
