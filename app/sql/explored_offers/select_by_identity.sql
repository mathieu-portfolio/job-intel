SELECT id
FROM explored_offers
WHERE provider = ?
  AND (
    (external_id IS NOT NULL AND external_id = ?)
    OR (canonical_url IS NOT NULL AND canonical_url = ?)
  )
ORDER BY
  CASE
    WHEN external_id IS NOT NULL AND external_id = ? THEN 0
    ELSE 1
  END
LIMIT 1;
