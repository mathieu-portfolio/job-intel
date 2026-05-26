# Module cleanup

This cleanup introduces focused import surfaces without changing runtime behavior.

## Storage

Existing imports from `app.storage.sqlite` still work. New code can prefer:

- `app.storage.connection`
- `app.storage.offers`
- `app.storage.exploration`
- `app.storage.scoring`
- `app.storage.reviews`
- `app.storage.maintenance`
- `app.storage.models`

The original implementation is kept in `app.storage._sqlite_impl` during this compatibility step.

## Workflows

Existing imports from `app.workflows` still work. New code can prefer:

- `app.workflow_parts.fetch`
- `app.workflow_parts.review`

The original implementation is kept in `app._workflows_impl` during this compatibility step.

## Next step

Move implementation functions from the internal modules into their focused modules in small follow-up commits, while keeping the compatibility facades stable.
