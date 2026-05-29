# Operations

## Database Save and Load

The application can export and restore the complete SQLite state from the Settings page.

Use this before upgrades, experiments, or deployments.

## Profiles

Profiles define:

- fetch queries
- required terms
- weighted interests
- weighted strengths
- weighted dislikes

Profiles can be managed from:

```text
Settings → Profiles
```

## Scoring Presets

Presets define:

- category weights
- scoring behavior
- thresholds

Presets can be managed from:

```text
Settings → Presets
```

## Import and Export

Profiles and presets support JSON import/export from the Settings page.

## Exploration Tracking

Explored offers are tracked separately from stored offers to avoid repeatedly processing the same provider results.

Fast backfill uses exploration metadata to revisit result sets efficiently while preserving correctness through explored-offer tracking.

## Data Ownership

Profiles and presets are personal configuration.

Keep your own versions local and use the example files as templates.
