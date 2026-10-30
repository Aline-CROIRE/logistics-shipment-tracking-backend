
-- Logistics Shipment Tracking Backend
-- PostgreSQL relational schema

CREATE TABLE customers (
    customer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shipments (
    shipment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id BIGINT NOT NULL
        REFERENCES customers(customer_id) ON DELETE RESTRICT,
    shipment_number VARCHAR(50) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE packages (
    package_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shipment_id BIGINT NOT NULL
        REFERENCES shipments(shipment_id) ON DELETE RESTRICT,
    tracking_id VARCHAR(100) NOT NULL UNIQUE,
    current_status VARCHAR(30) NOT NULL DEFAULT 'Created'
        CHECK (
            current_status IN (
                'Created',
                'In Transit',
                'Out for Delivery',
                'Delivered',
                'Delayed',
                'Returned'
            )
        ),
    package_metadata JSONB NOT NULL DEFAULT '{}'::JSONB
        CHECK (jsonb_typeof(package_metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE locations (
    location_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    location_name VARCHAR(150) NOT NULL,
    city VARCHAR(100) NOT NULL,
    country VARCHAR(100) NOT NULL,
    UNIQUE (location_name, city, country)
);

CREATE TABLE tracking_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    package_id BIGINT NOT NULL
        REFERENCES packages(package_id) ON DELETE RESTRICT,
    location_id BIGINT NOT NULL
        REFERENCES locations(location_id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL
        CHECK (
            status IN (
                'Created',
                'In Transit',
                'Out for Delivery',
                'Delivered',
                'Delayed',
                'Returned'
            )
        ),
    event_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scanner_id VARCHAR(100),
    scan_metadata JSONB NOT NULL DEFAULT '{}'::JSONB
        CHECK (jsonb_typeof(scan_metadata) = 'object')
);

-- The tracking-history composite index will be added
-- in the query-optimization feature after benchmarking.
