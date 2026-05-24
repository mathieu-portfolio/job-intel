# Job Intel

AI-assisted technical job fetching and filtering prototype.

The current version is intentionally small: it fetches job offers from public APIs, normalizes them into a common schema, applies a cheap rule-based filter, and prints a ranked shortlist.

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

## Configuration

The project can run without credentials using the Arbeitnow source.

For Adzuna, copy the example environment file:

```bash
cp .env.example .env
```

Then fill:

```env
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_app_key
```

Adzuna requires an `app_id` and `app_key`. Leave these empty if you only use Arbeitnow.

## Commands

Show available commands:

```bash
jobs --help
jobs fetch --help
```

Fetch and filter jobs from Arbeitnow:

```bash
jobs fetch --source arbeitnow --page 1 --min-score 10
```

Fetch and filter jobs from Adzuna:

```bash
jobs fetch --source adzuna --query "c++ simulation" --country fr --where France --min-score 10
```

You can also run the CLI as a Python module:

```bash
python -m app.cli fetch --source arbeitnow --page 1 --min-score 10
```

## Output files

Fetched jobs are saved to:

```text
data/normalized/latest_jobs.json
```

The command also prints the best matches in the terminal.

## Current filtering logic

The first version uses simple keyword scoring.

Positive signals include:

- C++
- simulation
- systems
- Linux
- embedded
- graphics
- rendering
- tooling
- infrastructure
- performance

Negative signals include:

- frontend
- React
- PHP
- WordPress
- Salesforce
- senior
- lead
- principal

This is only a cheap prefilter. The next step is to add an AI evaluator that receives only the surviving jobs and ranks them more deeply against a profile.

## Project structure

```text
app/
  cli.py
  filtering/
    rules.py
  models/
    job.py
    profile.py
    evaluation.py
  sources/
    arbeitnow.py
    adzuna.py
    france_travail.py
  storage/
    files.py
    sqlite.py
data/
  raw/
  normalized/
  ranked/
profiles/
  mathieu.json
.env.example
pyproject.toml
requirements.txt
```

## About `requirements.txt`

The main install path is now:

```bash
python -m pip install -e .
```

`requirements.txt` is kept as a simple fallback for tools or environments that still expect it:

```bash
python -m pip install -r requirements.txt
```

## Next steps

- Add AI ranking after the rule-based filter.
- Store ranked results in `data/ranked/`.
- Add deduplication by URL.
- Expand API sources.
- Add profile-aware scoring.
