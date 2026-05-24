DELETE FROM rankings
WHERE offer_id = ?
  AND algorithm = ?
  AND model IS ?
  AND profile_path = ?;
