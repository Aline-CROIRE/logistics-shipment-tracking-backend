
-- Sample logistics data for development and query testing.

INSERT INTO customers (name, email)
VALUES
    ('Aline Niyonizera', 'aline@example.com'),
    ('Patrick Uwimana', 'patrick@example.com')
ON CONFLICT (email) DO NOTHING;

INSERT INTO shipments (customer_id, shipment_number)
SELECT c.customer_id, s.shipment_number
FROM (
    VALUES
        ('aline@example.com', 'SHP-2026-001'),
        ('aline@example.com', 'SHP-2026-002'),
        ('patrick@example.com', 'SHP-2026-003')
) AS s(email, shipment_number)
JOIN customers c ON c.email = s.email
ON CONFLICT (shipment_number) DO NOTHING;

INSERT INTO packages (
    shipment_id, tracking_id, current_status, package_metadata
)
SELECT s.shipment_id, p.tracking_id, p.current_status, p.package_metadata::JSONB
FROM (
    VALUES
        ('SHP-2026-001', 'TRK-1001', 'In Transit',
         '{"weight_kg": 2.5, "fragile": true}'),
        ('SHP-2026-001', 'TRK-1002', 'Created',
         '{"weight_kg": 1.2, "fragile": false}'),
        ('SHP-2026-002', 'TRK-1003', 'Out for Delivery',
         '{"weight_kg": 4.0, "fragile": false}'),
        ('SHP-2026-003', 'TRK-1004', 'Delivered',
         '{"weight_kg": 0.8, "fragile": true}')
) AS p(shipment_number, tracking_id, current_status, package_metadata)
JOIN shipments s ON s.shipment_number = p.shipment_number
ON CONFLICT (tracking_id) DO NOTHING;

INSERT INTO locations (location_name, city, country)
VALUES
    ('Kigali Distribution Centre', 'Kigali', 'Rwanda'),
    ('Huye Transit Hub', 'Huye', 'Rwanda'),
    ('Musanze Delivery Hub', 'Musanze', 'Rwanda')
ON CONFLICT (location_name, city, country) DO NOTHING;

INSERT INTO tracking_events (
    package_id, location_id, status, event_timestamp, scanner_id, scan_metadata
)
SELECT
    p.package_id,
    l.location_id,
    e.status,
    e.event_timestamp::TIMESTAMPTZ,
    e.scanner_id,
    e.scan_metadata::JSONB
FROM (
    VALUES
        ('TRK-1001', 'Kigali Distribution Centre', 'In Transit',
         '2026-10-27 08:00:00+02', 'SCANNER-KGL-01',
         '{"source": "dispatch"}'),
        ('TRK-1001', 'Huye Transit Hub', 'In Transit',
         '2026-10-27 11:30:00+02', 'SCANNER-HUY-01',
         '{"source": "transit"}'),
        ('TRK-1002', 'Kigali Distribution Centre', 'Created',
         '2026-10-27 09:00:00+02', 'SCANNER-KGL-01',
         '{"source": "registration"}'),
        ('TRK-1003', 'Musanze Delivery Hub', 'Out for Delivery',
         '2026-10-27 13:00:00+02', 'SCANNER-MUS-01',
         '{"source": "delivery"}'),
        ('TRK-1004', 'Huye Transit Hub', 'In Transit',
         '2026-10-26 10:00:00+02', 'SCANNER-HUY-01',
         '{"source": "transit"}'),
        ('TRK-1004', 'Kigali Distribution Centre', 'Delivered',
         '2026-10-27 15:00:00+02', 'SCANNER-KGL-02',
         '{"recipient_confirmed": true}')
) AS e(tracking_id, location_name, status, event_timestamp, scanner_id, scan_metadata)
JOIN packages p ON p.tracking_id = e.tracking_id
JOIN locations l ON l.location_name = e.location_name
WHERE NOT EXISTS (
    SELECT 1
    FROM tracking_events existing
    WHERE existing.package_id = p.package_id
      AND existing.location_id = l.location_id
      AND existing.status = e.status
      AND existing.event_timestamp = e.event_timestamp::TIMESTAMPTZ
);
