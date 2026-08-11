-- Run with psql against a representative database after migrations.
-- These statements verify that the Application API's busiest bounded reads use
-- indexes instead of sequential scans as the dataset grows.

EXPLAIN (ANALYZE, BUFFERS)
SELECT chat_id, title, summary, last_activity
FROM chat_sessions
WHERE is_active = true
ORDER BY last_activity DESC NULLS LAST
LIMIT 50;

EXPLAIN (ANALYZE, BUFFERS)
SELECT message_id, chat_id, role, text, timestamp
FROM chat_messages
WHERE chat_id = '00000000-0000-0000-0000-000000000000'
ORDER BY timestamp
LIMIT 100;

EXPLAIN (ANALYZE, BUFFERS)
SELECT study_uid
FROM workflow_details
WHERE task_name = 'replace-with-real-task'
LIMIT 100;

EXPLAIN (ANALYZE, BUFFERS)
SELECT study_uid, description, modality
FROM studies
WHERE description ILIKE '%brain%'
LIMIT 100;

EXPLAIN (ANALYZE, BUFFERS)
SELECT volume_series_instance_uid, series_description
FROM volume_series
WHERE series_description ILIKE '%mri%'
LIMIT 100;
