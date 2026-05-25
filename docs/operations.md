# Operations

This page covers storage-oriented commands and behavior that are too detailed for the README quick start.

## Fetch Collection

Fetch supports two modes:

- Target mode: `--new-offers N` scans provider pages until N newly explored provider offers are processed.
- Page scan mode: omit `--new-offers` and use `--pages` to scan a fixed number of pages for debugging.

Fetch also supports exploration strategies:

- `safe`: normal exploration; every provider row is checked against `explored_offers`.
- `fast_backfill`: uses per-scope metadata to process new top results, skip a previously explored range, then resume normal deduplication for older history.

Example:

```bash
python -m app.cli fetch --source arbeitnow --new-offers 20 --max-pages 10
```

Fast backfill example:

```bash
python -m app.cli fetch --source arbeitnow --new-offers 20 --max-pages 10 --exploration-mode fast_backfill
```

Stop conditions:

- the target number of newly explored offers is processed
- `--max-pages` is reached
- the provider returns no results
- `--max-seen-pages` consecutive pages contain only already-explored offers

Fetch only inserts offers that pass the rule filter. Already-explored items are skipped quickly by provider identity or canonical URL. Filtered-out offers still count as newly explored and are recorded in explored tracking.

## Explored Offers

The `explored_offers` table records provider items even when they are not inserted into `offers`.

Tracked fields include:

- provider/source name
- external provider ID when available
- canonical URL when available
- first and last seen timestamps
- status such as `duplicate`, `filtered_out`, `inserted`, `updated`, or `error`
- optional reason such as `already_seen`, `missing_description`, or `rule_filter_failed`

This prevents repeated parsing/filtering of the same irrelevant or duplicate provider results.

## Exploration Scope Metadata

Fast backfill stores minimal metadata in `exploration_scopes`, keyed by a stable hash of the source, profile, query, filters, and other parameters that define a result set.

Tracked fields:

- `newest_id`
- `oldest_id`
- `last_explored_page`
- `updated_at`

This metadata is an optimization only. `explored_offers` remains the correctness layer for deduplication. If metadata is missing or incomplete, fast backfill falls back to normal exploration.

Clearing explored tracking also clears exploration scope metadata so stale skip ranges are not reused.

## Scoring Profiles

Candidate-specific scoring terms live in profile JSON files. The scorer reads `signals` categories:

```json
{
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

Category scores are normalized by total item weight:

```text
matched item weights / total item weights
```

Then category contribution is:

```text
category score * category weight
```

Category weights come from scoring presets or `config/rule_weights.example.json`; profile files own only item terms and item weights.

The legacy profile fields (`interests`, `preferred_domains`, `positive_signals`, `negative_signals`, and similar) and the previous `{ "weight": ..., "items": [...] }` signal-category shape are still accepted and converted at load time, but new profiles should use `signals` as category-to-item-list mappings.

`config/rule_weights.example.json` contains generic category-weight and score-calibration settings. User-specific terms should stay in profile JSON.

## Automatic Pruning

Fetch runs storage pruning after each successful fetch. Defaults:

- explored offers: `10000`
- unranked offers: `1000`
- ranked offers: `300`

Override from the CLI:

```bash
python -m app.cli fetch \
  --new-offers 20 \
  --explored-capacity 10000 \
  --unranked-capacity 1000 \
  --ranked-capacity 300
```

Deletion priority:

1. unmarked rows first
2. unranked rows before ranked rows where that distinction applies
3. oldest rows first within each priority bucket

For `offers`, `review_status = 'new'` is unmarked. Other statuses such as `saved`, `skipped`, and `applied` are treated as user-marked and are preserved until unmarked candidates are exhausted. Explored offers have a minimal `keep_flag` for the same purpose.

The UI also shows current counts and capacities and has a confirmed manual cleanup action.

## Clear Command

Use `clear` for explicit data removal:

```bash
python -m app.cli clear --scope rankings --db data/job_intel.sqlite
```

Supported scopes:

- `rankings`: clears ranking rows only
- `offers`: clears offers and dependent rankings through foreign-key cascade
- `explored`: clears only explored-offer tracking
- `all`: clears explored offers, offers, rankings, and ranking run metadata

The command validates the scope, prints the exact affected counts, and requires confirmation unless `--yes` is provided:

```bash
python -m app.cli clear --scope all --db data/job_intel.sqlite --yes
```
