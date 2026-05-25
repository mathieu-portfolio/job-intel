SELECT DISTINCT location
FROM offers
WHERE location IS NOT NULL
  AND TRIM(location) != ''
ORDER BY location;
