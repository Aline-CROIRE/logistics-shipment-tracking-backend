
-- Query optimization indexes.
-- Apply after collecting the baseline benchmark.

CREATE INDEX IF NOT EXISTS idx_tracking_events_package_timestamp
    ON tracking_events (package_id, event_timestamp);

-- Refresh planner statistics after creating the index.
ANALYZE tracking_events;
