# Operations

This page covers storage-oriented commands and behavior that are too detailed for the README quick start.

## Fetch Collection

Fetch supports two modes:

- Target mode: `--new-offers N` scans provider pages until N new relevant offers are inserted.
- Page scan mode: omit `--new-offers` and use `--pages` to scan a fixed number of pages for debugging.

Example:

```bash
python -m app.cli fetch --source arbeitnow --new-offers 20 --max-pages 10
```

Stop conditions:

- the target number of new inserted offers is collected
- `--max-pages` is reached
- the provider returns no results
- `--consecutive-seen-limit` already-explored offers are encountered in a row

Fetch only inserts offers that pass the rule filter. Already-explored items are skipped quickly by provider identity or canonical URL.

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
