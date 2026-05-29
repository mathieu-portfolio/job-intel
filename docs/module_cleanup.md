# Architecture Overview

## UI

- FastAPI
- Jinja templates
- SQLite-backed views

The web UI is the primary interface for the application.

## Workflows

### Fetch

Collects offers from supported providers and records exploration history.

### Review

Uses profile matching, scoring presets and optional AI review to rank opportunities.

## Configuration

### Profiles

Profiles define:

- fetch queries
- required terms
- weighted interests
- weighted strengths
- weighted dislikes

### Scoring Presets

Presets define:

- category weights
- scoring calibration
- thresholds

## Storage

SQLite is the source of truth for:

- offers
- reviews
- exploration tracking
- review decisions
