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
jobs clear --help
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

Fetch collection is target-based when `--new-offers` is provided. The app scans provider pages until the target number of newly explored provider offers is processed, `--max-pages` is reached, the provider has no more results, or `--max-seen-pages` consecutive pages contain only already-explored offers.

During fetch, the app also tracks explored provider items, including duplicates and filtered-out offers, so future runs can skip already-inspected results. Fetch automatically prunes explored, unranked, and ranked data using capacity limits.

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
python -m app.cli rank --weights-path config/scoring_presets/balanced.json
```

Profiles own candidate-specific scoring signals. The current profile format is JSON:

```json
{
  "must_match": {
    "any": ["systems", "simulation", "C++"]
  },
  "signals": {
    "interests": [
      { "term": "systems", "weight": 1.0 },
      { "term": "simulation", "weight": 0.8 }
    ],
    "disliked_work": [
      { "term": "generic CRUD", "weight": 1.0 }
    ]
  }
}
```

When `must_match.any` contains terms, an offer is rejected before normal scoring unless at least one term appears in the title, company, location, description, or tags. Presets under `config/scoring_presets/` can also define `weights.must_match.any` for generic preset-level gates.

Each category is normalized by its own total item weight, so adding more items does not automatically make that category dominate. Category weights and score calibration come from JSON scoring presets under `config/scoring_presets/`; profile JSON owns only item terms and item weights.

Start the local review dashboard:

```bash
python -m app.cli ui --db data/job_intel.sqlite --open-browser
```

The UI lists ranked offers from SQLite, supports sorting and filters for recommendation, status, source, location, ranking mode, and recency, lets you mark offers as `saved`, `skipped`, or `applied`, and can run fetch/rank workflows directly.

Clear specific stored data:

```bash
python -m app.cli clear --scope rankings --db data/job_intel.sqlite --yes
```

Supported clear scopes are `rankings`, `offers`, `explored`, and `all`. Without `--yes`, the command prints exactly what will be cleared and asks for confirmation.

More details on fetch collection, explored-offer tracking, pruning capacities, and clear scopes are in [docs/operations.md](docs/operations.md).

## Benchmarks

Run the full benchmark suite with one command:

```bash
python benchmarks/run_benchmarks.py --provider arbeitnow --pages 3 --repeats 1 --offers 500
```

On PowerShell with the project virtualenv:

```powershell
.venv\Scripts\python.exe benchmarks\run_benchmarks.py --provider arbeitnow --pages 3 --repeats 1 --offers 500
```

The runner executes fetch, fetch+parse, fetch+parse+scoring, full pipeline, standalone scoring, and standalone storage benchmarks. Each run writes a timestamped text report and JSON report under:

```text
benchmarks/results/
```

Use Adzuna by passing `--provider adzuna --query "c++ simulation" --country fr`; Adzuna requires `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in the environment.

## Output files

Fetched jobs are saved to:

```text
data/job_intel.sqlite
```

SQLite is the source of truth. It contains `explored_offers`, `offers`, `ranking_runs`, and `rankings`.
It also contains scoring preset and exploration metadata tables used by the screened-offer UI and fast backfill mode.

The saved ranking rows include run metadata, job metadata, weighted rule scoring, raw AI evaluation when used, and the final policy-adjusted decision.

The fetch command also prints the best rule matches in the terminal. The rank command prints an explainable shortlist sorted by final weighted score.

## Current Filtering Logic

Rule scoring uses profile-owned signal categories. For each category, the scorer computes:

```text
category score = matched item weights / total item weights
final contribution = category score * category weight
```

Built-in scoring presets are generic: they adjust category weights and score calibration, while item terms and item weights remain in profile JSON.
