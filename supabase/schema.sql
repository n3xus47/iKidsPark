-- iKids Park — schemat PostgreSQL (Supabase)
-- Uruchom w SQL Editorze albo zostaw create_schema() w main.py przy starcie.

CREATE TABLE IF NOT EXISTS reservations (
    id BIGSERIAL PRIMARY KEY,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    children_count INTEGER NOT NULL,
    adults_count INTEGER NOT NULL,
    guest_total INTEGER,
    reservation_type TEXT NOT NULL DEFAULT 'banquet',
    parent_name TEXT NOT NULL,
    parent_phone TEXT,
    birthday_child_name TEXT NOT NULL,
    birthday_child_age INTEGER NOT NULL,
    birthday_children_json TEXT,
    child_location TEXT NOT NULL,
    adult_location TEXT NOT NULL,
    animation_enabled INTEGER NOT NULL DEFAULT 0,
    animation_type TEXT,
    animation_at TEXT,
    animations_json TEXT,
    cake_enabled INTEGER NOT NULL DEFAULT 0,
    cake_theme TEXT,
    cake_weight TEXT,
    cake_sponge TEXT,
    cake_filling TEXT,
    cake_cream TEXT,
    cake_image_data TEXT,
    cake_candle TEXT,
    cake_at TEXT,
    fruit_enabled INTEGER NOT NULL DEFAULT 0,
    fruit_plates INTEGER,
    fruit_at TEXT,
    drinks_enabled INTEGER NOT NULL DEFAULT 0,
    drinks_at TEXT,
    culinary_workshops_enabled INTEGER NOT NULL DEFAULT 0,
    culinary_workshops_type TEXT,
    culinary_workshops_at TEXT,
    pinata_enabled INTEGER NOT NULL DEFAULT 0,
    pinata_theme TEXT,
    pinata_at TEXT,
    mascot_enabled INTEGER NOT NULL DEFAULT 0,
    mascot_type TEXT,
    mascot_at TEXT,
    balloons_enabled INTEGER NOT NULL DEFAULT 0,
    balloons_description TEXT,
    balloons_at TEXT,
    attraction_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    assigned_waiter TEXT,
    assigned_animator TEXT,
    created_by TEXT,
    cooperation_enabled INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    cancellation_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reservation_history (
    id BIGSERIAL PRIMARY KEY,
    reservation_id BIGINT NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    changed_by_role TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reservations_active_time
    ON reservations(status, start_at, end_at);
CREATE INDEX IF NOT EXISTS idx_reservations_child_location_time
    ON reservations(child_location, start_at, end_at);
CREATE INDEX IF NOT EXISTS idx_reservation_history_reservation
    ON reservation_history(reservation_id, created_at);

CREATE TABLE IF NOT EXISTS inventory_items (
    id BIGSERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    ean TEXT NOT NULL DEFAULT '',
    qty_available INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_lines (
    id BIGSERIAL PRIMARY KEY,
    reservation_id BIGINT NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    item_id BIGINT REFERENCES inventory_items(id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    qty INTEGER NOT NULL,
    qty_reserved INTEGER NOT NULL DEFAULT 0,
    qty_to_order INTEGER NOT NULL DEFAULT 0,
    purchased INTEGER NOT NULL DEFAULT 0,
    issued INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    item_id BIGINT,
    line_id BIGINT,
    qty INTEGER NOT NULL DEFAULT 0,
    changed_by_role TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inventory_items_category
    ON inventory_items(category, name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_items_ean
    ON inventory_items(ean) WHERE ean != '';
CREATE INDEX IF NOT EXISTS idx_inventory_lines_reservation
    ON inventory_lines(reservation_id, cancelled);
CREATE INDEX IF NOT EXISTS idx_inventory_lines_shopping
    ON inventory_lines(cancelled, purchased, qty_to_order);

