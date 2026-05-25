SELECT id
FROM offers
WHERE (source = ? AND source_id IS NOT NULL AND source_id = ?)
   OR url = ?
ORDER BY
  CASE
    WHEN source = ? AND source_id IS NOT NULL AND source_id = ? THEN 0
    ELSE 1
  END
LIMIT 1;
