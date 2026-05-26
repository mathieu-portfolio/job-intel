from __future__ import annotations

from app.storage._common import *  # noqa: F401,F403

def _select_ids_to_delete(
    connection: sqlite3.Connection,
    sql_name: str,
    count: int,
) -> list[int]:
    if count <= 0:
        return []
    rows = connection.execute(load_sql(sql_name), (count,)).fetchall()
    return [int(row["id"]) for row in rows]


def _delete_ids(
    connection: sqlite3.Connection,
    sql_name: str,
    ids: list[int],
) -> int:
    if not ids:
        return 0
    placeholders = ", ".join(["?"] * len(ids))
    sql = load_sql(sql_name).replace("/*IDS*/", placeholders)
    cursor = connection.execute(sql, ids)
    return int(cursor.rowcount if cursor.rowcount is not None else len(ids))


def get_storage_counts(db_path: Path = DEFAULT_DB_PATH) -> StorageCounts:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(load_sql("pruning/counts.sql")).fetchone()
    return StorageCounts(
        explored=int(row["explored_count"]),
        unranked=int(row["unranked_count"]),
        ranked=int(row["ranked_count"]),
    )


def prune_storage(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    explored_capacity: int = DEFAULT_EXPLORED_CAPACITY,
    unranked_capacity: int = DEFAULT_UNRANKED_CAPACITY,
    ranked_capacity: int = DEFAULT_RANKED_CAPACITY,
) -> PruneStats:
    if explored_capacity < 0 or unranked_capacity < 0 or ranked_capacity < 0:
        raise ValueError("Storage capacities must be zero or greater.")

    init_db(db_path)
    with _connect(db_path) as connection:
        before_row = connection.execute(load_sql("pruning/counts.sql")).fetchone()
        before = StorageCounts(
            explored=int(before_row["explored_count"]),
            unranked=int(before_row["unranked_count"]),
            ranked=int(before_row["ranked_count"]),
        )

        explored_overage = max(0, before.explored - explored_capacity)
        explored_ids = _select_ids_to_delete(
            connection,
            "pruning/select_explored_to_delete.sql",
            explored_overage,
        )
        deleted_explored = _delete_ids(
            connection,
            "pruning/delete_explored_by_ids.sql",
            explored_ids,
        )

        unranked_overage = max(0, before.unranked - unranked_capacity)
        unranked_offer_ids = _select_ids_to_delete(
            connection,
            "pruning/select_unranked_offers_to_delete.sql",
            unranked_overage,
        )
        deleted_unranked = _delete_ids(
            connection,
            "pruning/delete_offers_by_ids.sql",
            unranked_offer_ids,
        )

        ranked_overage = max(0, before.ranked - ranked_capacity)
        ranked_offer_ids = _select_ids_to_delete(
            connection,
            "pruning/select_ranked_offers_to_delete.sql",
            ranked_overage,
        )
        deleted_ranked = _delete_ids(
            connection,
            "pruning/delete_offers_by_ids.sql",
            ranked_offer_ids,
        )
        connection.execute(load_sql("pruning/delete_orphaned_ranking_runs.sql"))

        after_row = connection.execute(load_sql("pruning/counts.sql")).fetchone()
        after = StorageCounts(
            explored=int(after_row["explored_count"]),
            unranked=int(after_row["unranked_count"]),
            ranked=int(after_row["ranked_count"]),
        )

    return PruneStats(
        deleted_explored=deleted_explored,
        deleted_unranked=deleted_unranked,
        deleted_ranked=deleted_ranked,
        before=before,
        after=after,
    )


def _validate_clear_scope(scope: str) -> ClearScope:
    if scope not in VALID_CLEAR_SCOPES:
        valid = ", ".join(sorted(VALID_CLEAR_SCOPES))
        raise ValueError(f"Unsupported clear scope: {scope}. Expected one of: {valid}.")
    return scope  # type: ignore[return-value]


def get_clear_plan(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    scope: str,
) -> ClearPlan:
    clear_scope = _validate_clear_scope(scope)
    init_db(db_path)
    with _connect(db_path) as connection:
        if clear_scope == "rankings":
            row = connection.execute(load_sql("clear/count_rankings.sql")).fetchone()
            return ClearPlan(scope=clear_scope, rankings=int(row["rankings_count"]))
        if clear_scope == "offers":
            row = connection.execute(load_sql("clear/count_offers.sql")).fetchone()
            return ClearPlan(
                scope=clear_scope,
                offers=int(row["offers_count"]),
                rankings=int(row["dependent_rankings_count"]),
            )
        if clear_scope == "explored":
            row = connection.execute(load_sql("clear/count_explored.sql")).fetchone()
            return ClearPlan(scope=clear_scope, explored=int(row["explored_count"]))

        row = connection.execute(load_sql("clear/count_all.sql")).fetchone()
        return ClearPlan(
            scope=clear_scope,
            explored=int(row["explored_count"]),
            offers=int(row["offers_count"]),
            rankings=int(row["rankings_count"]),
            ranking_runs=int(row["ranking_runs_count"]),
        )


def clear_data(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    scope: str,
) -> ClearPlan:
    plan = get_clear_plan(db_path=db_path, scope=scope)
    init_db(db_path)
    with _connect(db_path) as connection:
        if plan.scope == "rankings":
            connection.execute(load_sql("clear/delete_ai_reviews.sql"))
            connection.execute(load_sql("clear/delete_rankings.sql"))
        elif plan.scope == "offers":
            connection.execute(load_sql("clear/delete_offers.sql"))
        elif plan.scope == "explored":
            connection.execute(load_sql("clear/delete_explored.sql"))
            connection.execute("DELETE FROM exploration_scopes;")
        elif plan.scope == "all":
            connection.execute(load_sql("clear/delete_explored.sql"))
            connection.execute("DELETE FROM exploration_scopes;")
            connection.execute(load_sql("clear/delete_ai_reviews.sql"))
            connection.execute(load_sql("clear/delete_rankings.sql"))
            connection.execute(load_sql("clear/delete_offers.sql"))
            connection.execute(load_sql("clear/delete_ranking_runs.sql"))
        else:
            raise ValueError(f"Unsupported clear scope: {plan.scope}")
    return plan


def clear_rankings(db_path: Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        connection.execute(load_sql("rankings/delete_all.sql"))
