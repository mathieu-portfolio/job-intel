SELECT screening_results.*, offers.title
FROM screening_results
JOIN offers ON offers.id = screening_results.offer_id
ORDER BY screening_results.screened_at DESC, screening_results.id DESC;
