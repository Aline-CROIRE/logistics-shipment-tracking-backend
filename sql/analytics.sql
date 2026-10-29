
-- Logistics Shipment Tracking Analytics
-- Run against the logistics PostgreSQL database.

-- QUERY 1: Tracking history for one package.
-- The CTE combines tracking events with package and location details.

WITH package_history AS (
    SELECT
        p.package_id,
        p.tracking_id,
        event.event_id,
        event.status,
        event.event_timestamp,
        event.scanner_id,
        location.location_name,
        location.city,
        location.country
    FROM packages AS p
    JOIN tracking_events AS event
        ON event.package_id = p.package_id
    JOIN locations AS location
        ON location.location_id = event.location_id
    WHERE p.tracking_id = 'TRK-1001'
)
SELECT
    tracking_id,
    status,
    event_timestamp,
    location_name,
    city,
    country,
    scanner_id
FROM package_history
ORDER BY event_timestamp, event_id;


-- QUERY 2: Calculate elapsed time between consecutive scans.
-- LAG() retrieves the previous event timestamp for the same package.
-- The first event has no previous timestamp, so elapsed time is NULL.

WITH package_history AS (
    SELECT
        p.package_id,
        p.tracking_id,
        event.event_id,
        event.status,
        event.event_timestamp,
        location.city,
        location.country
    FROM packages AS p
    JOIN tracking_events AS event
        ON event.package_id = p.package_id
    JOIN locations AS location
        ON location.location_id = event.location_id
),
scan_intervals AS (
    SELECT
        package_id,
        tracking_id,
        event_id,
        status,
        event_timestamp,
        city,
        country,
        LAG(event_timestamp) OVER (
            PARTITION BY package_id
            ORDER BY event_timestamp, event_id
        ) AS previous_event_timestamp
    FROM package_history
)
SELECT
    tracking_id,
    status,
    city,
    country,
    event_timestamp,
    previous_event_timestamp,
    ROUND(
        (
            EXTRACT(
                EPOCH FROM (
                    event_timestamp - previous_event_timestamp
                )
            ) / 60
        )::numeric,
        2
    ) AS minutes_since_previous_scan
FROM scan_intervals
ORDER BY tracking_id, event_timestamp, event_id;


-- QUERY 3: Find the latest tracking event for every package.
-- ROW_NUMBER() ranks each package's events newest first.

WITH ranked_events AS (
    SELECT
        p.tracking_id,
        p.current_status,
        event.event_id,
        event.status AS event_status,
        event.event_timestamp,
        location.location_name,
        location.city,
        location.country,
        ROW_NUMBER() OVER (
            PARTITION BY p.package_id
            ORDER BY event.event_timestamp DESC, event.event_id DESC
        ) AS event_rank
    FROM packages AS p
    LEFT JOIN tracking_events AS event
        ON event.package_id = p.package_id
    LEFT JOIN locations AS location
        ON location.location_id = event.location_id
)
SELECT
    tracking_id,
    current_status,
    event_status,
    event_timestamp,
    location_name,
    city,
    country
FROM ranked_events
WHERE event_rank = 1
ORDER BY tracking_id;
