# Job Intel

AI-assisted technical job fetching and filtering prototype.

The current version is intentionally small: it fetches job offers from public APIs, normalizes them into a common schema, applies a cheap rule-based filter, and can use an LLM provider to rank the surviving jobs against a candidate profile.

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
OLLAMA_MODEL=qwen3
```

Adzuna requires an `app_id` and `app_key`. Leave these empty if you only use Arbeitnow.

OpenAI ranking calls the OpenAI API and uses paid API credits. Mock mode avoids API costs.

## Ollama

Install Ollama from the official installer for your platform, then pull a local model:

```bash
ollama pull qwen3
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
```

Fetch and filter jobs from Arbeitnow:

```bash
jobs fetch --source arbeitnow --page 1 --min-score 10
```

Fetch and filter jobs from Adzuna:

```bash
jobs fetch --source adzuna --query "c++ simulation" --country fr --where France --min-score 10
```

You can also run the fetch command as a Python module:

```bash
python -m app.cli fetch --source arbeitnow --page 1 --min-score 10
```

Preview which jobs would be sent to an LLM without requiring provider credentials:

```bash
python -m app.cli rank --dry-run
```

Rank the filtered jobs with the configured provider:

```bash
python -m app.cli rank
```

Switch providers explicitly:

```bash
python -m app.cli rank --provider openai
python -m app.cli rank --provider ollama
python -m app.cli rank --provider mock
```

Use a custom profile or jobs file:

```bash
python -m app.cli rank --profile profiles/default.json --jobs-path data/normalized/latest_jobs.json --limit 10
```

## Output files

Fetched jobs are saved to:

```text
data/normalized/latest_jobs.json
```

The fetch command also prints the best rule matches in the terminal. The rank command prints an explainable LLM shortlist sorted by fit score.

## Current filtering logic

The first version uses simple keyword scoring.
