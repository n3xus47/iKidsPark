from __future__ import annotations

import csv
import errno
import html
import io
import json
import sqlite3
from datetime import date, datetime, time, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


APP_TITLE = "iKids Park - Rezerwacje urodzin"
DB_PATH = Path(__file__).with_name("reservations.db")
LOGO_PATH = Path(__file__).with_name("logo ikids.png")
HOST = "127.0.0.1"
PORT = 8000

PARTY_ROOMS = [
    "Loża 1 - Biały Dom",
    "Loża 2 - Magiczny Las",
    "Loża 3 - Wróżki",
    "Loża 4 - Kosmos",
    "Loża 5 - Zima",
    "Loża 6 - Football",
]

TABLE_NUMBERS = tuple(range(7, 78))
TABLE_GROUP_NUMBERS = {
    "Bar": [7, 8, 9, 10, 11, 12, 13, 14],
    "Scena": [18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40],
    "Trójkąt": [41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57],
    "Labirynt": [58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 71, 72, 73, 74],
    "Pozostałe stoliki": [15, 16, 17, 70, 75, 76, 77],
}
TABLE_ZONE_BY_NUMBER = {
    number: area
    for area, numbers in TABLE_GROUP_NUMBERS.items()
    for number in numbers
}
ADULT_LOCATION_GROUPS = {
    area: [f"{area} - Stolik {number}" for number in numbers]
    for area, numbers in TABLE_GROUP_NUMBERS.items()
}
ADULT_LOCATIONS = [location for locations in ADULT_LOCATION_GROUPS.values() for location in locations]
LOCATION_GROUPS = {"Loże tematyczne": PARTY_ROOMS, **ADULT_LOCATION_GROUPS}

ALL_LOCATIONS = PARTY_ROOMS + ADULT_LOCATIONS
LOCATION_SEPARATOR = " | "

ANIMATION_GROUPS = {
    "Animacje tematyczne": [
        "Impreza Hawajska",
        "Ekipa Małych Ratowników",
        "Wirtualny Świat Gier",
        "Piłka Nożna",
        "Królestwo Zimowego Czaru",
        "Laboratorium Naukowe",
        "Bańkowe Widowisko",
        "Zabawy na Trampolinach",
        "Dyskoteka",
        "Dla Najmłodszych",
    ],
    "Misje specjalne (samodzielne przejście z podpowiedziami)": [
        "Tajemnice Szkoły Magii",
        "Piracka Przygoda",
        "Zagadki Pradawnego Lasu",
        "Wyzwanie Detektywistyczne",
        "Superbohaterowie - Era Najodważniejszych",
    ],
}

ANIMATION_TYPES = [name for names in ANIMATION_GROUPS.values() for name in names]
WORKSHOP_TYPES = ["Pizza", "Burger", "Piernik", "Shake"]
MASCOT_TYPES = ["Lew", "Pan Królik", "Pani Królik", "Miś"]

ROOM_LAYOUT = [
    ("Loża 1 - Biały Dom", 92, 410, 86, 48),
    ("Loża 2 - Magiczny Las", 184, 410, 86, 48),
    ("Loża 3 - Wróżki", 276, 410, 86, 48),
    ("Loża 4 - Kosmos", 368, 410, 86, 48),
    ("Loża 5 - Zima", 460, 410, 86, 48),
    ("Loża 6 - Football", 552, 410, 86, 48),
]

ADULT_ZONE_LAYOUT = [
    ("Bar", "7-14", 40, 258, 210, 72),
    ("Scena", "18-40", 280, 154, 312, 176),
    ("Trójkąt", "41-57", 628, 82, 244, 248),
    ("Labirynt", "58-74", 740, 338, 168, 188),
]

TABLE_LAYOUT = [
    (13, 68, 276), (14, 68, 306),
    (7, 130, 278), (8, 166, 278), (9, 202, 278),
    (10, 130, 310), (11, 166, 310), (12, 202, 310),
    (30, 364, 174), (31, 430, 174), (32, 496, 174),
    (27, 374, 212), (28, 430, 212), (29, 486, 212),
    (23, 340, 246), (24, 390, 246), (25, 440, 246), (26, 490, 246),
    (21, 374, 280), (22, 424, 280),
    (33, 520, 234), (34, 558, 234), (35, 558, 270),
    (18, 332, 306), (19, 382, 306), (20, 432, 306),
    (36, 492, 306), (37, 528, 306), (38, 564, 306), (39, 528, 334), (40, 564, 334),
    (52, 732, 110), (53, 764, 136), (54, 796, 164), (55, 828, 194),
    (47, 692, 150), (48, 730, 180), (49, 768, 210), (50, 806, 240), (51, 844, 270),
    (41, 684, 292), (42, 720, 292), (43, 756, 292), (44, 792, 292), (45, 828, 292), (46, 864, 292),
    (56, 858, 332), (57, 890, 332),
    (58, 744, 368), (59, 744, 402), (60, 744, 436), (61, 744, 470),
    (62, 816, 368), (63, 858, 368), (64, 816, 402), (65, 858, 402),
    (66, 816, 436), (67, 858, 436), (68, 816, 470), (69, 858, 470),
    (71, 782, 488), (72, 782, 520), (73, 836, 488), (74, 836, 520),
]

LEGACY_CHILD_LOCATION_RENAMES = {
    "Salka Piłka Nożna": "Loża 6 - Football",
    "Salka Dżungla": "Loża 2 - Magiczny Las",
    "Salka Kosmos": "Loża 4 - Kosmos",
    "Salka Księżniczki": "Loża 3 - Wróżki",
    "Salka Piraci": "Loża 1 - Biały Dom",
    "Salka Kreatywna": "Loża 5 - Zima",
    "Sala 1 - Biały Dom": "Loża 1 - Biały Dom",
    "Sala 2 - Magiczny Las": "Loża 2 - Magiczny Las",
    "Sala 3 - Wróżki": "Loża 3 - Wróżki",
    "Sala 4 - Kosmos": "Loża 4 - Kosmos",
    "Sala 5 - Zima": "Loża 5 - Zima",
    "Sala 6 - Piłka nożna": "Loża 6 - Football",
}

LEGACY_ADULT_LOCATION_RENAMES = {
    "Antresola - Stolik A": "Bar - Stolik 7",
    "Antresola - Stolik B": "Bar - Stolik 8",
    **{
        f"Sala główna - Stolik {number}": f"{TABLE_ZONE_BY_NUMBER[number]} - Stolik {number}"
        for number in TABLE_NUMBERS
    },
}

ROLE_DEFS = {
    "manager": {
        "label": "Kierownik zmiany / Recepcja",
        "hint": "Pełny widok, edycja, statusy i dostępność sal.",
    },
    "animators": {
        "label": "Animatorzy",
        "hint": "Animacje, piniaty, maskotki.",
    },
    "kitchen": {
        "label": "Kuchnia",
        "hint": "Owoce, torty i warsztaty.",
    },
    "organizer": {
        "label": "Organizator urodzin",
        "hint": "Podgląd bankietów, lokalizacji i dodatków.",
    },
}

DAY_FILTERS = {
    "today": ("Dziś", 0),
    "tomorrow": ("Jutro", 1),
    "after_tomorrow": ("Pojutrze", 2),
}

STATUS_LABELS = {
    "active": "Aktywna",
    "cancelled": "Anulowana",
}

STAGE_BLOCK_MESSAGE = "Ta atrakcja nachodzi na Koło Marzeń 17:45-18:15 - wybierz inną godzinę startu."
STAGE_BLOCK_START = time(17, 45)
STAGE_BLOCK_END = time(18, 15)
SERVICE_DURATIONS = {
    "animation_at": 60,
    "cake_at": 20,
    "culinary_workshops_at": 60,
    "pinata_at": 20,
    "mascot_at": 20,
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            children_count INTEGER NOT NULL,
            adults_count INTEGER NOT NULL,
            parent_name TEXT NOT NULL,
            birthday_child_name TEXT NOT NULL,
            birthday_child_age INTEGER NOT NULL,
            child_location TEXT NOT NULL,
            adult_location TEXT NOT NULL,
            animation_enabled INTEGER NOT NULL DEFAULT 0,
            animation_type TEXT,
            animation_at TEXT,
            cake_enabled INTEGER NOT NULL DEFAULT 0,
            cake_theme TEXT,
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
            attraction_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
            cancellation_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reservation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            changed_by_role TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (reservation_id) REFERENCES reservations(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reservations_active_time
        ON reservations(status, start_at, end_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reservations_child_location_time
        ON reservations(child_location, start_at, end_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reservation_history_reservation
        ON reservation_history(reservation_id, created_at)
        """
    )


def migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "reservations")
    if not columns or "start_at" in columns:
        return

    backup_name = "reservations_legacy_backup_" + datetime.now().strftime("%Y%m%d%H%M%S")
    conn.execute(f"ALTER TABLE reservations RENAME TO {backup_name}")
    create_schema(conn)

    legacy_rows = conn.execute(f"SELECT * FROM {backup_name}").fetchall()
    legacy_room_map = {
        "Dżungla": "Loża 2 - Magiczny Las",
        "Kosmos": "Loża 4 - Kosmos",
        "Księżniczki": "Loża 3 - Wróżki",
        "Piraci": "Loża 1 - Biały Dom",
        "Superbohaterowie": "Loża 6 - Football",
        "Sala kreatywna": "Loża 5 - Zima",
    }

    for row in legacy_rows:
        row_keys = set(row.keys())
        reservation_day = row["reservation_date"] if "reservation_date" in row_keys else date.today().isoformat()
        start_at = f"{reservation_day}T10:00"
        end_at = f"{reservation_day}T12:00"
        created_at = row["created_at"] if "created_at" in row_keys else now_iso()
        theme_room = row["theme_room"] if "theme_room" in row_keys else PARTY_ROOMS[0]
        child_location = legacy_room_map.get(theme_room, PARTY_ROOMS[0])

        conn.execute(
            """
            INSERT INTO reservations (
                start_at, end_at, children_count, adults_count, parent_name,
                birthday_child_name, birthday_child_age, child_location, adult_location,
                animation_enabled, cake_enabled, fruit_enabled, culinary_workshops_enabled,
                notes, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                start_at,
                end_at,
                row["children_count"] if "children_count" in row_keys else 1,
                row["adults_count"] if "adults_count" in row_keys else 0,
                row["parent_name"] if "parent_name" in row_keys else "Migracja danych",
                row["child_name"] if "child_name" in row_keys else "Solenizant",
                row["child_age"] if "child_age" in row_keys else 6,
                child_location,
                ADULT_LOCATIONS[0],
                1 if "animations" in row_keys and row["animations"] == "Tak" else 0,
                1 if "cake" in row_keys and row["cake"] == "Tak" else 0,
                1 if "fruit" in row_keys and row["fruit"] == "Tak" else 0,
                1 if "workshops" in row_keys and row["workshops"] == "Tak" else 0,
                row["notes"] if "notes" in row_keys else "",
                created_at,
                now_iso(),
            ),
        )


def migrate_location_names(conn: sqlite3.Connection) -> None:
    for old_name, new_name in LEGACY_CHILD_LOCATION_RENAMES.items():
        conn.execute(
            "UPDATE reservations SET child_location = ? WHERE child_location = ?",
            (new_name, old_name),
        )
    for old_name, new_name in LEGACY_ADULT_LOCATION_RENAMES.items():
        conn.execute(
            "UPDATE reservations SET adult_location = ? WHERE adult_location = ?",
            (new_name, old_name),
        )


def ensure_current_schema(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "reservations")
    if "animation_type" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN animation_type TEXT")
    if "cake_theme" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN cake_theme TEXT")
    if "fruit_plates" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN fruit_plates INTEGER")
    if "culinary_workshops_type" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN culinary_workshops_type TEXT")
    if "pinata_theme" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN pinata_theme TEXT")
    if "pinata_at" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN pinata_at TEXT")
    if "mascot_type" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN mascot_type TEXT")
    if "mascot_at" not in columns:
        conn.execute("ALTER TABLE reservations ADD COLUMN mascot_at TEXT")

    conn.execute(
        """
        UPDATE reservations
        SET pinata_at = attraction_at
        WHERE pinata_enabled = 1
          AND pinata_at IS NULL
          AND attraction_at IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE reservations
        SET mascot_at = attraction_at
        WHERE mascot_enabled = 1
          AND mascot_at IS NULL
          AND attraction_at IS NOT NULL
        """
    )


def init_db() -> None:
    with connect() as conn:
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reservations'"
        ).fetchone()
        if existing:
            migrate_legacy_schema(conn)
        create_schema(conn)
        ensure_current_schema(conn)
        migrate_location_names(conn)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db_rows(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def db_one(query: str, params: tuple = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(query, params).fetchone()


def execute(query: str, params: tuple = ()) -> int:
    with connect() as conn:
        cursor = conn.execute(query, params)
        return int(cursor.lastrowid or 0)


def escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def normalize_role(role: str | None) -> str:
    return role if role in ROLE_DEFS else "manager"


def normalize_day(day: str | None) -> str:
    return day if day in DAY_FILTERS else "today"


def selected_day(day_key: str) -> date:
    _, offset = DAY_FILTERS[normalize_day(day_key)]
    return date.today() + timedelta(days=offset)


def day_bounds(target_day: date) -> tuple[str, str]:
    start = datetime.combine(target_day, time.min).isoformat(timespec="minutes")
    end = datetime.combine(target_day + timedelta(days=1), time.min).isoformat(timespec="minutes")
    return start, end


def parse_date(value: str, errors: dict[str, str], field: str, label: str) -> date | None:
    raw = value.strip()
    if not raw:
        errors[field] = f"Pole \"{label}\" jest wymagane."
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        errors[field] = f"Pole \"{label}\" ma niepoprawny format."
        return None


def parse_time_value(raw: str) -> time | None:
    value = raw.strip().replace(".", ":")
    if not value:
        return None
    if value.isdigit():
        if len(value) <= 2:
            hours = int(value)
            minutes = 0
        elif len(value) in (3, 4):
            hours = int(value[:-2])
            minutes = int(value[-2:])
        else:
            return None
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            return time(hours, minutes)
        return None
    if ":" in value:
        parts = value.split(":", 1)
        if len(parts) == 2 and parts[0].isdigit() and (parts[1].isdigit() or parts[1] == ""):
            hours = int(parts[0])
            minutes = int(parts[1] or "0")
            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                return time(hours, minutes)
            return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def parse_time_field(
    data: dict[str, object],
    errors: dict[str, str],
    field: str,
    label: str,
    required: bool = False,
) -> time | None:
    raw = data.get(field, "").strip()
    if not raw:
        if required:
            errors[field] = f"Pole \"{label}\" jest wymagane."
        return None
    parsed = parse_time_value(raw)
    if parsed is None:
        errors[field] = f"Pole \"{label}\" ma niepoprawną godzinę."
    return parsed


def parse_int_field(
    data: dict[str, object],
    errors: dict[str, str],
    field: str,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    raw = data.get(field, "").strip()
    try:
        value = int(raw)
    except ValueError:
        errors[field] = f"Pole \"{label}\" musi być liczbą."
        return minimum
    if value < minimum or value > maximum:
        errors[field] = f"Pole \"{label}\" musi być w zakresie {minimum}-{maximum}."
    return value


def parse_text_field(data: dict[str, object], errors: dict[str, str], field: str, label: str) -> str:
    value = data.get(field, "").strip()
    if not value:
        errors[field] = f"Pole \"{label}\" jest wymagane."
    return value


def checked_bool(data: dict[str, object], field: str) -> int:
    return 1 if data.get(field) == "1" else 0


def overlaps_stage_block(value: time | None, duration_minutes: int | None) -> bool:
    if value is None:
        return False
    duration = duration_minutes or 0
    service_start = datetime.combine(date.today(), value)
    service_end = service_start + timedelta(minutes=duration)
    block_start = datetime.combine(date.today(), STAGE_BLOCK_START)
    block_end = datetime.combine(date.today(), STAGE_BLOCK_END)
    return service_start < block_end and service_end > block_start


def combine_day_time(day: date | None, value: time | None) -> str | None:
    if day is None or value is None:
        return None
    return datetime.combine(day, value).isoformat(timespec="minutes")


def location_values(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        candidates = [str(item).strip() for item in value]
    else:
        raw = str(value or "").strip()
        candidates = [part.strip() for part in raw.split(LOCATION_SEPARATOR)] if raw else []

    locations: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in locations:
            locations.append(candidate)
    return locations


def joined_locations(values: object) -> str:
    return LOCATION_SEPARATOR.join(location_values(values))


def display_locations(value: object) -> str:
    return ", ".join(location_values(value))


def reservation_locations(row: sqlite3.Row | dict[str, object]) -> set[str]:
    return {str(row["child_location"]), *location_values(row["adult_location"])}


def time_in_reservation_window(start_dt: datetime | None, end_dt: datetime | None, value: time | None) -> bool:
    if start_dt is None or end_dt is None or value is None:
        return True
    candidate = datetime.combine(start_dt.date(), value)
    return start_dt <= candidate <= end_dt


def find_conflicts(
    start_at: str,
    end_at: str,
    child_location: str,
    adult_locations: list[str],
    exclude_id: int | None = None,
) -> list[sqlite3.Row]:
    params: list[object] = [end_at, start_at]
    exclude_sql = ""
    if exclude_id:
        exclude_sql = "AND id != ?"
        params.append(exclude_id)

    requested_locations = {child_location, *adult_locations}
    rows = db_rows(
        f"""
        SELECT id, start_at, end_at, parent_name, birthday_child_name, child_location, adult_location
        FROM reservations
        WHERE status = 'active'
          AND start_at < ?
          AND end_at > ?
          {exclude_sql}
        ORDER BY start_at ASC
        """,
        tuple(params),
    )
    return [row for row in rows if requested_locations & reservation_locations(row)]


def validate_reservation(
    data: dict[str, object],
    reservation_id: int | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    errors: dict[str, str] = {}
    reservation_day = parse_date(data.get("reservation_date", ""), errors, "reservation_date", "Data")
    party_start_time = parse_time_field(data, errors, "party_start_time", "Godzina startu imprezy", required=True)
    start_at = combine_day_time(reservation_day, party_start_time) or ""
    end_at = (
        datetime.combine(reservation_day + timedelta(days=1), time.min).isoformat(timespec="minutes")
        if reservation_day
        else ""
    )

    child_location = data.get("child_location", "").strip()
    if child_location not in ALL_LOCATIONS:
        errors["child_location"] = "Wybierz lokalizację dzieci z listy."

    adult_locations = location_values(data.get("adult_location", ""))
    invalid_adult_locations = [location for location in adult_locations if location not in ALL_LOCATIONS]
    if not adult_locations:
        errors["adult_location"] = "Wybierz lokalizację dorosłych z listy."
    elif invalid_adult_locations:
        errors["adult_location"] = "Wybierz lokalizacje dorosłych z listy."
    adult_location = joined_locations(adult_locations)

    animation_enabled = checked_bool(data, "animation_enabled")
    cake_enabled = checked_bool(data, "cake_enabled")
    fruit_enabled = checked_bool(data, "fruit_enabled")
    drinks_enabled = 0
    culinary_workshops_enabled = checked_bool(data, "culinary_workshops_enabled")
    pinata_enabled = checked_bool(data, "pinata_enabled")
    mascot_enabled = checked_bool(data, "mascot_enabled")

    animation_type = data.get("animation_type", "").strip()
    if animation_enabled and animation_type not in ANIMATION_TYPES:
        errors["animation_type"] = "Wybierz animację z listy."
    if not animation_enabled:
        animation_type = ""

    fruit_plates = 0
    if fruit_enabled:
        fruit_plates = parse_int_field(data, errors, "fruit_plates", "Liczba talerzy owoców", 1, 200)

    cake_theme = data.get("cake_theme", "").strip()
    if cake_enabled and not cake_theme:
        cake_theme = "(brak)"
    if not cake_enabled:
        cake_theme = ""

    workshops_type = data.get("culinary_workshops_type", "").strip()
    if culinary_workshops_enabled and workshops_type not in WORKSHOP_TYPES:
        errors["culinary_workshops_type"] = "Wybierz rodzaj warsztatów."
    if not culinary_workshops_enabled:
        workshops_type = ""

    pinata_theme = data.get("pinata_theme", "").strip()
    if pinata_enabled and not pinata_theme:
        pinata_theme = "(brak)"
    if not pinata_enabled:
        pinata_theme = ""

    mascot_type = data.get("mascot_type", "").strip()
    if mascot_enabled and mascot_type not in MASCOT_TYPES:
        errors["mascot_type"] = "Wybierz maskotkę."
    if not mascot_enabled:
        mascot_type = ""

    animation_time = parse_time_field(data, errors, "animation_at", "Start animacji", bool(animation_enabled))
    cake_time = parse_time_field(data, errors, "cake_at", "Start tortu", bool(cake_enabled))
    fruit_time = party_start_time
    drinks_time = None
    workshops_time = parse_time_field(
        data,
        errors,
        "culinary_workshops_at",
        "Start warsztatów",
        bool(culinary_workshops_enabled),
    )
    pinata_time = parse_time_field(data, errors, "pinata_at", "Start piniaty", bool(pinata_enabled))
    mascot_time = parse_time_field(data, errors, "mascot_at", "Start maskotki", bool(mascot_enabled))

    for field, value, duration in (
        ("animation_at", animation_time, SERVICE_DURATIONS["animation_at"]),
        ("cake_at", cake_time, SERVICE_DURATIONS["cake_at"]),
        ("culinary_workshops_at", workshops_time, SERVICE_DURATIONS["culinary_workshops_at"]),
        ("pinata_at", pinata_time, SERVICE_DURATIONS["pinata_at"]),
        ("mascot_at", mascot_time, SERVICE_DURATIONS["mascot_at"]),
    ):
        if overlaps_stage_block(value, duration):
            errors[field] = STAGE_BLOCK_MESSAGE

    status = data.get("status", "active").strip()
    if status not in STATUS_LABELS:
        errors["status"] = "Wybierz poprawny status rezerwacji."

    cancellation_reason = data.get("cancellation_reason", "").strip()
    if status == "cancelled" and not cancellation_reason:
        errors["cancellation_reason"] = "Powód anulowania jest wymagany przy statusie Anulowana."
    if status == "active":
        cancellation_reason = ""

    cleaned: dict[str, object] = {
        "id": reservation_id,
        "reservation_date": data.get("reservation_date", "").strip(),
        "party_start_time": data.get("party_start_time", "").strip(),
        "start_at": start_at or "",
        "end_at": end_at or "",
        "children_count": parse_int_field(data, errors, "children_count", "Liczba dzieci", 1, 120),
        "adults_count": parse_int_field(data, errors, "adults_count", "Liczba dorosłych", 0, 120),
        "parent_name": parse_text_field(data, errors, "parent_name", "Rodzic / osoba rezerwująca"),
        "birthday_child_name": parse_text_field(data, errors, "birthday_child_name", "Imię solenizanta"),
        "birthday_child_age": parse_int_field(data, errors, "birthday_child_age", "Wiek solenizanta", 1, 18),
        "child_location": child_location,
        "adult_location": adult_location,
        "animation_enabled": animation_enabled,
        "animation_type": animation_type or None,
        "animation_at": combine_day_time(reservation_day, animation_time) if animation_enabled else None,
        "cake_enabled": cake_enabled,
        "cake_theme": cake_theme or None,
        "cake_at": combine_day_time(reservation_day, cake_time) if cake_enabled else None,
        "fruit_enabled": fruit_enabled,
        "fruit_plates": fruit_plates if fruit_enabled else None,
        "fruit_at": combine_day_time(reservation_day, fruit_time) if fruit_enabled else None,
        "drinks_enabled": drinks_enabled,
        "drinks_at": None,
        "culinary_workshops_enabled": culinary_workshops_enabled,
        "culinary_workshops_type": workshops_type or None,
        "culinary_workshops_at": combine_day_time(reservation_day, workshops_time)
        if culinary_workshops_enabled
        else None,
        "pinata_enabled": pinata_enabled,
        "pinata_theme": pinata_theme or None,
        "pinata_at": combine_day_time(reservation_day, pinata_time) if pinata_enabled else None,
        "mascot_enabled": mascot_enabled,
        "mascot_type": mascot_type or None,
        "mascot_at": combine_day_time(reservation_day, mascot_time) if mascot_enabled else None,
        "attraction_at": None,
        "notes": data.get("notes", "").strip(),
        "status": status,
        "cancellation_reason": cancellation_reason,
    }

    if (
        status == "active"
        and start_at
        and end_at
        and child_location in ALL_LOCATIONS
        and adult_locations
        and not invalid_adult_locations
    ):
        conflicts = find_conflicts(start_at, end_at, child_location, adult_locations, exclude_id=reservation_id)
        if conflicts:
            conflict_lines = []
            for conflict in conflicts:
                conflict_lines.append(
                    f"{conflict['birthday_child_name']} ({conflict['child_location']}, {display_locations(conflict['adult_location'])})"
                )
            errors["child_location"] = "Wybrana sala lub stolik nakłada się z rezerwacją: " + "; ".join(conflict_lines)

    return cleaned, errors


def history_snapshot(row: sqlite3.Row | dict[str, object]) -> str:
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True)


def record_history(reservation_id: int, action: str, role: str, snapshot: sqlite3.Row | dict[str, object]) -> None:
    execute(
        """
        INSERT INTO reservation_history (reservation_id, action, changed_by_role, snapshot_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (reservation_id, action, role, history_snapshot(snapshot), now_iso()),
    )


def save_reservation(values: dict[str, object], role: str = "manager") -> int:
    reservation_id = values.get("id")
    timestamp = now_iso()
    params = (
        values["start_at"],
        values["end_at"],
        values["children_count"],
        values["adults_count"],
        values["parent_name"],
        values["birthday_child_name"],
        values["birthday_child_age"],
        values["child_location"],
        values["adult_location"],
        values["animation_enabled"],
        values["animation_type"],
        values["animation_at"],
        values["cake_enabled"],
        values["cake_theme"],
        values["cake_at"],
        values["fruit_enabled"],
        values["fruit_plates"],
        values["fruit_at"],
        values["drinks_enabled"],
        values["drinks_at"],
        values["culinary_workshops_enabled"],
        values["culinary_workshops_type"],
        values["culinary_workshops_at"],
        values["pinata_enabled"],
        values["pinata_theme"],
        values["pinata_at"],
        values["mascot_enabled"],
        values["mascot_type"],
        values["mascot_at"],
        values["attraction_at"],
        values["notes"],
        values["status"],
        values["cancellation_reason"],
    )

    if reservation_id:
        previous = get_reservation(int(reservation_id))
        execute(
            """
            UPDATE reservations
            SET start_at = ?, end_at = ?, children_count = ?, adults_count = ?,
                parent_name = ?, birthday_child_name = ?, birthday_child_age = ?,
                child_location = ?, adult_location = ?, animation_enabled = ?, animation_type = ?,
                animation_at = ?,
                cake_enabled = ?, cake_theme = ?, cake_at = ?,
                fruit_enabled = ?, fruit_plates = ?, fruit_at = ?,
                drinks_enabled = ?, drinks_at = ?, culinary_workshops_enabled = ?,
                culinary_workshops_type = ?, culinary_workshops_at = ?,
                pinata_enabled = ?, pinata_theme = ?, pinata_at = ?,
                mascot_enabled = ?, mascot_type = ?, mascot_at = ?, attraction_at = ?,
                notes = ?, status = ?, cancellation_reason = ?,
                updated_at = ?
            WHERE id = ?
            """,
            params + (timestamp, int(reservation_id)),
        )
        updated = get_reservation(int(reservation_id))
        action = "cancelled" if previous and previous["status"] != "cancelled" and values["status"] == "cancelled" else "updated"
        if updated:
            record_history(int(reservation_id), action, role, updated)
        return int(reservation_id)

    new_id = execute(
        """
        INSERT INTO reservations (
            start_at, end_at, children_count, adults_count, parent_name,
            birthday_child_name, birthday_child_age, child_location, adult_location,
            animation_enabled, animation_type, animation_at, cake_enabled, cake_theme, cake_at,
            fruit_enabled, fruit_plates, fruit_at, drinks_enabled, drinks_at,
            culinary_workshops_enabled, culinary_workshops_type, culinary_workshops_at,
            pinata_enabled, pinata_theme, pinata_at,
            mascot_enabled, mascot_type, mascot_at, attraction_at,
            notes, status, cancellation_reason,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params + (timestamp, timestamp),
    )
    row = get_reservation(new_id)
    if row:
        record_history(new_id, "created", role, row)
    return new_id


def delete_reservation(reservation_id: int) -> bool:
    existing = get_reservation(reservation_id)
    if existing is None:
        return False
    execute("DELETE FROM reservation_history WHERE reservation_id = ?", (reservation_id,))
    execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
    return True


def get_reservation(reservation_id: int) -> sqlite3.Row | None:
    return db_one("SELECT * FROM reservations WHERE id = ?", (reservation_id,))


def get_reservations_for_day(target_day: date) -> list[sqlite3.Row]:
    start, end = day_bounds(target_day)
    return db_rows(
        """
        SELECT *
        FROM reservations
        WHERE start_at < ? AND end_at > ?
        ORDER BY start_at ASC, child_location ASC
        """,
        (end, start),
    )


def get_all_reservations() -> list[sqlite3.Row]:
    return db_rows(
        """
        SELECT *
        FROM reservations
        ORDER BY start_at ASC, id ASC
        """
    )


def get_history(reservation_id: int) -> list[sqlite3.Row]:
    return db_rows(
        """
        SELECT *
        FROM reservation_history
        WHERE reservation_id = ?
        ORDER BY created_at DESC
        """,
        (reservation_id,),
    )


def availability_for(
    reservation_day: str,
    start_time: str = "",
    end_time: str = "",
    exclude_id: int | None = None,
) -> dict[str, dict[str, str]]:
    statuses = {location: {"status": "free", "label": "Wolne"} for location in ALL_LOCATIONS}
    day_value = parse_date_for_api(reservation_day)
    if day_value is None:
        return statuses

    start_at, end_at = day_bounds(day_value)

    params: list[object] = [end_at, start_at]
    exclude_sql = ""
    if exclude_id:
        exclude_sql = "AND id != ?"
        params.append(exclude_id)

    rows = db_rows(
        f"""
        SELECT id, start_at, end_at, parent_name, birthday_child_name, child_location, adult_location
        FROM reservations
        WHERE status = 'active'
          AND start_at < ?
          AND end_at > ?
          {exclude_sql}
        ORDER BY start_at ASC
        """,
        tuple(params),
    )
    for row in rows:
        label = f"Zajęte: {row['birthday_child_name']}"
        for location in reservation_locations(row):
            if location in statuses:
                statuses[location] = {"status": "occupied", "label": label}
    return statuses


def parse_date_for_api(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def format_time(value: object) -> str:
    if not value:
        return ""
    raw = str(value)
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        return raw


def format_date(value: object) -> str:
    if not value:
        return ""
    raw = str(value)
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d")
    except ValueError:
        return raw[:10]


def format_datetime(value: object) -> str:
    if not value:
        return ""
    raw = str(value)
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def field_time(values: dict[str, object], field: str) -> str:
    return format_time(values.get(field))


def is_enabled(row: sqlite3.Row | dict[str, object], field: str) -> bool:
    return int(row[field] or 0) == 1


def selected(current: object, value: str) -> str:
    return " selected" if value in location_values(current) else ""


def checked(current: object) -> str:
    return " checked" if int(current or 0) == 1 else ""


def error_for(errors: dict[str, str], field: str) -> str:
    if field not in errors:
        return ""
    return f'<span class="field-error">{escape(errors[field])}</span>'


def link_for(role: str, day: str, **extra: object) -> str:
    params = {"role": role, "day": day}
    for key, value in extra.items():
        if value not in (None, ""):
            params[key] = str(value)
    return "/?" + urlencode(params)


def page_template(
    content: str,
    message: str = "",
    errors: dict[str, str] | None = None,
    role: str = "manager",
    day: str = "today",
) -> bytes:
    errors = errors or {}
    alert = ""
    if message:
        alert = f'<div class="alert success">{escape(message)}</div>'
    elif errors:
        alert = '<div class="alert error">Popraw zaznaczone pola formularza.</div>'

    role = normalize_role(role)
    day = normalize_day(day)

    document = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      color-scheme: dark;
      --ink: #f7f9fc;
      --muted: #aab3c2;
      --line: #343944;
      --surface: #151821;
      --surface-strong: #1d222c;
      --soft: #0b0d12;
      --brand: #139bd7;
      --brand-dark: #0b78ad;
      --orange: #f58212;
      --lime: #b3d316;
      --accent: #f58212;
      --danger: #fb7185;
      --danger-soft: #351923;
      --ok: #b3d316;
      --ok-soft: #253016;
      --busy: #f58212;
      --busy-soft: #432613;
      --focus: rgba(19, 155, 215, 0.32);
      --field: #0f1218;
      --field-strong: #111722;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--soft);
      min-height: 100vh;
    }}

    header {{
      background: #0b0d12;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 20;
    }}

    .topbar {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 18px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }}

    .logo {{
      width: 132px;
      height: 56px;
      background: #ffffff;
      object-fit: contain;
      padding: 7px 10px;
      flex: 0 0 auto;
    }}

    h1 {{
      margin: 0;
      font-size: 1.42rem;
      line-height: 1.12;
      font-weight: 900;
    }}

    h2, h3 {{
      margin: 0;
      line-height: 1.25;
    }}

    h2 {{
      font-size: 1.04rem;
    }}

    h3 {{
      font-size: 0.95rem;
    }}

    .subtitle, .muted {{
      color: var(--muted);
      font-size: 0.9rem;
    }}

    .subtitle {{
      margin: 4px 0 0;
    }}

    main {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 22px 24px 34px;
    }}

    .toolbar {{
      display: grid;
      gap: 12px;
      margin-bottom: 18px;
    }}

    .tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .tab {{
      border: 1px solid var(--line);
      min-height: 40px;
      padding: 8px 12px;
      background: var(--surface-strong);
      color: var(--ink);
      text-decoration: none;
      font-weight: 800;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}

    .tab[aria-current="page"] {{
      background: var(--brand);
      border-color: var(--brand);
      color: white;
    }}

    .layout {{
      display: grid;
      grid-template-columns: minmax(360px, 520px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      margin-top: 18px;
    }}

    .stack {{
      display: grid;
      gap: 18px;
    }}

    section, .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      overflow: hidden;
    }}

    .section-head {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--surface-strong);
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}

    .section-body {{
      padding: 16px 18px;
    }}

    .count {{
      color: var(--muted);
      font-size: 0.86rem;
      white-space: nowrap;
    }}

    form {{
      padding: 18px;
    }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 13px;
    }}

    .form-board {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 0;
      border: 1px solid var(--line);
      background: var(--field);
    }}

    .form-category {{
      border: 0;
      border-top: 1px solid var(--line);
      background: var(--field);
    }}

    .form-category:first-child {{
      border-top: 0;
    }}

    .category-title {{
      margin: 0;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #202531;
      color: #f7f9fc;
      font-size: 0.76rem;
      line-height: 1.2;
      text-transform: uppercase;
      letter-spacing: 0;
      font-weight: 900;
    }}

    .category-fields {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      padding: 12px;
      align-content: start;
    }}

    .category-fields.single {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}

    .category-fields.services {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}

    .full {{
      grid-column: 1 / -1;
    }}

    label {{
      display: grid;
      gap: 6px;
      font-size: 0.88rem;
      font-weight: 800;
    }}

    input, select, textarea {{
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      padding: 8px 10px;
      font: inherit;
      color: var(--ink);
      background: #0d1016;
    }}

    input::placeholder, textarea::placeholder {{
      color: #7d8797;
    }}

    textarea {{
      min-height: 78px;
      resize: vertical;
    }}

    input:focus, select:focus, textarea:focus {{
      outline: 3px solid var(--focus);
      border-color: var(--brand);
    }}

    .choice-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .switch, .service-option {{
      display: grid;
      gap: 10px;
      border: 1px solid var(--line);
      padding: 10px;
      font-weight: 800;
      min-height: 44px;
      background: var(--field-strong);
    }}

    .switch {{
      grid-template-columns: 1fr auto;
      align-items: center;
    }}

    .service-check, .service-time {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      font-weight: 800;
    }}

    .service-extra {{
      font-size: 0.82rem;
      font-weight: 800;
    }}

    .service-time {{
      grid-template-columns: minmax(104px, 128px) minmax(86px, 1fr);
    }}

    .service-time input {{
      min-height: 36px;
    }}

    .service-duration {{
      margin-left: 6px;
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 900;
    }}

    .service-end {{
      color: var(--muted);
      font-size: 0.8rem;
      font-weight: 900;
      white-space: nowrap;
    }}

    .switch input, .service-check input {{
      width: 18px;
      height: 18px;
      min-height: 18px;
      accent-color: var(--brand);
    }}

    .is-hidden {{
      display: none;
    }}

    .actions {{
      margin-top: 16px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}

    button, .button {{
      appearance: none;
      border: 0;
      padding: 9px 13px;
      background: var(--brand);
      color: white;
      font: inherit;
      font-weight: 900;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
    }}

    button:hover, .button:hover {{
      background: var(--brand-dark);
    }}

    .button.secondary {{
      background: var(--surface-strong);
      color: var(--ink);
      border: 1px solid var(--line);
    }}

    .button.secondary:hover {{
      background: #283040;
    }}

    .button.warning {{
      background: var(--accent);
      color: #141414;
    }}

    .button.danger {{
      background: #be123c;
      color: white;
    }}

    .button.danger:hover {{
      background: #9f1239;
    }}

    .inline-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 8px;
    }}

    .inline-form {{
      padding: 0;
      margin: 0;
    }}

    .alert {{
      margin-bottom: 16px;
      padding: 12px 14px;
      font-weight: 800;
      border: 1px solid;
    }}

    .alert.success {{
      color: var(--ok);
      background: var(--ok-soft);
      border-color: #526117;
    }}

    .alert.error {{
      color: var(--danger);
      background: var(--danger-soft);
      border-color: #7f2637;
    }}

    .field-error {{
      color: var(--danger);
      font-size: 0.79rem;
      font-weight: 800;
      line-height: 1.35;
    }}

    .table-wrap {{
      overflow-x: auto;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }}

    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 0.9rem;
    }}

    th {{
      color: #cbd5e1;
      background: #202531;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0;
    }}

    tbody tr:nth-child(even) {{
      background: #11151d;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      background: #122a38;
      color: #8bdcff;
      font-weight: 900;
      font-size: 0.76rem;
      margin: 0 4px 4px 0;
      white-space: nowrap;
    }}

    .pill.ok {{
      background: var(--ok-soft);
      color: var(--ok);
    }}

    .pill.cancelled, .pill.danger {{
      background: var(--danger-soft);
      color: var(--danger);
    }}

    .empty {{
      padding: 30px 18px;
      color: var(--muted);
      text-align: center;
    }}

    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }}

    .metric {{
      border: 1px solid var(--line);
      padding: 12px;
      background: var(--field);
    }}

    .metric strong {{
      display: block;
      font-size: 1.35rem;
      line-height: 1;
      margin-bottom: 6px;
    }}

    .schedule-list {{
      display: grid;
      gap: 0;
    }}

    .banquet-grid, .kitchen-board {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
      padding: 14px;
    }}

    .banquet-card, .kitchen-column {{
      border: 1px solid var(--line);
      background: var(--field);
      min-width: 0;
    }}

    .banquet-title, .kitchen-title {{
      margin: 0;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #202531;
      color: #f7f9fc;
      font-size: 0.86rem;
      line-height: 1.25;
      font-weight: 900;
    }}

    .banquet-tasks, .kitchen-orders {{
      display: grid;
      gap: 0;
    }}

    .banquet-task, .kitchen-order {{
      display: grid;
      gap: 4px;
      padding: 10px 12px;
      border-top: 1px solid var(--line);
    }}

    .banquet-task:first-child, .kitchen-order:first-child {{
      border-top: 0;
    }}

    .schedule-item {{
      display: grid;
      grid-template-columns: 90px minmax(0, 1fr);
      gap: 14px;
      padding: 14px 18px;
      border-top: 1px solid var(--line);
      align-items: start;
    }}

    .schedule-item:first-child {{
      border-top: 0;
    }}

    .schedule-time {{
      color: #f7f9fc;
      font-size: 1.05rem;
      font-weight: 900;
      line-height: 1.2;
    }}

    .schedule-detail {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}

    .schedule-title {{
      font-weight: 900;
      line-height: 1.25;
    }}

    .plan-wrap {{
      padding: 14px;
    }}

    .plan-legend {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 700;
    }}

    .legend-key {{
      width: 14px;
      height: 14px;
      display: inline-block;
      vertical-align: middle;
      margin-right: 5px;
      border: 1px solid var(--line);
    }}

    .key-free {{
      background: var(--ok-soft);
      border-color: rgba(179, 211, 22, 0.7);
    }}

    .key-busy {{
      background: var(--busy-soft);
      border-color: rgba(245, 130, 18, 0.78);
    }}

    .room-plan {{
      width: 100%;
      max-height: 620px;
      border: 1px solid var(--line);
      background: #0d1016;
      display: block;
    }}

    .plan-outline {{
      fill: #151b25;
      stroke: #343944;
      stroke-width: 2;
    }}

    .room-node rect {{
      fill: var(--ok-soft);
      stroke: #7f9918;
      stroke-width: 2;
      transition: fill 0.15s ease, stroke 0.15s ease;
    }}

    .room-zone rect, .room-zone polygon {{
      fill: #172231;
      stroke: #36536c;
      stroke-width: 2;
    }}

    .room-node.is-busy rect {{
      fill: var(--busy-soft);
      stroke: var(--busy);
    }}

    .room-zone.is-busy rect, .room-zone.is-busy polygon {{
      fill: var(--busy-soft);
      stroke: var(--busy);
    }}

    .table-node circle {{
      fill: var(--ok-soft);
      stroke: #7f9918;
      stroke-width: 2;
    }}

    .table-node.is-busy circle {{
      fill: var(--busy-soft);
      stroke: var(--busy);
    }}

    .room-node text, .room-zone text, .table-node text {{
      fill: var(--ink);
      pointer-events: none;
      font-size: 13px;
      font-weight: 900;
      letter-spacing: 0;
    }}

    .room-zone text {{
      font-size: 12px;
    }}

    .room-zone .zone-subtitle {{
      fill: var(--muted);
      font-size: 11px;
    }}

    .table-node text {{
      font-size: 10px;
    }}

    .schema-list {{
      columns: 2;
      column-gap: 28px;
      padding-left: 18px;
      margin: 0;
    }}

    .schema-list li {{
      break-inside: avoid;
      margin-bottom: 7px;
    }}

    @media (max-width: 1120px) {{
      .layout {{
        grid-template-columns: 1fr;
      }}

      .form-board {{
        grid-template-columns: 1fr;
      }}

      .category-fields.services {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .metrics {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}

    @media (max-width: 640px) {{
      .topbar, main {{
        padding-left: 14px;
        padding-right: 14px;
      }}

      .topbar {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .brand {{
        align-items: flex-start;
      }}

      .logo {{
        width: 108px;
        height: 48px;
      }}

      .grid, .choice-grid, .metrics, .form-board, .category-fields, .category-fields.services {{
        grid-template-columns: 1fr;
      }}

      .section-head {{
        flex-direction: column;
      }}

      .schema-list {{
        columns: 1;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div class="brand">
        <img class="logo" src="/logo.png" alt="iKids Park">
        <div>
          <h1>iKids Park Rezerwacje</h1>
          <p class="subtitle">Wewnętrzny panel urodzin: kierownik zmiany, recepcja, animatorzy, kuchnia i organizator.</p>
        </div>
      </div>
      <div class="tabs">
        <a class="tab" href="/schema">Struktura bazy</a>
        <a class="tab" href="/export">Eksport CSV</a>
      </div>
    </div>
  </header>
  <main>
    {alert}
    {content}
  </main>
  <script>
    window.IKIDS_CONTEXT = {json.dumps({"role": role, "day": day}, ensure_ascii=False)};
  </script>
</body>
</html>"""
    return document.encode("utf-8")


def render_nav(role: str, day: str) -> str:
    role = normalize_role(role)
    day = normalize_day(day)
    role_links = []
    for key, meta in ROLE_DEFS.items():
        role_links.append(
            f'<a class="tab" href="{link_for(key, day)}" aria-current="page"'
            f' title="{escape(meta["hint"])}">{escape(meta["label"])}</a>'
            if key == role
            else f'<a class="tab" href="{link_for(key, day)}" title="{escape(meta["hint"])}">{escape(meta["label"])}</a>'
        )

    day_links = []
    for key, (label, _) in DAY_FILTERS.items():
        target = selected_day(key)
        text = f"{label} · {target.strftime('%d.%m')}"
        day_links.append(
            f'<a class="tab" href="{link_for(role, key)}" aria-current="page">{escape(text)}</a>'
            if key == day
            else f'<a class="tab" href="{link_for(role, key)}">{escape(text)}</a>'
        )

    return f"""
<div class="toolbar">
  <div class="tabs">{''.join(role_links)}</div>
  <div class="tabs">{''.join(day_links)}</div>
</div>
"""


def default_form_values(target_day: date) -> dict[str, object]:
    return {
        "id": "",
        "reservation_date": target_day.isoformat(),
        "party_start_time": "",
        "children_count": "",
        "adults_count": "",
        "parent_name": "",
        "birthday_child_name": "",
        "birthday_child_age": "",
        "child_location": PARTY_ROOMS[0],
        "adult_location": PARTY_ROOMS[0],
        "animation_enabled": 0,
        "animation_type": "",
        "animation_at": "",
        "cake_enabled": 0,
        "cake_theme": "",
        "cake_at": "",
        "fruit_enabled": 0,
        "fruit_plates": "",
        "fruit_at": "",
        "drinks_enabled": 0,
        "drinks_at": "",
        "culinary_workshops_enabled": 0,
        "culinary_workshops_type": "",
        "culinary_workshops_at": "",
        "pinata_enabled": 0,
        "pinata_theme": "",
        "pinata_at": "",
        "mascot_enabled": 0,
        "mascot_type": "",
        "mascot_at": "",
        "attraction_at": "",
        "notes": "",
        "status": "active",
        "cancellation_reason": "",
    }


def row_to_form_values(row: sqlite3.Row) -> dict[str, object]:
    values = dict(row)
    values["reservation_date"] = format_date(row["start_at"])
    values["party_start_time"] = format_time(row["start_at"])
    for field in (
        "animation_at",
        "cake_at",
        "fruit_at",
        "drinks_at",
        "culinary_workshops_at",
        "pinata_at",
        "mascot_at",
        "attraction_at",
    ):
        values[field] = format_time(row[field])
    return values


def render_options(options: list[str], current: object) -> str:
    return "\n".join(
        f'<option value="{escape(option)}"{selected(current, option)}>{escape(option)}</option>' for option in options
    )


def render_grouped_options(groups: dict[str, list[str]], current: object) -> str:
    optgroups = []
    for label, options in groups.items():
        option_markup = "\n".join(
            f'<option value="{escape(option)}"{selected(current, option)}>{escape(option)}</option>'
            for option in options
        )
        optgroups.append(f'<optgroup label="{escape(label)}">{option_markup}</optgroup>')
    return "\n".join(optgroups)


def render_animation_type_select(values: dict[str, object], errors: dict[str, str]) -> str:
    return f"""
        <label class="service-extra">
          Rodzaj animacji
          <select name="animation_type">
            <option value="">Wybierz animację</option>
            {render_grouped_options(ANIMATION_GROUPS, values.get("animation_type"))}
          </select>
          {error_for(errors, "animation_type")}
        </label>
"""


def render_fruit_plates_input(values: dict[str, object], errors: dict[str, str]) -> str:
    return f"""
        <label class="service-extra">
          Liczba talerzy
          <input type="number" name="fruit_plates" min="1" max="200" value="{escape(values.get("fruit_plates", ""))}">
          {error_for(errors, "fruit_plates")}
        </label>
"""


def render_cake_theme_input(values: dict[str, object], errors: dict[str, str]) -> str:
    return f"""
        <label class="service-extra">
          Motyw tortu
          <input name="cake_theme" value="{escape(values.get("cake_theme", ""))}" placeholder="(brak)">
          {error_for(errors, "cake_theme")}
        </label>
"""


def render_workshop_type_select(values: dict[str, object], errors: dict[str, str]) -> str:
    return f"""
        <label class="service-extra">
          Rodzaj warsztatów
          <select name="culinary_workshops_type">
            <option value="">Wybierz warsztaty</option>
            {render_options(WORKSHOP_TYPES, values.get("culinary_workshops_type"))}
          </select>
          {error_for(errors, "culinary_workshops_type")}
        </label>
"""


def render_pinata_theme_input(values: dict[str, object], errors: dict[str, str]) -> str:
    return f"""
        <label class="service-extra">
          Motyw piniaty
          <input name="pinata_theme" value="{escape(values.get("pinata_theme", ""))}" placeholder="(brak)">
          {error_for(errors, "pinata_theme")}
        </label>
"""


def render_mascot_type_select(values: dict[str, object], errors: dict[str, str]) -> str:
    return f"""
        <label class="service-extra">
          Maskotka
          <select name="mascot_type">
            <option value="">Wybierz maskotkę</option>
            {render_options(MASCOT_TYPES, values.get("mascot_type"))}
          </select>
          {error_for(errors, "mascot_type")}
        </label>
"""


def format_duration(minutes: int | None) -> str:
    if minutes is None:
        return ""
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}min"


def service_end_label(values: dict[str, object], field: str, duration_minutes: int | None) -> str:
    if duration_minutes is None:
        return ""
    start = parse_time_value(field_time(values, field))
    if start is None:
        return ""
    end_dt = datetime.combine(date.today(), start) + timedelta(minutes=duration_minutes)
    return f"koniec {end_dt.strftime('%H:%M')}"


def render_service_option(
    values: dict[str, object],
    errors: dict[str, str],
    enabled_field: str,
    time_field: str,
    label: str,
    duration_minutes: int | None = None,
    extra_markup: str = "",
    show_time: bool = True,
) -> str:
    duration = format_duration(duration_minutes)
    duration_label = f'<span class="service-duration">{escape(duration)}</span>' if duration else ""
    end_label = service_end_label(values, time_field, duration_minutes)
    end_markup = f'<span class="service-end">{escape(end_label)}</span>' if end_label else '<span class="service-end"></span>'
    time_markup = (
        f"""
        <div class="service-time">
          <input type="text" inputmode="numeric" autocomplete="off" placeholder="00:00" maxlength="5" name="{escape(time_field)}" value="{escape(field_time(values, time_field))}" data-time-input="1" data-duration-minutes="{escape(duration_minutes or '')}" aria-label="{escape(label)} start">
          {end_markup}
        </div>
        {error_for(errors, time_field)}
"""
        if show_time
        else ""
    )
    return f"""
      <div class="service-option">
        <label class="service-check">
          <span>{escape(label)}{duration_label}</span>
          <input type="checkbox" name="{escape(enabled_field)}" value="1"{checked(values.get(enabled_field))}>
        </label>
        {time_markup}
        {extra_markup}
      </div>
"""


def render_room_plan(values: dict[str, object], errors: dict[str, str]) -> str:
    statuses = availability_for(
        str(values.get("reservation_date", "")),
    )
    room_nodes = []
    for name, x, y, width, height in ROOM_LAYOUT:
        status = statuses.get(name, {"status": "free", "label": "Wolne"})
        classes = ["room-node"]
        if status["status"] == "occupied":
            classes.append("is-busy")
        label_lines = split_svg_label(name)
        line_markup = "".join(
            f'<tspan x="{x + width / 2:.1f}" dy="{0 if index == 0 else 15}">{escape(line)}</tspan>'
            for index, line in enumerate(label_lines)
        )
        room_nodes.append(
            f"""
    <g class="{' '.join(classes)}" data-location="{escape(name)}" aria-label="{escape(name)}">
      <title>{escape(status["label"])}</title>
      <rect x="{x}" y="{y}" width="{width}" height="{height}"></rect>
      <text text-anchor="middle" x="{x + width / 2:.1f}" y="{y + height / 2 - (len(label_lines) - 1) * 7:.1f}">{line_markup}</text>
    </g>
"""
        )

    zones = []
    for name, subtitle, x, y, width, height in ADULT_ZONE_LAYOUT:
        zone_locations = ADULT_LOCATION_GROUPS.get(name, [])
        busy_count = sum(
            1 for location in zone_locations if statuses.get(location, {}).get("status") == "occupied"
        )
        classes = ["room-zone"]
        if name == "Trójkąt":
            shape = '<polygon points="628,82 800,82 872,166 872,330 628,330"></polygon>'
        else:
            shape = f'<rect x="{x}" y="{y}" width="{width}" height="{height}"></rect>'
        zones.append(
            f"""
    <g class="{' '.join(classes)}" aria-label="{escape(name)}: {escape(subtitle)}">
      <title>{escape(name)}: {busy_count} zajęte</title>
      {shape}
      <text text-anchor="middle" x="{x + width / 2:.1f}" y="{y + 24:.1f}">{escape(name)}</text>
      <text class="zone-subtitle" text-anchor="middle" x="{x + width / 2:.1f}" y="{y + 42:.1f}">{escape(subtitle)}</text>
    </g>
"""
        )

    table_nodes = []
    for number, x, y in TABLE_LAYOUT:
        area = TABLE_ZONE_BY_NUMBER[number]
        location = f"{area} - Stolik {number}"
        status = statuses.get(location, {"status": "free", "label": "Wolne"})
        classes = ["table-node"]
        if status["status"] == "occupied":
            classes.append("is-busy")
        table_nodes.append(
            f"""
    <g class="{' '.join(classes)}" data-location="{escape(location)}" aria-label="{escape(location)}">
      <title>{escape(status["label"])}</title>
      <circle cx="{x}" cy="{y}" r="13"></circle>
      <text text-anchor="middle" x="{x}" y="{y + 4}">{number}</text>
    </g>
"""
        )

    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>Plan sali i dostępność na żywo</h2>
      <p class="subtitle">Mockup pokazuje zajęte sale i strefy kolorem pomarańczowym dla wybranej daty.</p>
    </div>
  </div>
  <div class="plan-wrap">
    <div class="plan-legend">
      <span><span class="legend-key key-free"></span>wolne</span>
      <span><span class="legend-key key-busy"></span>zajęte</span>
      <span class="muted">Bar, Scena, Trójkąt, Labirynt i loże tematyczne.</span>
    </div>
    {error_for(errors, "child_location")}
    <svg class="room-plan" viewBox="0 0 940 560" aria-label="Plan sali iKids Park">
      <rect x="16" y="16" width="908" height="528" fill="none" stroke="#343944" stroke-width="2"></rect>
      <path d="M40 252 H250 V338 H40 Z" class="plan-outline"></path>
      <path d="M280 148 H592 V338 H280 Z" class="plan-outline"></path>
      <path d="M628 76 H804 L880 164 V338 H628 Z" class="plan-outline"></path>
      <path d="M736 338 H912 V532 H736 Z" class="plan-outline"></path>
      <text x="92" y="392" font-size="13" font-weight="900" fill="#aab3c2">Pokoje / loże tematyczne</text>
      {''.join(zones)}
      {''.join(table_nodes)}
      {''.join(room_nodes)}
    </svg>
  </div>
</section>
"""


def format_service_window(start_value: object, duration_minutes: int | None = None) -> str:
    start_label = format_time(start_value)
    if not start_label:
        return ""
    if duration_minutes is None:
        return start_label
    start = parse_time_value(start_label)
    if start is None:
        return start_label
    end_dt = datetime.combine(date.today(), start) + timedelta(minutes=duration_minutes)
    return f"{start_label}-{end_dt.strftime('%H:%M')}"


def split_svg_label(label: str) -> list[str]:
    cleaned = label.replace("Sala główna - ", "").replace("Antresola - ", "Antresola ")
    if " - " in cleaned:
        return cleaned.split(" - ", 1)
    parts = cleaned.split()
    if len(parts) <= 2:
        return [cleaned]
    midpoint = (len(parts) + 1) // 2
    return [" ".join(parts[:midpoint]), " ".join(parts[midpoint:])]


def render_form(
    values: dict[str, object],
    errors: dict[str, str],
    role: str,
    day: str,
) -> str:
    reservation_id = str(values.get("id", "") or "")
    is_edit = reservation_id.isdigit()
    title = "Edycja rezerwacji" if is_edit else "Nowa rezerwacja"
    action_label = "Zapisz zmiany" if is_edit else "Zapisz rezerwację"
    history_link = (
        f'<a class="button secondary" href="/history?id={escape(reservation_id)}&role={escape(role)}&day={escape(day)}">Historia</a>'
        if is_edit
        else ""
    )
    status_field = (
        f"""
      <label>
        Status
        <select name="status" id="status">
          <option value="active"{selected(values.get("status"), "active")}>Aktywna</option>
          <option value="cancelled"{selected(values.get("status"), "cancelled")}>Anulowana</option>
        </select>
        {error_for(errors, "status")}
      </label>
"""
        if is_edit
        else ""
    )
    cancellation_class = "full" if values.get("status") == "cancelled" else "full is-hidden"

    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>{title}</h2>
      <p class="subtitle">Rezerwacja blokuje wybrane lokalizacje na cały dzień. Godziny przy usługach są godzinami startu.</p>
    </div>
  </div>
  <form method="post" action="/reservations?role={escape(role)}&day={escape(day)}" id="reservation-form">
    <input type="hidden" name="id" id="reservation_id" value="{escape(reservation_id)}">
    <div class="form-board">
      <div class="form-category">
        <h3 class="category-title">Termin</h3>
        <div class="category-fields single">
          <label>
            Data
            <input type="date" name="reservation_date" id="reservation_date" value="{escape(values.get("reservation_date", ""))}" required>
            {error_for(errors, "reservation_date")}
          </label>
          <label>
            Godzina startu imprezy
            <input type="text" inputmode="numeric" autocomplete="off" placeholder="00:00" maxlength="5" name="party_start_time" value="{escape(values.get("party_start_time", ""))}" data-time-input="1" required>
            {error_for(errors, "party_start_time")}
          </label>
          {status_field}
        </div>
      </div>

      <div class="form-category">
        <h3 class="category-title">Goście</h3>
        <div class="category-fields">
          <label>
            Liczba dzieci
            <input type="number" name="children_count" min="1" max="120" value="{escape(values.get("children_count", ""))}" required>
            {error_for(errors, "children_count")}
          </label>
          <label>
            Liczba dorosłych
            <input type="number" name="adults_count" min="0" max="120" value="{escape(values.get("adults_count", ""))}" required>
            {error_for(errors, "adults_count")}
          </label>
          <label class="full">
            Rodzic / osoba rezerwująca
            <input name="parent_name" autocomplete="name" value="{escape(values.get("parent_name", ""))}" required>
            {error_for(errors, "parent_name")}
          </label>
          <label>
            Imię solenizanta
            <input name="birthday_child_name" value="{escape(values.get("birthday_child_name", ""))}" required>
            {error_for(errors, "birthday_child_name")}
          </label>
          <label>
            Wiek solenizanta
            <input type="number" name="birthday_child_age" min="1" max="18" value="{escape(values.get("birthday_child_age", ""))}" required>
            {error_for(errors, "birthday_child_age")}
          </label>
        </div>
      </div>

      <div class="form-category">
        <h3 class="category-title">Lokalizacje</h3>
        <div class="category-fields single">
          <label>
            Lokalizacja dzieci
            <select name="child_location" id="child_location" required>
              {render_grouped_options(LOCATION_GROUPS, values.get("child_location"))}
            </select>
            {error_for(errors, "child_location")}
          </label>
          <label>
            Lokalizacja dorosłych
            <select name="adult_location" id="adult_location" size="8" multiple required>
              {render_grouped_options(LOCATION_GROUPS, values.get("adult_location"))}
            </select>
            {error_for(errors, "adult_location")}
          </label>
        </div>
      </div>

      <div class="form-category wide">
        <h3 class="category-title">Atrakcje i dodatki</h3>
        <div class="category-fields services">
          {render_service_option(values, errors, "animation_enabled", "animation_at", "Animacja", SERVICE_DURATIONS["animation_at"], render_animation_type_select(values, errors))}
          {render_service_option(values, errors, "cake_enabled", "cake_at", "Tort", SERVICE_DURATIONS["cake_at"], render_cake_theme_input(values, errors))}
          {render_service_option(values, errors, "fruit_enabled", "fruit_at", "Owoce", None, render_fruit_plates_input(values, errors), show_time=False)}
          {render_service_option(values, errors, "culinary_workshops_enabled", "culinary_workshops_at", "Warsztaty kulinarne", SERVICE_DURATIONS["culinary_workshops_at"], render_workshop_type_select(values, errors))}
          {render_service_option(values, errors, "pinata_enabled", "pinata_at", "Piniata", SERVICE_DURATIONS["pinata_at"], render_pinata_theme_input(values, errors))}
          {render_service_option(values, errors, "mascot_enabled", "mascot_at", "Maskotka", SERVICE_DURATIONS["mascot_at"], render_mascot_type_select(values, errors))}
        </div>
      </div>

      <div class="form-category wide">
        <h3 class="category-title">Uwagi</h3>
        <div class="category-fields">
          <label class="full">
            Notatki
            <textarea name="notes" placeholder="Alergie, ustalenia z rodzicem, szczegóły organizacyjne...">{escape(values.get("notes", ""))}</textarea>
          </label>
          <label class="{cancellation_class}" id="cancellation_reason_field">
            Powód anulowania
            <textarea name="cancellation_reason" id="cancellation_reason" placeholder="Wymagane tylko przy zmianie statusu na Anulowana.">{escape(values.get("cancellation_reason", ""))}</textarea>
            {error_for(errors, "cancellation_reason")}
          </label>
        </div>
      </div>
    </div>
    <div class="actions">
      <button type="submit">{action_label}</button>
      <a class="button secondary" href="{link_for(role, day)}">Wyczyść</a>
      {history_link}
    </div>
  </form>
</section>
"""


def service_pills(row: sqlite3.Row) -> str:
    items = []
    if is_enabled(row, "animation_enabled"):
        animation_label = "Animacja"
        if row["animation_type"]:
            animation_label = f"Animacja: {row['animation_type']}"
        items.append((animation_label, row["animation_at"], SERVICE_DURATIONS["animation_at"]))
    if is_enabled(row, "cake_enabled"):
        cake_label = f"Tort: {row['cake_theme'] or '(brak)'}"
        items.append((cake_label, row["cake_at"], SERVICE_DURATIONS["cake_at"]))
    if is_enabled(row, "fruit_enabled"):
        fruit_label = "Owoce"
        if row["fruit_plates"]:
            fruit_label = f"Owoce: {row['fruit_plates']} tal."
        items.append((fruit_label, row["fruit_at"], None))
    if is_enabled(row, "culinary_workshops_enabled"):
        workshop_label = "Warsztaty"
        if row["culinary_workshops_type"]:
            workshop_label = f"Warsztaty: {row['culinary_workshops_type']}"
        items.append((workshop_label, row["culinary_workshops_at"], SERVICE_DURATIONS["culinary_workshops_at"]))
    if is_enabled(row, "pinata_enabled"):
        pinata_label = f"Piniata: {row['pinata_theme'] or '(brak)'}"
        items.append((pinata_label, row["pinata_at"], SERVICE_DURATIONS["pinata_at"]))
    if is_enabled(row, "mascot_enabled"):
        mascot_label = "Maskotka"
        if row["mascot_type"]:
            mascot_label = f"Maskotka: {row['mascot_type']}"
        items.append((mascot_label, row["mascot_at"], SERVICE_DURATIONS["mascot_at"]))

    if not items:
        return '<span class="pill">Bez dodatków</span>'
    return "".join(
        f'<span class="pill">{escape(label)}{": " + escape(format_service_window(value, duration)) if value else ""}</span>'
        for label, value, duration in items
    )


def render_metrics(rows: list[sqlite3.Row]) -> str:
    active = [row for row in rows if row["status"] == "active"]
    animation_count = sum(1 for row in active if is_enabled(row, "animation_enabled"))
    workshops = sum(1 for row in active if is_enabled(row, "culinary_workshops_enabled"))
    cakes = sum(1 for row in active if is_enabled(row, "cake_enabled"))
    kitchen = sum(
        1
        for row in active
        if is_enabled(row, "fruit_enabled")
        or is_enabled(row, "culinary_workshops_enabled")
    )
    guests = sum(int(row["children_count"]) + int(row["adults_count"]) for row in active)
    return f"""
<section>
  <div class="section-body">
    <div class="metrics">
      <div class="metric"><strong>{len(active)}</strong><span class="muted">aktywne rezerwacje</span></div>
      <div class="metric"><strong>{guests}</strong><span class="muted">goście łącznie</span></div>
      <div class="metric"><strong>{animation_count}</strong><span class="muted">animacje</span></div>
      <div class="metric"><strong>{workshops}</strong><span class="muted">warsztaty</span></div>
      <div class="metric"><strong>{cakes}</strong><span class="muted">torty · kuchnia: {kitchen}</span></div>
    </div>
  </div>
</section>
"""


def render_manager_view(rows: list[sqlite3.Row], role: str, day: str) -> str:
    if not rows:
        body = '<div class="empty">Brak rezerwacji w wybranym dniu.</div>'
    else:
        table_rows = []
        for row in rows:
            status_class = "ok" if row["status"] == "active" else "cancelled"
            cancellation = (
                f'<div class="muted">Powód: {escape(row["cancellation_reason"])}</div>'
                if row["status"] == "cancelled" and row["cancellation_reason"]
                else ""
            )
            notes = f'<div class="muted">{escape(row["notes"])}</div>' if row["notes"] else ""
            table_rows.append(
                f"""
                <tr>
                  <td>
                    <strong>{format_time(row["start_at"])} · {escape(row["birthday_child_name"])}, {escape(row["birthday_child_age"])} lat</strong>
                    <br><span class="muted">{format_date(row["start_at"])} · {escape(row["parent_name"])}</span>
                    <br><span class="muted">{escape(row["children_count"])} dzieci · {escape(row["adults_count"])} dorosłych</span>
                    {notes}
                  </td>
                  <td><strong>{escape(row["child_location"])}</strong><br><span class="muted">{escape(display_locations(row["adult_location"]))}</span></td>
                  <td>{service_pills(row)}</td>
                  <td>
                    <span class="pill {status_class}">{escape(STATUS_LABELS[row["status"]])}</span>{cancellation}
                    <div class="inline-actions">
                      <a class="button secondary" href="{link_for("organizer", day, edit=row["id"])}">Edytuj</a>
                      <form class="inline-form" method="post" action="/delete?role={escape(role)}&day={escape(day)}" onsubmit="return confirm('Usunąć tę rezerwację? Tej operacji nie można cofnąć.');">
                        <input type="hidden" name="id" value="{escape(row["id"])}">
                        <button class="button danger" type="submit">Usuń</button>
                      </form>
                    </div>
                  </td>
                </tr>
                """
            )
        body = f"""
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rezerwacja</th>
                <th>Miejsca</th>
                <th>Dodatki</th>
                <th>Status i akcje</th>
              </tr>
            </thead>
            <tbody>{''.join(table_rows)}</tbody>
          </table>
        </div>
        """

    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>Dzień operacyjny</h2>
      <p class="subtitle">Pełny widok rezerwacji dla recepcji i kierownika zmiany.</p>
    </div>
    <span class="count">{len(rows)} pozycji</span>
  </div>
  {body}
</section>
"""


def render_animator_view(rows: list[sqlite3.Row]) -> str:
    banquets = []
    for row in rows:
        if row["status"] != "active":
            continue

        tasks = []
        if is_enabled(row, "animation_enabled"):
            tasks.append((row["animation_at"], row["animation_type"] or "Animacja"))
        if is_enabled(row, "pinata_enabled"):
            tasks.append((row["pinata_at"], f"Piniata: {row['pinata_theme'] or '(brak)'}"))
        if is_enabled(row, "mascot_enabled"):
            tasks.append((row["mascot_at"], f"Maskotka: {row['mascot_type'] or '(brak)'}"))

        task_items = []
        for task_time, task_name in tasks:
            time_label = format_time(task_time)
            if not time_label:
                continue
            try:
                hour = int(time_label[:2])
            except ValueError:
                hour = 0
            if hour < 10 or hour > 21:
                continue
            task_items.append((time_label, task_name))

        if task_items:
            task_items.sort(key=lambda item: (item[0], item[1]))
            banquet_title = f"Bankiet: {row['birthday_child_name']} {row['birthday_child_age']} lat rodzic {row['parent_name']}"
            banquets.append((task_items[0][0], banquet_title, task_items))

    banquets.sort(key=lambda item: (item[0], item[1]))
    if not banquets:
        return '<section><div class="empty">Brak animacji</div></section>'

    banquet_cards = []
    for _, banquet_title, task_items in banquets:
        task_markup = "".join(
            f"""
              <div class="banquet-task">
                <div class="schedule-time">{escape(time_label)}</div>
                <div class="schedule-title">{escape(task_name)}</div>
              </div>
            """
            for time_label, task_name in task_items
        )
        banquet_cards.append(
            f"""
            <div class="banquet-card">
              <h3 class="banquet-title">{escape(banquet_title)}</h3>
              <div class="banquet-tasks">{task_markup}</div>
            </div>
            """
        )

    task_count = sum(len(task_items) for _, _, task_items in banquets)
    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>Animatorzy</h2>
      <p class="subtitle">Animacje, piniaty, maskotki.</p>
    </div>
    <span class="count">{task_count} pozycji</span>
  </div>
  <div class="banquet-grid">
    {''.join(banquet_cards)}
  </div>
</section>
"""


def render_kitchen_view(rows: list[sqlite3.Row]) -> str:
    active = [row for row in rows if row["status"] == "active"]

    def banquet_label(row: sqlite3.Row) -> str:
        return f"Bankiet: {row['birthday_child_name']} {row['birthday_child_age']} lat rodzic {row['parent_name']}"

    def order_markup(title: str, detail: str, row: sqlite3.Row) -> str:
        notes = f'<div class="muted">{escape(row["notes"])}</div>' if row["notes"] else ""
        return f"""
          <div class="kitchen-order">
            <div class="schedule-title">{escape(title)}</div>
            <div class="muted">{escape(detail)}</div>
            <div class="muted">{escape(banquet_label(row))}</div>
            {notes}
          </div>
        """

    fruit_orders = []
    cake_orders = []
    workshop_orders = []

    for row in active:
        if is_enabled(row, "fruit_enabled"):
            plates = f"{row['fruit_plates']} tal." if row["fruit_plates"] else "liczba talerzy: brak"
            fruit_orders.append(order_markup("Owoce", f"Start imprezy {format_time(row['start_at'])} · {plates}", row))
        if is_enabled(row, "cake_enabled"):
            cake_orders.append(
                order_markup(
                    f"Tort: {row['cake_theme'] or '(brak)'}",
                    format_service_window(row["cake_at"], SERVICE_DURATIONS["cake_at"]),
                    row,
                )
            )
        if is_enabled(row, "culinary_workshops_enabled"):
            workshop_name = row["culinary_workshops_type"] or "Warsztaty"
            workshop_orders.append(
                order_markup(
                    f"Warsztaty: {workshop_name}",
                    format_service_window(row["culinary_workshops_at"], SERVICE_DURATIONS["culinary_workshops_at"]),
                    row,
                )
            )

    total_orders = len(fruit_orders) + len(cake_orders) + len(workshop_orders)

    def column(title: str, orders: list[str]) -> str:
        body = "".join(orders) if orders else '<div class="kitchen-order"><span class="muted">Brak</span></div>'
        return (
            f"""
            <div class="kitchen-column">
              <h3 class="kitchen-title">{escape(title)} ({len(orders)})</h3>
              <div class="kitchen-orders">{body}</div>
            </div>
            """
        )

    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>Kuchnia</h2>
      <p class="subtitle">Owoce, torty i warsztaty dla wybranego dnia.</p>
    </div>
    <span class="count">{total_orders} zamówień</span>
  </div>
  <div class="kitchen-board">
    {column("Owoce", fruit_orders)}
    {column("Torty", cake_orders)}
    {column("Warsztaty", workshop_orders)}
  </div>
</section>
"""


def render_organizer_view(rows: list[sqlite3.Row]) -> str:
    active = [row for row in rows if row["status"] == "active"]
    if not active:
        return '<section><div class="empty">Brak aktywnych urodzin w wybranym dniu.</div></section>'

    table_rows = []
    for row in active:
        notes = f'<div class="muted">{escape(row["notes"])}</div>' if row["notes"] else ""
        table_rows.append(
            f"""
            <tr>
              <td>
                <strong>{format_time(row["start_at"])} · {escape(row["birthday_child_name"])}, {escape(row["birthday_child_age"])} lat</strong>
                <br><span class="muted">Rodzic: {escape(row["parent_name"])}</span>
                <br><span class="muted">{escape(row["children_count"])} dzieci · {escape(row["adults_count"])} dorosłych</span>
                {notes}
              </td>
              <td><strong>{escape(row["child_location"])}</strong><br><span class="muted">{escape(display_locations(row["adult_location"]))}</span></td>
              <td>{service_pills(row)}</td>
            </tr>
            """
        )

    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>Organizator urodzin</h2>
      <p class="subtitle">Podgląd bankietów, lokalizacji i dodatków dla wybranego dnia.</p>
    </div>
    <span class="count">{len(active)} bankietów</span>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Bankiet</th><th>Miejsca</th><th>Dodatki</th></tr></thead>
      <tbody>{''.join(table_rows)}</tbody>
    </table>
  </div>
</section>
"""


def render_role_view(role: str, rows: list[sqlite3.Row], day: str) -> str:
    if role == "animators":
        return render_animator_view(rows)
    if role == "kitchen":
        return render_kitchen_view(rows)
    if role == "organizer":
        return render_organizer_view(rows)
    return render_manager_view(rows, role, day)


def render_schema_summary() -> str:
    fields = [
        "reservations.id",
        "start_at jako godzina startu imprezy / end_at techniczne",
        "children_count / adults_count",
        "parent_name",
        "birthday_child_name / birthday_child_age",
        "child_location / adult_location",
        "animation_enabled / animation_type / animation_at",
        "cake_enabled / cake_theme / cake_at",
        "fruit_enabled / fruit_plates / fruit_at",
        "culinary_workshops_enabled / culinary_workshops_type / culinary_workshops_at",
        "pinata_enabled / pinata_theme / pinata_at",
        "mascot_enabled / mascot_type / mascot_at",
        "notes",
        "status / cancellation_reason",
        "created_at / updated_at",
        "reservation_history z pełnym snapshotem JSON",
    ]
    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>Proponowana struktura bazy danych</h2>
      <p class="subtitle">W prototypie działa SQLite. Ten sam model można przenieść 1:1 do PostgreSQL/Supabase.</p>
    </div>
  </div>
  <div class="section-body">
    <ul class="schema-list">
      {''.join(f'<li><code>{escape(field)}</code></li>' for field in fields)}
    </ul>
  </div>
</section>
"""


def render_home(
    role: str = "manager",
    day: str = "today",
    message: str = "",
    values: dict[str, object] | None = None,
    errors: dict[str, str] | None = None,
    edit_id: int | None = None,
) -> bytes:
    role = normalize_role(role)
    day = normalize_day(day)
    errors = errors or {}
    target_day = selected_day(day)
    rows = get_reservations_for_day(target_day)

    if values is None and edit_id:
        row = get_reservation(edit_id)
        values = row_to_form_values(row) if row else default_form_values(target_day)
        if row is None:
            message = "Nie znaleziono rezerwacji do edycji."

    if values is None:
        values = default_form_values(target_day)

    content = render_nav(role, day) + render_metrics(rows)
    if role == "manager":
        content += f"""
<div class="layout">
  <div class="stack">
    {render_room_plan(values, errors)}
  </div>
  <div class="stack">
    {render_role_view(role, rows, day)}
  </div>
</div>
"""
    elif role == "organizer":
        content += f"""
<div class="stack">
  {render_form(values, errors, role, day)}
</div>
<div class="layout">
  <div class="stack">
    {render_room_plan(values, errors)}
  </div>
  <div class="stack">
    {render_role_view(role, rows, day)}
  </div>
</div>
{room_plan_script()}
"""
    else:
        content += f"""
<div class="stack">
  {render_role_view(role, rows, day)}
</div>
"""

    return page_template(content, message=message, errors=errors, role=role, day=day)


def room_plan_script() -> str:
    return """
<script>
(() => {
  const form = document.getElementById("reservation-form");
  if (!form) return;

  const dateInput = document.getElementById("reservation_date");
  const statusSelect = document.getElementById("status");
  const cancellationReason = document.getElementById("cancellation_reason");
  const cancellationReasonField = document.getElementById("cancellation_reason_field");
  const nodes = Array.from(document.querySelectorAll(".room-node, .table-node"));
  const serviceOptions = Array.from(document.querySelectorAll(".service-option"));
  const timeInputs = Array.from(document.querySelectorAll("[data-time-input]"));
  let timer = null;

  function syncCancellationRequirement() {
    if (!statusSelect || !cancellationReason || !cancellationReasonField) return;
    const cancelled = statusSelect.value === "cancelled";
    cancellationReason.required = cancelled;
    cancellationReasonField.classList.toggle("is-hidden", !cancelled);
  }

  function formatTime(totalMinutes) {
    const minutesInDay = 24 * 60;
    const normalized = ((totalMinutes % minutesInDay) + minutesInDay) % minutesInDay;
    const hours = String(Math.floor(normalized / 60)).padStart(2, "0");
    const minutes = String(normalized % 60).padStart(2, "0");
    return `${hours}:${minutes}`;
  }

  function normalizeClockText(value) {
    const raw = value.trim().replace(".", ":");
    if (!raw) return "";

    let hours = null;
    let minutes = 0;
    if (/^\\d{1,2}$/.test(raw)) {
      hours = Number(raw);
    } else if (/^\\d{3,4}$/.test(raw)) {
      hours = Number(raw.slice(0, -2));
      minutes = Number(raw.slice(-2));
    } else if (/^\\d{1,2}:\\d{0,2}$/.test(raw)) {
      const parts = raw.split(":");
      hours = Number(parts[0]);
      minutes = parts[1] ? Number(parts[1]) : 0;
    } else {
      return value;
    }

    if (Number.isNaN(hours) || Number.isNaN(minutes) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
      return value;
    }
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  }

  function normalizeTimeInput(input) {
    const normalized = normalizeClockText(input.value);
    if (normalized !== input.value) input.value = normalized;
  }

  function updateServiceOption(option) {
    const checkbox = option.querySelector('input[type="checkbox"]');
    const timeInput = option.querySelector("[data-time-input]");
    const extraInputs = Array.from(option.querySelectorAll(".service-extra select, .service-extra input"));
    const end = option.querySelector(".service-end");
    if (!checkbox) return;

    if (timeInput) timeInput.disabled = !checkbox.checked;
    extraInputs.forEach((input) => {
      input.disabled = !checkbox.checked;
    });
    if (!checkbox.checked) {
      if (end) end.textContent = "";
      return;
    }
    if (!timeInput) return;

    const duration = Number(timeInput.dataset.durationMinutes || 0);
    if (!duration || !timeInput.value) {
      if (end) end.textContent = "";
      return;
    }

    const [hours, minutes] = timeInput.value.split(":").map(Number);
    if (Number.isNaN(hours) || Number.isNaN(minutes)) return;
    if (end) end.textContent = `koniec ${formatTime(hours * 60 + minutes + duration)}`;
  }

  function applyAvailability(locations) {
    nodes.forEach((node) => {
      const info = locations[node.dataset.location] || { status: "free", label: "Wolne" };
      node.classList.toggle("is-busy", info.status === "occupied");
      const title = node.querySelector("title");
      if (title) title.textContent = info.label;
    });
  }

  function refreshAvailability() {
    if (!dateInput.value) return;
    const params = new URLSearchParams({
      date: dateInput.value,
    });
    fetch(`/api/availability?${params.toString()}`)
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (payload && payload.locations) applyAvailability(payload.locations);
      })
      .catch(() => {});
  }

  function scheduleRefresh() {
    window.clearTimeout(timer);
    timer = window.setTimeout(refreshAvailability, 150);
  }

  serviceOptions.forEach((option) => {
    const checkbox = option.querySelector('input[type="checkbox"]');
    const timeInput = option.querySelector("[data-time-input]");
    if (checkbox) checkbox.addEventListener("change", () => updateServiceOption(option));
    if (timeInput) timeInput.addEventListener("input", () => updateServiceOption(option));
    updateServiceOption(option);
  });

  timeInputs.forEach((input) => {
    input.addEventListener("blur", () => {
      normalizeTimeInput(input);
      const option = input.closest(".service-option");
      if (option) updateServiceOption(option);
    });
  });

  form.addEventListener("submit", () => {
    timeInputs.forEach(normalizeTimeInput);
  });

  dateInput.addEventListener("input", scheduleRefresh);
  if (statusSelect) statusSelect.addEventListener("change", syncCancellationRequirement);
  syncCancellationRequirement();
})();
</script>
"""


def render_schema_page(role: str = "manager", day: str = "today") -> bytes:
    sql = """
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
  animation_type TEXT,
  animation_at TIMESTAMPTZ,
  cake_enabled BOOLEAN NOT NULL DEFAULT false,
  cake_theme TEXT,
  cake_at TIMESTAMPTZ,
  fruit_enabled BOOLEAN NOT NULL DEFAULT false,
  fruit_plates INT,
  fruit_at TIMESTAMPTZ,
  culinary_workshops_enabled BOOLEAN NOT NULL DEFAULT false,
  culinary_workshops_type TEXT,
  culinary_workshops_at TIMESTAMPTZ,
  pinata_enabled BOOLEAN NOT NULL DEFAULT false,
  pinata_theme TEXT,
  pinata_at TIMESTAMPTZ,
  mascot_enabled BOOLEAN NOT NULL DEFAULT false,
  mascot_type TEXT,
  mascot_at TIMESTAMPTZ,
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
  cancellation_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_at > start_at),
  CHECK (status = 'active' OR length(trim(cancellation_reason)) > 0)
);

CREATE INDEX reservations_overlap_idx
  ON reservations (status, child_location, start_at, end_at);

CREATE TABLE reservation_history (
  id BIGSERIAL PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES reservations(id),
  action TEXT NOT NULL,
  changed_by_role TEXT NOT NULL,
  snapshot_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
    content = render_nav(normalize_role(role), normalize_day(day)) + f"""
<section>
  <div class="section-head">
    <div>
      <h2>Optymalna struktura bazy danych</h2>
      <p class="subtitle">Docelowo: PostgreSQL/Supabase z indeksami pod szybkie sprawdzanie nakładania terminów.</p>
    </div>
    <a class="button secondary" href="{link_for(normalize_role(role), normalize_day(day))}">Wróć do panelu</a>
  </div>
  <div class="section-body">
    <p>Kluczowa decyzja: rezerwacja jest jednym agregatem z godzinami usług, a historia zmian jest osobną tabelą append-only. Konflikty liczone są po przedziale <code>start_at/end_at</code>, statusie <code>active</code> i lokalizacjach.</p>
    <pre style="white-space: pre-wrap; border: 1px solid var(--line); padding: 14px; background: #0f172a; color: #e2e8f0; overflow-x: auto;"><code>{escape(sql.strip())}</code></pre>
  </div>
</section>
"""
    return page_template(content, role=role, day=day)


def render_history_page(reservation_id: int, role: str, day: str) -> bytes:
    row = get_reservation(reservation_id)
    if row is None:
        return page_template(
            render_nav(role, day) + '<section><div class="empty">Nie znaleziono historii rezerwacji.</div></section>',
            role=role,
            day=day,
        )
    history = get_history(reservation_id)
    rows = []
    for item in history:
        snapshot = json.loads(item["snapshot_json"])
        rows.append(
            f"""
            <tr>
              <td><strong>{escape(item["action"])}</strong><br><span class="muted">{format_datetime(item["created_at"])}</span></td>
              <td>{escape(item["changed_by_role"])}</td>
              <td>{escape(snapshot.get("birthday_child_name", ""))}<br><span class="muted">{escape(snapshot.get("status", ""))}</span></td>
              <td>{escape(snapshot.get("child_location", ""))}<br><span class="muted">{format_time(snapshot.get("start_at", ""))}-{format_time(snapshot.get("end_at", ""))}</span></td>
            </tr>
            """
        )
    body = (
        f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>Zmiana</th><th>Rola</th><th>Rezerwacja</th><th>Lokalizacja</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        """
        if rows
        else '<div class="empty">Brak wpisów historii.</div>'
    )
    content = render_nav(role, day) + f"""
<section>
  <div class="section-head">
    <div>
      <h2>Historia rezerwacji</h2>
      <p class="subtitle">{escape(row["birthday_child_name"])} · {format_date(row["start_at"])}</p>
    </div>
    <a class="button secondary" href="{link_for(role, day, edit=reservation_id)}">Wróć do edycji</a>
  </div>
  {body}
</section>
"""
    return page_template(content, role=role, day=day)


def parse_post(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values if key == "adult_location" else values[-1] for key, values in parsed.items()}


def csv_response() -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Data",
            "Start imprezy",
            "Dzieci",
            "Dorośli",
            "Rodzic",
            "Solenizant",
            "Wiek",
            "Sala dzieci",
            "Lokalizacja dorosłych",
            "Animacja",
            "Rodzaj animacji",
            "Animacja czas",
            "Tort",
            "Motyw tortu",
            "Tort czas",
            "Owoce",
            "Liczba talerzy owoców",
            "Godzina owoców",
            "Warsztaty",
            "Rodzaj warsztatów",
            "Warsztaty czas",
            "Piniata",
            "Motyw piniaty",
            "Piniata czas",
            "Maskotka",
            "Rodzaj maskotki",
            "Maskotka czas",
            "Status",
            "Powód anulowania",
            "Notatki",
        ]
    )
    for row in get_all_reservations():
        writer.writerow(
            [
                row["id"],
                format_date(row["start_at"]),
                format_time(row["start_at"]),
                row["children_count"],
                row["adults_count"],
                row["parent_name"],
                row["birthday_child_name"],
                row["birthday_child_age"],
                row["child_location"],
                display_locations(row["adult_location"]),
                "Tak" if is_enabled(row, "animation_enabled") else "Nie",
                row["animation_type"] or "",
                format_service_window(row["animation_at"], SERVICE_DURATIONS["animation_at"]),
                "Tak" if is_enabled(row, "cake_enabled") else "Nie",
                row["cake_theme"] or "",
                format_service_window(row["cake_at"], SERVICE_DURATIONS["cake_at"]),
                "Tak" if is_enabled(row, "fruit_enabled") else "Nie",
                row["fruit_plates"] or "",
                format_time(row["fruit_at"]),
                "Tak" if is_enabled(row, "culinary_workshops_enabled") else "Nie",
                row["culinary_workshops_type"] or "",
                format_service_window(row["culinary_workshops_at"], SERVICE_DURATIONS["culinary_workshops_at"]),
                "Tak" if is_enabled(row, "pinata_enabled") else "Nie",
                row["pinata_theme"] or "",
                format_service_window(row["pinata_at"], SERVICE_DURATIONS["pinata_at"]),
                "Tak" if is_enabled(row, "mascot_enabled") else "Nie",
                row["mascot_type"] or "",
                format_service_window(row["mascot_at"], SERVICE_DURATIONS["mascot_at"]),
                STATUS_LABELS[row["status"]],
                row["cancellation_reason"],
                row["notes"],
            ]
        )
    return ("\ufeff" + output.getvalue()).encode("utf-8")


class ReservationHandler(BaseHTTPRequestHandler):
    def send_bytes(
        self,
        payload: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "text/html; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        role = normalize_role(query.get("role", ["manager"])[0])
        day = normalize_day(query.get("day", ["today"])[0])

        if parsed.path == "/":
            message = query.get("message", [""])[0]
            edit_values = query.get("edit", [""])[0]
            edit_id = int(edit_values) if edit_values.isdigit() else None
            self.send_bytes(render_home(role=role, day=day, message=message, edit_id=edit_id))
            return

        if parsed.path == "/logo.png":
            if LOGO_PATH.exists():
                self.send_bytes(LOGO_PATH.read_bytes(), content_type="image/png")
                return
            self.send_bytes(b"", status=HTTPStatus.NOT_FOUND, content_type="image/png")
            return

        if parsed.path == "/schema":
            self.send_bytes(render_schema_page(role=role, day=day))
            return

        if parsed.path == "/history":
            reservation_value = query.get("id", [""])[0]
            if reservation_value.isdigit():
                self.send_bytes(render_history_page(int(reservation_value), role, day))
                return

        if parsed.path == "/api/availability":
            exclude_value = query.get("exclude_id", [""])[0]
            exclude_id = int(exclude_value) if exclude_value.isdigit() else None
            locations = availability_for(
                query.get("date", [""])[0],
                exclude_id=exclude_id,
            )
            payload = json.dumps({"locations": locations}, ensure_ascii=False).encode("utf-8")
            self.send_bytes(payload, content_type="application/json; charset=utf-8")
            return

        if parsed.path == "/export":
            self.send_bytes(
                csv_response(),
                content_type="text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="ikidspark-rezerwacje.csv"'},
            )
            return

        self.send_bytes(
            page_template('<div class="panel empty">Nie znaleziono strony.</div>', role=role, day=day),
            status=HTTPStatus.NOT_FOUND,
        )

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/logo.png":
            self.send_response(HTTPStatus.OK if LOGO_PATH.exists() else HTTPStatus.NOT_FOUND)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            return

        if parsed.path in {"/", "/export", "/schema", "/api/availability"}:
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                "text/csv; charset=utf-8" if parsed.path == "/export" else "text/html; charset=utf-8",
            )
            self.end_headers()
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        role = normalize_role(query.get("role", ["manager"])[0])
        day = normalize_day(query.get("day", ["today"])[0])
        data = parse_post(self)

        if parsed.path == "/reservations":
            raw_id = data.get("id", "")
            reservation_id = int(raw_id) if raw_id.isdigit() else None
            values, errors = validate_reservation(data, reservation_id=reservation_id)
            if errors:
                self.send_bytes(
                    render_home(role=role, day=day, values=values, errors=errors),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            saved_id = save_reservation(values, role=role)
            message = "Rezerwacja została zaktualizowana." if reservation_id else "Rezerwacja została zapisana."
            self.redirect(link_for(role, day, edit=saved_id, message=message))
            return

        if parsed.path == "/delete":
            raw_id = data.get("id", "")
            if raw_id.isdigit() and delete_reservation(int(raw_id)):
                self.redirect(link_for(role, day, message="Rezerwacja została usunięta."))
            else:
                self.redirect(link_for(role, day, message="Nie znaleziono rezerwacji do usunięcia."))
            return

        payload = json.dumps({"error": "Unsupported route"}, ensure_ascii=False).encode("utf-8")
        self.send_bytes(payload, status=HTTPStatus.NOT_FOUND, content_type="application/json; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}")


def run() -> None:
    init_db()
    server = None
    selected_port = PORT
    for candidate_port in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer((HOST, candidate_port), ReservationHandler)
            selected_port = candidate_port
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
    if server is None:
        raise RuntimeError(f"Nie znaleziono wolnego portu w zakresie {PORT}-{PORT + 19}.")

    print(f"{APP_TITLE} działa pod adresem http://{HOST}:{selected_port}")
    print("Zatrzymaj serwer skrótem Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymano serwer.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
