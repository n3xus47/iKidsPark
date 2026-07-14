# iKids Park - docelowa struktura bazy danych

Rekomendowany wariant produkcyjny: PostgreSQL, najlepiej Supabase, z Row Level Security dla ról aplikacyjnych. W prototypie `main.py` używa SQLite i tego samego modelu logicznego.

## Tabele

### `reservations`

Jeden rekord opisuje całą rezerwację urodzinową.

```sql
CREATE TABLE reservations (
  id BIGSERIAL PRIMARY KEY,
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  children_count INT NOT NULL CHECK (children_count > 0),
  adults_count INT NOT NULL CHECK (adults_count >= 0),
  parent_name TEXT NOT NULL,
  birthday_child_name TEXT NOT NULL,
  birthday_child_age INT NOT NULL,
  child_location TEXT NOT NULL,
  adult_location TEXT NOT NULL,
  animation_enabled BOOLEAN NOT NULL DEFAULT false,
  animation_at TIMESTAMPTZ,
  cake_enabled BOOLEAN NOT NULL DEFAULT false,
  cake_at TIMESTAMPTZ,
  fruit_enabled BOOLEAN NOT NULL DEFAULT false,
  fruit_at TIMESTAMPTZ,
  drinks_enabled BOOLEAN NOT NULL DEFAULT false,
  drinks_at TIMESTAMPTZ,
  culinary_workshops_enabled BOOLEAN NOT NULL DEFAULT false,
  culinary_workshops_at TIMESTAMPTZ,
  pinata_enabled BOOLEAN NOT NULL DEFAULT false,
  mascot_enabled BOOLEAN NOT NULL DEFAULT false,
  attraction_at TIMESTAMPTZ,
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
  cancellation_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_at > start_at),
  CHECK (status = 'active' OR length(trim(cancellation_reason)) > 0)
);
```

### `reservation_history`

Tabela append-only do audytu zmian.

```sql
CREATE TABLE reservation_history (
  id BIGSERIAL PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES reservations(id),
  action TEXT NOT NULL,
  changed_by_role TEXT NOT NULL,
  snapshot_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Indeksy i konflikt terminów

Minimalny indeks dla szybkiego sprawdzania konfliktów:

```sql
CREATE INDEX reservations_overlap_idx
  ON reservations (status, child_location, start_at, end_at);
```

Warunek konfliktu:

```sql
WHERE status = 'active'
  AND start_at < :new_end_at
  AND end_at > :new_start_at
  AND (child_location = :child_location OR adult_location = :adult_location)
```

Jeśli lokalizacje staną się osobnym słownikiem, warto dodać tabelę `locations` i zastąpić pola tekstowe `child_location_id` oraz `adult_location_id`.
