from __future__ import annotations

import errno
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import unicodedata
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from email.parser import BytesParser
from email.policy import HTTP
from urllib.parse import parse_qs, quote, urlencode, urlparse
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from ikidspark_pwa import app_icon_png as build_app_icon_png
from ikidspark_config import (
    ADULT_LOCATION_GROUPS,
    ADULT_LOCATIONS,
    ALL_LOCATIONS,
    ANIMATION_GROUPS,
    ANIMATION_TYPES,
    ANIMATORS,
    CAKE_CANDLE_LABELS,
    CAKE_CANDLE_TYPES,
    DAY_FILTERS,
    EMPTY_LOCATION,
    INVENTORY_CATEGORIES,
    INVENTORY_CATEGORY_LABELS,
    LEGACY_ADULT_LOCATION_RENAMES,
    LEGACY_CHILD_LOCATION_RENAMES,
    LOCATION_GROUPS,
    LOCATION_SEPARATOR,
    MASCOT_TYPES,
    MONTH_FULL_LABELS,
    MONTH_STANDALONE_LABELS,
    PARTY_ROOMS,
    PLAN_HOTSPOTS,
    PLAN_VIEWBOX,
    PLAN_WALLS,
    RESERVATION_COLORS,
    ROLE_DEFS,
    ROLE_NAV_ICONS,
    ROOM_CAPACITY,
    RESERVATION_AUTHORS,
    SERVICE_DURATIONS,
    SERVICE_OVERLAP_MESSAGE,
    STAGE_BLOCK_END,
    STAGE_BLOCK_MESSAGE,
    STAGE_BLOCK_CONFIRM_PROMPT,
    STAGE_BLOCK_START,
    STATUS_LABELS,
    TABLE_GROUP_NUMBERS,
    TABLE_NUMBERS,
    TABLE_ZONE_BY_NUMBER,
    WAITERS,
    WEEKDAY_FULL_LABELS,
    WEEKDAY_LABELS,
    WORKSHOP_TYPES,
    format_table_range,
)
from ikidspark_export import build_csv_response
import ikidspark_inventory as inventory

load_dotenv(Path(__file__).with_name(".env"))

APP_TITLE = "iKids Park - Rezerwacje urodzin"
APP_SHORT_TITLE = "iKids Park"
# Bump only when service-worker logic changes. Icon URLs use logo mtime separately.
PWA_CACHE_NAME = "ikidspark-pwa-v19"
PWA_ICON_SIZES = (48, 72, 96, 144, 192, 512)
PWA_MANIFEST_ID = "/"
APP_TIMEZONE = ZoneInfo(os.environ.get("IKIDS_TIMEZONE", "Europe/Warsaw"))
DbRow = dict[str, Any]
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_MODE = os.environ.get("IKIDS_DB_MODE", "").strip().lower()
USE_LOCAL_SQLITE = DB_MODE == "sqlite" or (not DATABASE_URL and DB_MODE != "supabase")
LOCAL_DB_PATH = Path(__file__).with_name(os.environ.get("IKIDS_LOCAL_DB_PATH", "reservations-local.db"))
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "").strip()
GOOGLE_SHEETS_RANGE = os.environ.get("GOOGLE_SHEETS_RANGE", "A1:Z250").strip()
GOOGLE_SHEETS_CACHE_SECONDS = int(os.environ.get("GOOGLE_SHEETS_CACHE_SECONDS", "600"))
RESERVATIONS_CACHE_SECONDS = int(os.environ.get("RESERVATIONS_CACHE_SECONDS", "120"))
LOGO_PATH = Path(__file__).with_name("logo.png")
MENU_LOGO_PATH = Path(__file__).with_name("logox221.png")
PWA_LOGO_PATH = Path(__file__).with_name("pwalogo.png")
ROOM_PLAN_SVG_PATH = Path(__file__).with_name("14.svg")
ROOM_PLAN_PNG_PATH = Path(__file__).with_name("assets") / "room-plan.png"
SOURCE_PATH = Path(__file__)
SOURCE_MTIME = SOURCE_PATH.stat().st_mtime
_RELOAD_LOCK = threading.Lock()
_RELOAD_TIMER: threading.Timer | None = None
_DEV_RELOAD_DEBOUNCE_SEC = 1.5
CA_CERT_PATH = Path(__file__).with_name("ikids-local-ca.crt")
CA_KEY_PATH = Path(__file__).with_name("ikids-local-ca.key")
CERT_PATH = Path(__file__).with_name("ikids-local.crt")
KEY_PATH = Path(__file__).with_name("ikids-local.key")
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
DEFAULT_LOCAL_DOMAINS = ("ikids.pl",)

def logo_asset_url() -> str:
    if LOGO_PATH.exists():
        return f"/logo.png?v={int(LOGO_PATH.stat().st_mtime)}"
    return "/logo.png"


def menu_logo_asset_url() -> str:
    if MENU_LOGO_PATH.exists():
        return f"/menu-logo.png?v={int(MENU_LOGO_PATH.stat().st_mtime)}"
    return logo_asset_url()

def week_month_label(week_days: list[date]) -> str:
    month_counts: dict[int, int] = {}
    for week_day in week_days:
        month_counts[week_day.month] = month_counts.get(week_day.month, 0) + 1
    dominant_month = max(month_counts, key=month_counts.get)
    return MONTH_STANDALONE_LABELS[dominant_month - 1]


def week_year_label(week_days: list[date]) -> int:
    year_counts: dict[int, int] = {}
    for week_day in week_days:
        year_counts[week_day.year] = year_counts.get(week_day.year, 0) + 1
    return max(year_counts, key=year_counts.get)


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError(
            "Brak DATABASE_URL. Wklej connection string z Supabase → Project Settings → Database "
            "(URI) do pliku .env, np. postgresql://postgres.xxx:HASLO@aws-0-...pooler.supabase.com:6543/postgres"
        )
    return DATABASE_URL


def adapt_sql(query: str) -> str:
    """Convert ? placeholders to psycopg %s."""
    if USE_LOCAL_SQLITE:
        return query
    return query.replace("?", "%s")


def connect() -> psycopg.Connection | sqlite3.Connection:
    if USE_LOCAL_SQLITE:
        conn = sqlite3.connect(LOCAL_DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
    # Transaction pooler (PgBouncer) rejects prepared statements.
    return psycopg.connect(
        require_database_url(),
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=10,
    )


def table_columns(conn: psycopg.Connection | sqlite3.Connection, table: str) -> set[str]:
    if USE_LOCAL_SQLITE:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        """.replace("?", "%s"),
        (table,),
    ).fetchall()
    return {row["column_name"] for row in rows}


def normalize_db_row(row: Any) -> DbRow:
    return dict(row)


def create_schema(conn: psycopg.Connection | sqlite3.Connection) -> None:
    reservation_id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if USE_LOCAL_SQLITE else "BIGSERIAL PRIMARY KEY"
    history_id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if USE_LOCAL_SQLITE else "BIGSERIAL PRIMARY KEY"
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS reservations (
            id {reservation_id_type},
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
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS reservation_history (
            id {history_id_type},
            reservation_id BIGINT NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            changed_by_role TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL
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
    for statement in inventory.inventory_schema_sql(use_sqlite=USE_LOCAL_SQLITE):
        conn.execute(statement)


def ensure_current_schema(conn: psycopg.Connection | sqlite3.Connection) -> None:
    columns = table_columns(conn, "reservations")
    schema_columns = {
        "animations_json": "TEXT",
        "cake_weight": "TEXT",
        "cake_sponge": "TEXT",
        "cake_filling": "TEXT",
        "cake_cream": "TEXT",
        "cake_image_data": "TEXT",
        "cake_candle": "TEXT",
        "guest_total": "INTEGER",
        "reservation_type": "TEXT NOT NULL DEFAULT 'banquet'",
        "parent_phone": "TEXT",
        "assigned_waiter": "TEXT",
        "assigned_animator": "TEXT",
        "created_by": "TEXT",
        "cooperation_enabled": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in schema_columns.items():
        if column in columns:
            continue
        if USE_LOCAL_SQLITE:
            conn.execute(f"ALTER TABLE reservations ADD COLUMN {column} {definition}")
        else:
            conn.execute(f"ALTER TABLE reservations ADD COLUMN IF NOT EXISTS {column} {definition}")

    inventory_columns = table_columns(conn, "inventory_items")
    if inventory_columns and "ean" not in inventory_columns:
        if USE_LOCAL_SQLITE:
            conn.execute("ALTER TABLE inventory_items ADD COLUMN ean TEXT NOT NULL DEFAULT ''")
        else:
            conn.execute("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS ean TEXT NOT NULL DEFAULT ''")
        inventory_columns = table_columns(conn, "inventory_items")
    if inventory_columns and "ean" in inventory_columns:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_items_ean
            ON inventory_items(ean) WHERE ean != ''
            """
        )
    inventory.migrate_inventory_schema(conn, use_sqlite=USE_LOCAL_SQLITE)

def migrate_location_names(conn: psycopg.Connection | sqlite3.Connection) -> None:
    for old_name, new_name in LEGACY_CHILD_LOCATION_RENAMES.items():
        conn.execute(
            adapt_sql("UPDATE reservations SET child_location = ? WHERE child_location = ?"),
            (new_name, old_name),
        )
    for old_name, new_name in LEGACY_ADULT_LOCATION_RENAMES.items():
        conn.execute(
            adapt_sql("UPDATE reservations SET adult_location = ? WHERE adult_location = ?"),
            (new_name, old_name),
        )


def init_db() -> None:
    inventory.bind_db(db_rows=db_rows, db_one=db_one, execute=execute, now_iso=now_iso)
    with connect() as conn:
        create_schema(conn)
        ensure_current_schema(conn)
        migrate_location_names(conn)
        for statement in inventory.inventory_schema_sql(use_sqlite=USE_LOCAL_SQLITE):
            conn.execute(statement)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db_rows(query: str, params: tuple = ()) -> list[DbRow]:
    with connect() as conn:
        return [normalize_db_row(row) for row in conn.execute(adapt_sql(query), params).fetchall()]


def db_one(query: str, params: tuple = ()) -> DbRow | None:
    with connect() as conn:
        row = conn.execute(adapt_sql(query), params).fetchone()
        return normalize_db_row(row) if row is not None else None


def execute(query: str, params: tuple = ()) -> int:
    sql = adapt_sql(query).rstrip().rstrip(";")
    with connect() as conn:
        if USE_LOCAL_SQLITE:
            result = conn.execute(sql, params)
            return int(result.lastrowid or 0)
        if sql.lstrip().upper().startswith("INSERT") and "RETURNING" not in sql.upper():
            sql = f"{sql} RETURNING id"
        result = conn.execute(sql, params)
        if result.description:
            row = result.fetchone()
            if row and "id" in row and row["id"] is not None:
                return int(row["id"])
        return 0


inventory.bind_db(db_rows=db_rows, db_one=db_one, execute=execute, now_iso=now_iso)


def escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def normalize_role(role: str | None) -> str:
    return role if role in ROLE_DEFS else "manager"


def normalize_page_role(role: str | None) -> str:
    if role == "home":
        return "home"
    return normalize_role(role)


def can_modify_reservations(role: str) -> bool:
    return normalize_role(role) == "organizer"


def can_assign_waiter(role: str) -> bool:
    return normalize_role(role) == "manager"


def can_assign_animator(role: str) -> bool:
    return normalize_role(role) == "animators"


def can_manage_inventory(role: str) -> bool:
    return normalize_role(role) in {"organizer", "manager"}


def normalize_day(day: str | None) -> str:
    if not day:
        return "today"
    if day in DAY_FILTERS:
        return day
    if parse_date_for_api(day) is not None:
        return day
    return "today"


def current_app_date() -> date:
    return datetime.now(APP_TIMEZONE).date()


def selected_day(day_key: str) -> date:
    if day_key in DAY_FILTERS:
        _, offset = DAY_FILTERS[day_key]
        return current_app_date() + timedelta(days=offset)
    parsed = parse_date_for_api(day_key)
    return parsed if parsed is not None else current_app_date()


def week_start(target_day: date) -> date:
    return target_day - timedelta(days=target_day.weekday())


def week_dates(target_day: date) -> list[date]:
    start = week_start(target_day)
    return [start + timedelta(days=offset) for offset in range(7)]


def calendar_week_pages(
    anchor_day: date,
    year: int | None = None,
    *,
    radius: int = 8,
    include_day: date | None = None,
) -> list[tuple[int, list[date]]]:
    """Build a short week strip around today (not the whole year — that crushed mobile loads)."""
    del year  # kept for call-site compatibility
    offsets = set(range(-radius, radius + 1))
    if include_day is not None:
        offsets.add(week_page_offset_for_day(anchor_day, include_day))
    pages: list[tuple[int, list[date]]] = []
    for offset in sorted(offsets):
        center = anchor_day + timedelta(days=7 * offset)
        days = [center + timedelta(days=delta) for delta in range(-3, 4)]
        pages.append((offset, days))
    return pages


def week_page_offset_for_day(anchor_day: date, target_day: date) -> int:
    delta = (target_day - anchor_day).days
    return (delta + 3) // 7


def day_query(target_day: date) -> str:
    for key, (_, offset) in DAY_FILTERS.items():
        if selected_day(key) == target_day:
            return key
    return target_day.isoformat()


def parse_birthday_children(data: dict[str, object]) -> list[dict[str, object]]:
    raw_names = data.get("birthday_child_name", "")
    raw_ages = data.get("birthday_child_age", "")
    names = [raw_names] if isinstance(raw_names, str) else [str(name) for name in raw_names]
    ages = [raw_ages] if isinstance(raw_ages, str) else [str(age) for age in raw_ages]
    length = max(len(names), len(ages))

    children: list[dict[str, object]] = []
    for index in range(length):
        name = str(names[index] if index < len(names) else "").strip()
        age_raw = str(ages[index] if index < len(ages) else "").strip()
        if not name and not age_raw:
            continue
        try:
            age = int(age_raw)
        except (TypeError, ValueError):
            age = None
        children.append({"name": name, "age": age})
    return children


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def parse_animations(data: dict[str, object]) -> list[dict[str, object]]:
    types = _as_str_list(data.get("animation_type"))
    starts = _as_str_list(data.get("animation_at"))
    length = max(len(types), len(starts), 0)
    animations: list[dict[str, object]] = []
    for index in range(length):
        anim_type = str(types[index] if index < len(types) else "").strip()
        anim_at = str(starts[index] if index < len(starts) else "").strip()
        if not anim_type and not anim_at:
            continue
        animations.append({"type": anim_type, "at": anim_at})
    return animations


def animations_json_value(animations: list[dict[str, object]]) -> str:
    payload = [{"type": item["type"], "at": item["at"]} for item in animations]
    return json.dumps(payload, ensure_ascii=False)


def animations_from_row(row: DbRow | dict[str, object]) -> list[dict[str, object]]:
    keys = row.keys() if hasattr(row, "keys") else row
    raw_json = row["animations_json"] if "animations_json" in keys else None
    if raw_json:
        try:
            parsed = json.loads(str(raw_json))
            if isinstance(parsed, list) and parsed:
                items: list[dict[str, object]] = []
                for item in parsed:
                    anim_type = str(item.get("type", "")).strip()
                    anim_at = str(item.get("at", "")).strip()
                    if anim_type or anim_at:
                        items.append({"type": anim_type, "at": anim_at})
                if items:
                    return items
        except json.JSONDecodeError:
            pass
    enabled = is_enabled(row, "animation_enabled")
    anim_type = str(row["animation_type"] or "").strip() if "animation_type" in keys else ""
    anim_at = row["animation_at"] if "animation_at" in keys else None
    if enabled or anim_type or anim_at:
        return [{"type": anim_type, "at": anim_at or ""}]
    return []


def animations_for_form(row_or_values: DbRow | dict[str, object]) -> list[dict[str, object]]:
    items = animations_from_row(row_or_values)
    formatted: list[dict[str, object]] = []
    for item in items:
        at_value = item.get("at", "")
        if isinstance(at_value, str) and "T" in at_value:
            at_label = format_time(at_value)
        else:
            at_label = format_time(at_value) if at_value else str(at_value or "")
        formatted.append({"type": item.get("type", ""), "at": at_label})
    return formatted


def birthday_children_from_row(row: DbRow | dict[str, object]) -> list[dict[str, object]]:
    raw_json = row["birthday_children_json"] if "birthday_children_json" in row.keys() else None
    if raw_json:
        try:
            parsed = json.loads(str(raw_json))
            if isinstance(parsed, list) and parsed:
                return [
                    {"name": str(item.get("name", "")).strip(), "age": item.get("age")}
                    for item in parsed
                    if str(item.get("name", "")).strip()
                ]
        except json.JSONDecodeError:
            pass
    name = str(row.get("birthday_child_name", "") if isinstance(row, dict) else row["birthday_child_name"]).strip()
    age = row.get("birthday_child_age") if isinstance(row, dict) else row["birthday_child_age"]
    if name:
        return [{"name": name, "age": age}]
    return []


def format_birthday_children(row: DbRow | dict[str, object]) -> str:
    children = birthday_children_from_row(row)
    if not children:
        return "Brak solenizanta"
    parts = []
    for child in children:
        name = escape(str(child["name"]))
        age = child.get("age")
        if age not in (None, ""):
            parts.append(f"{name}, {escape(age)} lat")
        else:
            parts.append(name)
    return ", ".join(parts)


def int_row_value(row: DbRow | dict[str, object], field: str) -> int:
    try:
        value = row.get(field, 0)
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def guest_count_label(row: DbRow | dict[str, object]) -> str:
    if is_table_reservation(row):
        total = int_row_value(row, "guest_total") or int_row_value(row, "children_count") + int_row_value(row, "adults_count")
        return f"{total} os."
    children = int_row_value(row, "children_count")
    adults = int_row_value(row, "adults_count")
    total = children + adults
    return f"{total} os. ({children} dzieci, {adults} dorosłych)"


def workshop_children_label(row: DbRow | dict[str, object]) -> str:
    return f"{int_row_value(row, 'children_count')} dzieci"


def reservation_type(row: DbRow | dict[str, object]) -> str:
    value = str(row.get("reservation_type") or "banquet").strip()
    return value if value in {"banquet", "table"} else "banquet"


def is_table_reservation(row: DbRow | dict[str, object]) -> bool:
    return reservation_type(row) == "table"


def cake_detail_pairs(row: DbRow | dict[str, object]) -> list[tuple[str, str]]:
    labels = (
        ("Waga", "cake_weight"),
        ("Biszkopt", "cake_sponge"),
        ("Nadzienie", "cake_filling"),
        ("Krem", "cake_cream"),
        ("\u015awieczka", "cake_candle"),
    )
    pairs: list[tuple[str, str]] = []
    for label, field in labels:
        value_text = str(row.get(field) or "").strip()
        if field == "cake_candle":
            value_text = CAKE_CANDLE_LABELS.get(value_text, value_text)
        if value_text:
            pairs.append((label, value_text))
    return pairs


def cake_detail_parts(row: DbRow | dict[str, object]) -> list[str]:
    return [f"{label}: {value}" for label, value in cake_detail_pairs(row)]


def cake_details_label(row: DbRow | dict[str, object]) -> str:
    return " · ".join(cake_detail_parts(row))


def cake_image_markup(row: DbRow | dict[str, object]) -> str:
    image_data = str(row.get("cake_image_data") or "").strip()
    if not image_data.startswith(("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")):
        return ""
    return f'<img class="kitchen-cake-photo" src="{escape(image_data)}" alt="Zdjęcie tortu">'


def cake_kitchen_panel(row: DbRow | dict[str, object]) -> str:
    """Collapsible 50/50 kitchen layout: category/value list left, cake photo right."""
    theme = str(row.get("cake_theme") or "").strip() or "(brak)"
    specs: list[tuple[str, str]] = [("Motyw", theme)]
    specs.extend(cake_detail_pairs(row))
    serving = format_service_window(row.get("cake_at"), SERVICE_DURATIONS["cake_at"])
    if serving:
        specs.append(("Podanie", serving))

    specs_markup = "".join(
        f'<div class="kitchen-cake-spec"><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>'
        for label, value in specs
    )
    photo = cake_image_markup(row)
    photo_markup = (
        photo
        if photo
        else '<div class="kitchen-cake-photo-empty" aria-hidden="true">Brak zdjęcia</div>'
    )
    return f"""
      <details class="kitchen-cake-fold">
        <summary class="kitchen-cake-fold-summary">
          <span class="kitchen-cake-fold-title">Szczegóły i zdjęcie</span>
          <span class="kitchen-cake-fold-theme">{escape(theme)}</span>
        </summary>
        <div class="kitchen-cake-layout">
          <dl class="kitchen-cake-specs">{specs_markup}</dl>
          <div class="kitchen-cake-photo-col">{photo_markup}</div>
        </div>
      </details>
    """


def reservation_plan_tip(row: DbRow | dict[str, object]) -> str:
    if is_table_reservation(row):
        child_part = f"Rezerwacja: {str(row.get('parent_name') or '').strip() or 'gość'}"
    else:
        children = birthday_children_from_row(row)
        if children:
            child = children[0]
            name = str(child.get("name") or "").strip() or "Solenizant"
            age = child.get("age")
            child_part = f"{name}, {age} lat" if age not in (None, "") else name
        else:
            child_part = str(row.get("birthday_child_name") or "Solenizant").strip()
    waiters = assigned_waiters_from_row(row)
    animators = assigned_animators_from_row(row)
    waiter_part = ", ".join(waiters) if waiters else "brak kelnera"
    animator_part = ", ".join(animators) if animators else "brak animatora"
    return f"{child_part} · {waiter_part} · {animator_part}"


def banquet_info_title(row: DbRow | dict[str, object]) -> str:
    if isinstance(row, dict):
        parent = escape(str(row.get("parent_name", "")))
        sala = escape(display_location(row.get("child_location", "")))
        start_at = row.get("start_at", "")
    else:
        parent = escape(str(row["parent_name"]))
        sala = escape(display_location(row["child_location"]))
        start_at = row["start_at"]
    return (
        f"{format_birthday_children(row)} · sala {sala} · start {escape(format_time(start_at))} · rodzic {parent}"
    )


def render_banquet_header(row: DbRow | dict[str, object]) -> str:
    if isinstance(row, dict):
        parent = escape(str(row.get("parent_name", "")))
        sala = escape(display_location(row.get("child_location", "")))
        start_at = row.get("start_at", "")
    else:
        parent = escape(str(row["parent_name"]))
        sala = escape(display_location(row["child_location"]))
        start_at = row["start_at"]
    start = escape(format_time(start_at))
    solenizant = format_birthday_children(row)
    guests = escape(guest_count_label(row))
    return f"""
      <div class="banquet-header">
        <div class="banquet-header-item">
          <span class="banquet-header-label">Solenizant</span>
          <span class="banquet-header-value">{solenizant}</span>
        </div>
        <div class="banquet-header-item">
          <span class="banquet-header-label">Sala</span>
          <span class="banquet-header-value">{sala}</span>
        </div>
        <div class="banquet-header-item">
          <span class="banquet-header-label">Start</span>
          <span class="banquet-header-value">{start}</span>
        </div>
        <div class="banquet-header-item">
          <span class="banquet-header-label">Goście</span>
          <span class="banquet-header-value">{guests}</span>
        </div>
        <div class="banquet-header-item">
          <span class="banquet-header-label">Rodzic</span>
          <span class="banquet-header-value">{parent}</span>
        </div>
      </div>
    """


def birthday_children_json_value(children: list[dict[str, object]]) -> str:
    payload = [{"name": child["name"], "age": child["age"]} for child in children]
    return json.dumps(payload, ensure_ascii=False)


def service_time_windows(values: dict[str, object]) -> list[tuple[str, time, time]]:
    windows: list[tuple[str, time, time]] = []

    def add_window(field: str, start: time | None, duration: int | None) -> None:
        if start is None or duration is None:
            return
        end_dt = datetime.combine(date.today(), start) + timedelta(minutes=duration)
        windows.append((field, start, end_dt.time()))

    if values.get("animations"):
        for item in values.get("animations") or []:
            add_window(
                "animation_at",
                parse_time_value(str(item.get("at") or "")),
                SERVICE_DURATIONS["animation_at"],
            )
    elif values.get("animation_enabled"):
        add_window("animation_at", parse_time_value(field_time(values, "animation_at")), SERVICE_DURATIONS["animation_at"])
    if values.get("cake_enabled"):
        add_window("cake_at", parse_time_value(field_time(values, "cake_at")), SERVICE_DURATIONS["cake_at"])
    if values.get("culinary_workshops_enabled"):
        add_window(
            "culinary_workshops_at",
            parse_time_value(field_time(values, "culinary_workshops_at")),
            SERVICE_DURATIONS["culinary_workshops_at"],
        )
    if values.get("pinata_enabled"):
        add_window("pinata_at", parse_time_value(field_time(values, "pinata_at")), SERVICE_DURATIONS["pinata_at"])
    if values.get("mascot_enabled"):
        add_window("mascot_at", parse_time_value(field_time(values, "mascot_at")), SERVICE_DURATIONS["mascot_at"])
    return windows


def find_internal_time_overlaps(values: dict[str, object]) -> list[str]:
    windows = service_time_windows(values)
    conflicts: list[str] = []
    for index, (field_a, start_a, end_a) in enumerate(windows):
        for field_b, start_b, end_b in windows[index + 1 :]:
            if start_a < end_b and end_a > start_b:
                conflicts.extend([field_a, field_b])
    return sorted(set(conflicts))


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


_PERSON_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$", re.UNICODE)
_PHONE_DIGITS_RE = re.compile(r"^\d{9}$")


def normalize_person_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    return cleaned


def is_valid_person_name(value: str) -> bool:
    cleaned = normalize_person_name(value)
    if len(cleaned) < 2 or len(cleaned) > 80:
        return False
    return bool(_PERSON_NAME_RE.fullmatch(cleaned))


def parse_person_name_field(
    data: dict[str, object],
    errors: dict[str, str],
    field: str,
    label: str,
    *,
    required: bool = True,
) -> str:
    cleaned = normalize_person_name(str(data.get(field, "") or ""))
    if not cleaned:
        if required:
            errors[field] = f"Pole \"{label}\" jest wymagane."
        return ""
    if not is_valid_person_name(cleaned):
        errors[field] = (
            f"Pole \"{label}\" ma niepoprawny format "
            "(tylko litery, spacje, myślnik lub apostrof)."
        )
        return cleaned
    return cleaned


def normalize_phone_number(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0048"):
        digits = digits[4:]
    elif digits.startswith("48") and len(digits) >= 11:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = digits[1:]
    if not _PHONE_DIGITS_RE.fullmatch(digits):
        return None
    if digits[0] == "0":
        return None
    return f"{digits[0:3]} {digits[3:6]} {digits[6:9]}"


def parse_phone_field(
    data: dict[str, object],
    errors: dict[str, str],
    field: str,
    label: str,
    *,
    required: bool = True,
) -> str:
    raw = str(data.get(field, "") or "").strip()
    if not raw:
        if required:
            errors[field] = f"Pole \"{label}\" jest wymagane."
        return ""
    normalized = normalize_phone_number(raw)
    if normalized is None:
        errors[field] = (
            f"Pole \"{label}\" ma niepoprawny format "
            "(9 cyfr, np. 500 000 000 lub +48 500 000 000)."
        )
        return raw
    return normalized


def parse_optional_free_text(
    data: dict[str, object],
    errors: dict[str, str],
    field: str,
    label: str,
    *,
    maximum: int = 120,
) -> str:
    cleaned = re.sub(r"\s+", " ", str(data.get(field, "") or "").strip())
    if not cleaned:
        return ""
    if any(ord(char) < 32 and char not in "\t\n\r" for char in cleaned):
        errors[field] = f"Pole \"{label}\" zawiera niedozwolone znaki."
        return cleaned
    if "<" in cleaned or ">" in cleaned:
        errors[field] = f"Pole \"{label}\" nie może zawierać znaków < ani >."
        return cleaned
    if len(cleaned) > maximum:
        errors[field] = f"Pole \"{label}\" może mieć maksymalnie {maximum} znaków."
        return cleaned[:maximum]
    return cleaned


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
        if candidate and candidate not in locations and candidate != EMPTY_LOCATION:
            locations.append(candidate)
    return locations


def normalize_location(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        locations = location_values(value)
        return joined_locations(locations) if locations else EMPTY_LOCATION
    raw = str(value or "").strip()
    if not raw or raw == EMPTY_LOCATION:
        return EMPTY_LOCATION
    locations = location_values(raw)
    return joined_locations(locations) if locations else EMPTY_LOCATION


def display_location(value: object) -> str:
    normalized = normalize_location(value)
    return normalized if normalized != EMPTY_LOCATION else EMPTY_LOCATION


def joined_locations(values: object) -> str:
    return LOCATION_SEPARATOR.join(location_values(values))


def compact_table_locations(locations: list[str]) -> list[str]:
    grouped: dict[str, list[str]] = {}
    plain_locations: list[str] = []
    for location in locations:
        area, separator, table_number = location.partition(" - Stolik ")
        if separator and table_number.strip():
            grouped.setdefault(area, []).append(table_number.strip())
        else:
            plain_locations.append(location)

    compacted = [f"{area}: {', '.join(numbers)}" for area, numbers in grouped.items()]
    return compacted + plain_locations


def display_locations(value: object) -> str:
    locations = location_values(value)
    if not locations:
        return EMPTY_LOCATION
    return ", ".join(compact_table_locations(locations))


def reservation_locations(row: DbRow | dict[str, object]) -> set[str]:
    locations = set(location_values(row["adult_location"]))
    child = str(row["child_location"]).strip()
    if child and child != EMPTY_LOCATION:
        locations.add(child)
    return locations


def location_for_plan_number(number: int) -> str | None:
    if 1 <= number <= len(PARTY_ROOMS):
        return PARTY_ROOMS[number - 1]
    area = TABLE_ZONE_BY_NUMBER.get(number)
    if not area:
        return None
    return f"{area} - Stolik {number}"


def reservation_color_map(rows: list[DbRow]) -> dict[int, str]:
    active = [
        row
        for row in rows
        if str(row["status"] or "") == "active"
    ]
    active.sort(key=lambda row: (str(row["start_at"]), int(row["id"])))
    mapping: dict[int, str] = {}
    for index, row in enumerate(active[: len(RESERVATION_COLORS)]):
        mapping[int(row["id"])] = RESERVATION_COLORS[index]
    return mapping


def room_plan_asset_url() -> str:
    plan_version = int(
        max(
            Path(__file__).stat().st_mtime,
            ROOM_PLAN_PNG_PATH.stat().st_mtime if ROOM_PLAN_PNG_PATH.exists() else 0,
            ROOM_PLAN_SVG_PATH.stat().st_mtime if ROOM_PLAN_SVG_PATH.exists() else 0,
        )
    )
    if ROOM_PLAN_PNG_PATH.exists():
        return f"/room-plan.png?v={plan_version}"
    if ROOM_PLAN_SVG_PATH.exists():
        return f"/room-plan.svg?v={plan_version}"
    return "/room-plan.svg"


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
) -> list[DbRow]:
    params: list[object] = [end_at, start_at]
    exclude_sql = ""
    if exclude_id:
        exclude_sql = "AND id != ?"
        params.append(exclude_id)

    requested_locations = set(adult_locations)
    if child_location and child_location != EMPTY_LOCATION:
        requested_locations.add(child_location)
    if not requested_locations:
        return []

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
    form_reservation_type = str(data.get("reservation_type", "banquet")).strip()
    if form_reservation_type not in {"banquet", "table"}:
        form_reservation_type = "banquet"
    is_table = form_reservation_type == "table"
    reservation_day = parse_date(data.get("reservation_date", ""), errors, "reservation_date", "Data")
    party_start_time = parse_time_field(data, errors, "party_start_time", "Godzina", required=True)
    start_at = combine_day_time(reservation_day, party_start_time) or ""
    end_at = (
        datetime.combine(reservation_day + timedelta(days=1), time.min).isoformat(timespec="minutes")
        if reservation_day
        else ""
    )

    child_location = EMPTY_LOCATION if is_table else normalize_location(data.get("child_location", ""))
    if child_location != EMPTY_LOCATION and child_location not in ALL_LOCATIONS:
        errors["child_location"] = "Wybierz lokalizację dzieci z listy."

    adult_locations = location_values(data.get("adult_location", ""))
    invalid_adult_locations = [location for location in adult_locations if location not in ALL_LOCATIONS]
    if invalid_adult_locations:
        errors["adult_location"] = "Wybierz lokalizacje dorosłych z listy."
    if is_table and not adult_locations:
        errors["adult_location"] = "Wybierz stolik dla rezerwacji."
    adult_location = joined_locations(adult_locations) if adult_locations else EMPTY_LOCATION

    animation_enabled = 0 if is_table else checked_bool(data, "animation_enabled")
    cake_enabled = 0 if is_table else checked_bool(data, "cake_enabled")
    fruit_enabled = 0 if is_table else checked_bool(data, "fruit_enabled")
    drinks_enabled = 0
    culinary_workshops_enabled = 0 if is_table else checked_bool(data, "culinary_workshops_enabled")
    pinata_enabled = 0 if is_table else checked_bool(data, "pinata_enabled")
    mascot_enabled = 0 if is_table else checked_bool(data, "mascot_enabled")
    balloons_enabled = 0 if is_table else checked_bool(data, "balloons_enabled")
    cooperation_enabled = checked_bool(data, "cooperation_enabled")

    parsed_animations = parse_animations(data) if animation_enabled else []
    animations: list[dict[str, object]] = []
    stage_block_fields: list[str] = []
    for index, item in enumerate(parsed_animations):
        anim_type = str(item.get("type") or "").strip()
        anim_at_raw = str(item.get("at") or "").strip()
        if anim_type not in ANIMATION_TYPES:
            errors["animation_type"] = "Wybierz każdą animację z listy."
        anim_time = parse_time_value(anim_at_raw)
        if anim_time is None:
            errors["animation_at"] = "Podaj godzinę startu każdej animacji (HH:MM)."
        else:
            if overlaps_stage_block(anim_time, SERVICE_DURATIONS["animation_at"]):
                stage_block_fields.append("animation_at")
            animations.append(
                {
                    "type": anim_type,
                    "at": anim_time.strftime("%H:%M"),
                    "at_iso": combine_day_time(reservation_day, anim_time),
                }
            )
    if animation_enabled and not animations:
        errors["animation_type"] = "Dodaj co najmniej jedną animację albo wyłącz dodatek."

    animation_type = str(animations[0]["type"]) if animations else ""
    animation_time = parse_time_value(str(animations[0]["at"])) if animations else None

    fruit_plates = 0
    if fruit_enabled:
        fruit_plates = parse_int_field(data, errors, "fruit_plates", "Liczba talerzy owoców", 1, 200)

    cake_theme = parse_optional_free_text(data, errors, "cake_theme", "Motyw tortu", maximum=80)
    cake_weight = parse_optional_free_text(data, errors, "cake_weight", "Waga tortu", maximum=40)
    cake_sponge = parse_optional_free_text(data, errors, "cake_sponge", "Biszkopt", maximum=60)
    cake_filling = parse_optional_free_text(data, errors, "cake_filling", "Nadzienie", maximum=60)
    cake_cream = parse_optional_free_text(data, errors, "cake_cream", "Krem", maximum=60)
    cake_image_data = data.get("cake_image_data", "").strip()
    cake_candle = data.get("cake_candle", "").strip()
    if cake_enabled and not cake_theme:
        cake_theme = "(brak)"
    if cake_enabled and cake_candle and cake_candle not in CAKE_CANDLE_TYPES:
        errors["cake_candle"] = "Wybierz rodzaj \u015bwieczki."
    if cake_enabled and cake_image_data:
        if not cake_image_data.startswith(("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")):
            errors["cake_image_data"] = "Wybierz poprawne zdjęcie tortu."
        elif len(cake_image_data) > 1_500_000:
            errors["cake_image_data"] = "Zdjęcie tortu jest za duże. Wybierz mniejszy plik."
    if not cake_enabled:
        cake_theme = ""
        cake_weight = ""
        cake_sponge = ""
        cake_filling = ""
        cake_cream = ""
        cake_image_data = ""
        cake_candle = ""

    workshops_type = data.get("culinary_workshops_type", "").strip()
    if culinary_workshops_enabled and workshops_type not in WORKSHOP_TYPES:
        errors["culinary_workshops_type"] = "Wybierz rodzaj warsztatów."
    if not culinary_workshops_enabled:
        workshops_type = ""

    pinata_theme = parse_optional_free_text(data, errors, "pinata_theme", "Motyw piniaty", maximum=80)
    if pinata_enabled and not pinata_theme:
        pinata_theme = "(brak)"
    if not pinata_enabled:
        pinata_theme = ""

    mascot_type = data.get("mascot_type", "").strip()
    if mascot_enabled and mascot_type not in MASCOT_TYPES:
        errors["mascot_type"] = "Wybierz maskotkę."
    if not mascot_enabled:
        mascot_type = ""

    balloons_description = parse_optional_free_text(
        data, errors, "balloons_description", "Opis balonów", maximum=120
    )
    if balloons_enabled and not balloons_description:
        balloons_description = "(brak)"
    if not balloons_enabled:
        balloons_description = ""

    cake_time = parse_time_field(data, errors, "cake_at", "Start tortu", bool(cake_enabled))
    fruit_time = party_start_time
    drinks_time = None
    balloons_time = party_start_time
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
        ("cake_at", cake_time, SERVICE_DURATIONS["cake_at"]),
        ("culinary_workshops_at", workshops_time, SERVICE_DURATIONS["culinary_workshops_at"]),
        ("pinata_at", pinata_time, SERVICE_DURATIONS["pinata_at"]),
        ("mascot_at", mascot_time, SERVICE_DURATIONS["mascot_at"]),
    ):
        if overlaps_stage_block(value, duration):
            stage_block_fields.append(field)

    if stage_block_fields and not checked_bool(data, "stage_block_acknowledged"):
        for field in dict.fromkeys(stage_block_fields):
            errors[field] = STAGE_BLOCK_MESSAGE

    parent_name = parse_person_name_field(data, errors, "parent_name", "Rodzic / osoba rezerwująca")
    parent_phone = parse_phone_field(data, errors, "parent_phone", "Telefon")
    created_by = str(data.get("created_by", "") or "").strip()
    if created_by not in RESERVATION_AUTHORS:
        errors["created_by"] = "Wybierz osobę, która dodała rezerwację."
        created_by = ""
    if is_table:
        guest_total = parse_int_field(data, errors, "guest_total", "Liczba gości", 1, 240)
        birthday_children = []
        primary_child = {"name": parent_name or "Rezerwacja stolika", "age": 1}
    else:
        guest_total = 0
        birthday_children = parse_birthday_children(data)
        if not birthday_children:
            errors["birthday_child_name"] = "Dodaj co najmniej jednego solenizanta."
        for index, child in enumerate(birthday_children):
            name = normalize_person_name(str(child.get("name") or ""))
            child["name"] = name
            if not name:
                errors["birthday_child_name"] = "Każdy solenizant musi mieć imię."
            elif not is_valid_person_name(name):
                errors["birthday_child_name"] = (
                    "Imię solenizanta ma niepoprawny format "
                    "(tylko litery, spacje, myślnik lub apostrof)."
                )
            age = child.get("age")
            if age is None:
                errors["birthday_child_age"] = "Podaj wiek każdego solenizanta (1-18 lat)."
            elif not isinstance(age, int) or age < 1 or age > 18:
                errors["birthday_child_age"] = "Wiek solenizanta musi być w zakresie 1-18 lat."
        primary_child = birthday_children[0] if birthday_children else {"name": "", "age": 1}

    overlap_fields = find_internal_time_overlaps(
        {
            "animations": [{"type": item["type"], "at": item["at"]} for item in animations],
            "animation_enabled": 1 if animations else 0,
            "animation_at": animations[0]["at"] if animations else "",
            "cake_enabled": cake_enabled,
            "cake_at": cake_time.strftime("%H:%M") if cake_time else "",
            "culinary_workshops_enabled": culinary_workshops_enabled,
            "culinary_workshops_at": workshops_time.strftime("%H:%M") if workshops_time else "",
            "pinata_enabled": pinata_enabled,
            "pinata_at": pinata_time.strftime("%H:%M") if pinata_time else "",
            "mascot_enabled": mascot_enabled,
            "mascot_at": mascot_time.strftime("%H:%M") if mascot_time else "",
        }
    )
    for field in overlap_fields:
        errors[field] = SERVICE_OVERLAP_MESSAGE

    status = data.get("status", "active").strip()
    if status not in STATUS_LABELS:
        errors["status"] = "Wybierz poprawny status rezerwacji."

    cancellation_reason = parse_optional_free_text(
        data, errors, "cancellation_reason", "Powód anulowania", maximum=300
    )
    if status == "cancelled" and not cancellation_reason:
        errors["cancellation_reason"] = "Powód anulowania jest wymagany przy statusie Anulowana."
    if status == "active":
        cancellation_reason = ""

    notes = parse_optional_free_text(data, errors, "notes", "Uwagi", maximum=1000)

    raw_inventory_lines = inventory.parse_inventory_lines_payload(data) if not is_table else []
    inventory_lines, inventory_errors = inventory.validate_inventory_form_lines(
        raw_inventory_lines,
        is_table=is_table,
    )
    errors.update(inventory_errors)

    form_animations = [{"type": item["type"], "at": item["at"]} for item in animations]
    if animation_enabled and not form_animations and parsed_animations:
        form_animations = [
            {"type": str(item.get("type") or ""), "at": str(item.get("at") or "")}
            for item in parsed_animations
        ]

    cleaned: dict[str, object] = {
        "id": reservation_id,
        "reservation_date": data.get("reservation_date", "").strip(),
        "party_start_time": data.get("party_start_time", "").strip(),
        "start_at": start_at or "",
        "end_at": end_at or "",
        "children_count": guest_total if is_table else parse_int_field(data, errors, "children_count", "Liczba dzieci", 1, 120),
        "adults_count": 0 if is_table else parse_int_field(data, errors, "adults_count", "Liczba dorosłych", 0, 120),
        "guest_total": guest_total if is_table else None,
        "reservation_type": form_reservation_type,
        "parent_name": parent_name,
        "parent_phone": parent_phone,
        "created_by": created_by or None,
        "birthday_child_name": str(primary_child["name"]),
        "birthday_child_age": int(primary_child["age"] or 1),
        "birthday_children_json": birthday_children_json_value(birthday_children) if birthday_children else "[]",
        "birthday_children": birthday_children,
        "child_location": child_location,
        "adult_location": adult_location,
        "animation_enabled": 1 if animation_enabled else 0,
        "animation_type": animation_type or None,
        "animation_at": animations[0]["at_iso"] if animations else None,
        "animations": form_animations,
        "animations_json": animations_json_value(
            [{"type": item["type"], "at": item["at_iso"]} for item in animations]
        )
        if animations
        else "[]",
        "cake_enabled": cake_enabled,
        "cake_theme": cake_theme or None,
        "cake_weight": cake_weight or None,
        "cake_sponge": cake_sponge or None,
        "cake_filling": cake_filling or None,
        "cake_cream": cake_cream or None,
        "cake_image_data": cake_image_data or None,
        "cake_candle": cake_candle or None,
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
        "balloons_enabled": balloons_enabled,
        "balloons_description": balloons_description or None,
        "balloons_at": combine_day_time(reservation_day, balloons_time) if balloons_enabled else None,
        "attraction_at": None,
        "notes": notes,
        "cooperation_enabled": 1 if cooperation_enabled else 0,
        "status": status,
        "cancellation_reason": cancellation_reason,
        "inventory_lines": inventory_lines,
    }

    capacity = ROOM_CAPACITY.get(child_location)
    if capacity is not None and int(cleaned["children_count"] or 0) > capacity:
        errors["children_count"] = f"Ta sala mieści maksymalnie {capacity} dzieci."

    if (
        status == "active"
        and start_at
        and end_at
        and not invalid_adult_locations
        and (child_location != EMPTY_LOCATION or adult_locations)
    ):
        conflicts = find_conflicts(
            start_at,
            end_at,
            child_location if child_location != EMPTY_LOCATION else "",
            adult_locations,
            exclude_id=reservation_id,
        )
        if conflicts:
            conflict_lines = []
            for conflict in conflicts:
                conflict_lines.append(
                    f"{conflict['birthday_child_name']} ({conflict['child_location']}, {display_locations(conflict['adult_location'])})"
                )
            errors["child_location"] = "Wybrana sala lub stolik nakłada się z rezerwacją: " + "; ".join(conflict_lines)

    return cleaned, errors


def history_snapshot(row: DbRow | dict[str, object]) -> str:
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True)


def record_history(reservation_id: int, action: str, role: str, snapshot: DbRow | dict[str, object]) -> None:
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
        values["guest_total"],
        values["reservation_type"],
        values["parent_name"],
        values["parent_phone"],
        values.get("created_by") or None,
        values["birthday_child_name"],
        values["birthday_child_age"],
        values["birthday_children_json"],
        values["child_location"],
        values["adult_location"],
        values["animation_enabled"],
        values["animation_type"],
        values["animation_at"],
        values.get("animations_json") or "[]",
        values["cake_enabled"],
        values["cake_theme"],
        values["cake_weight"],
        values["cake_sponge"],
        values["cake_filling"],
        values["cake_cream"],
        values["cake_image_data"],
        values["cake_candle"],
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
        values["balloons_enabled"],
        values["balloons_description"],
        values["balloons_at"],
        values["attraction_at"],
        values["notes"],
        values.get("cooperation_enabled") or 0,
        values["status"],
        values["cancellation_reason"],
    )

    if reservation_id:
        previous = get_reservation(int(reservation_id))
        execute(
            """
            UPDATE reservations
            SET start_at = ?, end_at = ?, children_count = ?, adults_count = ?,
                guest_total = ?, reservation_type = ?,
                parent_name = ?, parent_phone = ?, created_by = ?, birthday_child_name = ?, birthday_child_age = ?,
                birthday_children_json = ?,
                child_location = ?, adult_location = ?, animation_enabled = ?, animation_type = ?,
                animation_at = ?, animations_json = ?,
                cake_enabled = ?, cake_theme = ?, cake_weight = ?, cake_sponge = ?,
                cake_filling = ?, cake_cream = ?, cake_image_data = ?, cake_candle = ?, cake_at = ?,
                fruit_enabled = ?, fruit_plates = ?, fruit_at = ?,
                drinks_enabled = ?, drinks_at = ?, culinary_workshops_enabled = ?,
                culinary_workshops_type = ?, culinary_workshops_at = ?,
                pinata_enabled = ?, pinata_theme = ?, pinata_at = ?,
                mascot_enabled = ?, mascot_type = ?, mascot_at = ?,
                balloons_enabled = ?, balloons_description = ?, balloons_at = ?,
                attraction_at = ?,
                notes = ?, cooperation_enabled = ?, status = ?, cancellation_reason = ?,
                updated_at = ?
            WHERE id = ?
            """,
            params + (timestamp, int(reservation_id)),
        )
        updated = get_reservation(int(reservation_id))
        action = "cancelled" if previous and previous["status"] != "cancelled" and values["status"] == "cancelled" else "updated"
        if updated:
            record_history(int(reservation_id), action, role, updated)
        party_day = None
        start_at = str(values.get("start_at") or "")
        if len(start_at) >= 10:
            try:
                party_day = date.fromisoformat(start_at[:10])
            except ValueError:
                party_day = None
        inventory.sync_reservation_inventory(
            int(reservation_id),
            list(values.get("inventory_lines") or []),
            role=role,
            status=str(values.get("status") or "active"),
            party_day=party_day,
            today=current_app_date(),
        )
        return int(reservation_id)

    new_id = execute(
        """
        INSERT INTO reservations (
            start_at, end_at, children_count, adults_count, guest_total, reservation_type, parent_name,
            parent_phone, created_by, birthday_child_name, birthday_child_age, birthday_children_json, child_location, adult_location,
            animation_enabled, animation_type, animation_at, animations_json, cake_enabled, cake_theme,
            cake_weight, cake_sponge, cake_filling, cake_cream, cake_image_data, cake_candle, cake_at,
            fruit_enabled, fruit_plates, fruit_at, drinks_enabled, drinks_at,
            culinary_workshops_enabled, culinary_workshops_type, culinary_workshops_at,
            pinata_enabled, pinata_theme, pinata_at,
            mascot_enabled, mascot_type, mascot_at,
            balloons_enabled, balloons_description, balloons_at,
            attraction_at,
            notes, cooperation_enabled, status, cancellation_reason,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params + (timestamp, timestamp),
    )
    row = get_reservation(new_id)
    if row:
        record_history(new_id, "created", role, row)
    party_day = None
    start_at = str(values.get("start_at") or "")
    if len(start_at) >= 10:
        try:
            party_day = date.fromisoformat(start_at[:10])
        except ValueError:
            party_day = None
    inventory.sync_reservation_inventory(
        new_id,
        list(values.get("inventory_lines") or []),
        role=role,
        status=str(values.get("status") or "active"),
        party_day=party_day,
        today=current_app_date(),
    )
    return new_id


def assign_waiter(
    reservation_id: int,
    waiter: str | None,
    role: str,
    *,
    remove_waiter: str | None = None,
) -> bool:
    row = get_reservation(reservation_id)
    if row is None:
        return False

    current = assigned_waiters_from_row(row)
    remove_value = remove_waiter.strip() if remove_waiter and remove_waiter.strip() else None
    waiter_value = waiter.strip() if waiter and waiter.strip() else None

    if remove_value:
        next_names = [name for name in current if name != remove_value]
        action = "waiter_removed"
    elif waiter_value:
        if waiter_value not in WAITERS:
            return False
        next_names = list(current)
        if waiter_value not in next_names:
            next_names.append(waiter_value)
        action = "waiter_assigned"
    else:
        next_names = []
        action = "waiter_removed"

    stored = format_assigned_waiters(next_names)
    execute(
        "UPDATE reservations SET assigned_waiter = ?, updated_at = ? WHERE id = ?",
        (stored, now_iso(), reservation_id),
    )
    updated = get_reservation(reservation_id)
    if updated is None:
        return False

    record_history(reservation_id, action, role, updated)
    return True


def parse_assigned_waiters(raw: object) -> list[str]:
    value = str(raw or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            names = [str(name).strip() for name in parsed if str(name).strip()]
            seen: set[str] = set()
            ordered: list[str] = []
            for name in names:
                if name in seen:
                    continue
                seen.add(name)
                ordered.append(name)
            return ordered
    return parse_assigned_animators(value)


def format_assigned_waiters(names: list[str]) -> str | None:
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        cleaned_name = str(name or "").strip()
        if not cleaned_name or cleaned_name in seen:
            continue
        seen.add(cleaned_name)
        cleaned.append(cleaned_name)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    return " | ".join(cleaned)


def assigned_waiters_from_row(row: DbRow | dict[str, object]) -> list[str]:
    if isinstance(row, dict):
        raw = row.get("assigned_waiter")
    else:
        raw = row["assigned_waiter"] if "assigned_waiter" in row.keys() else ""
    return parse_assigned_waiters(raw)


def parse_assigned_animators(raw: object) -> list[str]:
    value = str(raw or "").strip()
    if not value:
        return []
    if " | " in value:
        parts = [part.strip() for part in value.split(" | ") if part.strip()]
    else:
        parts = [value]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in parts:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def normalize_animator_slot(slot: str | None) -> str | None:
    value = str(slot or "").strip()
    if value in {"pinata", "mascot"}:
        return value
    if value.startswith("anim:") and value[5:].isdigit():
        return value
    return None


def parse_assigned_animators_map(raw: object) -> dict[str, list[str]]:
    value = str(raw or "").strip()
    if not value:
        return {}
    if value.startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            mapping: dict[str, list[str]] = {}
            for key, names_raw in parsed.items():
                slot = normalize_animator_slot(str(key))
                if not slot:
                    continue
                if isinstance(names_raw, list):
                    names = [str(name).strip() for name in names_raw if str(name).strip()]
                else:
                    names = parse_assigned_animators(names_raw)
                if names:
                    mapping[slot] = names
            return mapping
    legacy = parse_assigned_animators(value)
    return {"anim:0": legacy} if legacy else {}


def format_assigned_animators_map(mapping: dict[str, list[str]]) -> str | None:
    cleaned: dict[str, list[str]] = {}
    for key, names in mapping.items():
        slot = normalize_animator_slot(key)
        if not slot:
            continue
        unique: list[str] = []
        seen: set[str] = set()
        for name in names:
            cleaned_name = str(name or "").strip()
            if not cleaned_name or cleaned_name in seen:
                continue
            seen.add(cleaned_name)
            unique.append(cleaned_name)
        if unique:
            cleaned[slot] = unique
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def assigned_animators_map_from_row(row: DbRow | dict[str, object]) -> dict[str, list[str]]:
    if isinstance(row, dict):
        raw = row.get("assigned_animator")
    else:
        raw = row["assigned_animator"] if "assigned_animator" in row.keys() else ""
    return parse_assigned_animators_map(raw)


def assigned_animators_for_slot(row: DbRow | dict[str, object], slot: str) -> list[str]:
    normalized = normalize_animator_slot(slot) or "anim:0"
    return list(assigned_animators_map_from_row(row).get(normalized, []))


def assigned_animators_from_row(row: DbRow | dict[str, object]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for names in assigned_animators_map_from_row(row).values():
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
    return ordered


def assign_animator(
    reservation_id: int,
    animator: str | None,
    role: str,
    *,
    remove_animator: str | None = None,
    slot: str | None = "anim:0",
) -> bool:
    row = get_reservation(reservation_id)
    if row is None:
        return False

    normalized_slot = normalize_animator_slot(slot)
    if normalized_slot is None:
        return False

    mapping = assigned_animators_map_from_row(row)
    current = list(mapping.get(normalized_slot, []))
    remove_value = remove_animator.strip() if remove_animator and remove_animator.strip() else None
    animator_value = animator.strip() if animator and animator.strip() else None

    if remove_value:
        next_names = [name for name in current if name != remove_value]
        action = "animator_removed"
    elif animator_value:
        if animator_value not in ANIMATORS:
            return False
        next_names = list(current)
        if animator_value not in next_names:
            next_names.append(animator_value)
        action = "animator_assigned"
    else:
        next_names = []
        action = "animator_removed"

    if next_names:
        mapping[normalized_slot] = next_names
    else:
        mapping.pop(normalized_slot, None)

    stored = format_assigned_animators_map(mapping)
    execute(
        "UPDATE reservations SET assigned_animator = ?, updated_at = ? WHERE id = ?",
        (stored, now_iso(), reservation_id),
    )
    updated = get_reservation(reservation_id)
    if updated is None:
        return False

    record_history(reservation_id, action, role, updated)
    return True


def delete_reservation(reservation_id: int) -> bool:
    existing = get_reservation(reservation_id)
    if existing is None:
        return False
    inventory.release_reservation_inventory(reservation_id, role="organizer", keep_purchased=False)
    execute("DELETE FROM reservation_history WHERE reservation_id = ?", (reservation_id,))
    execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
    return True


def get_reservation(reservation_id: int) -> DbRow | None:
    return db_one("SELECT * FROM reservations WHERE id = ?", (reservation_id,))


def get_reservations_for_day(target_day: date) -> list[DbRow]:
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


def get_reservations_for_days(iso_dates: list[str]) -> dict[str, list[DbRow]]:
    """Jedno zapytanie DB dla całego zakresu dat zamiast N osobnych."""
    result: dict[str, list[DbRow]] = {iso: [] for iso in iso_dates}
    valid_days: list[date] = []
    for iso in iso_dates:
        try:
            valid_days.append(date.fromisoformat(iso))
        except ValueError:
            continue
    if not valid_days:
        return result

    first, last = min(valid_days), max(valid_days)
    range_start, _ = day_bounds(first)
    _, range_end = day_bounds(last)
    cache_key = f"{range_start}|{range_end}"
    now = datetime.now().timestamp()
    cached_key = str(_RESERVATIONS_RANGE_CACHE.get("key") or "")
    cached_result = _RESERVATIONS_RANGE_CACHE.get("result")
    if (
        cached_key == cache_key
        and isinstance(cached_result, dict)
        and now - float(_RESERVATIONS_RANGE_CACHE.get("loaded_at", 0.0)) < RESERVATIONS_CACHE_SECONDS
    ):
        return {iso: list(cached_result.get(iso, [])) for iso in iso_dates}

    rows = db_rows(
        """
        SELECT *
        FROM reservations
        WHERE start_at < ? AND end_at > ?
        ORDER BY start_at ASC, child_location ASC
        """,
        (range_end, range_start),
    )
    bounds_by_iso = {day.isoformat(): day_bounds(day) for day in valid_days}
    for row in rows:
        start_at = str(row.get("start_at") or "")
        end_at = str(row.get("end_at") or "")
        for iso, (day_start, day_end) in bounds_by_iso.items():
            if start_at < day_end and end_at > day_start:
                result[iso].append(row)
    _RESERVATIONS_RANGE_CACHE["key"] = cache_key
    _RESERVATIONS_RANGE_CACHE["loaded_at"] = now
    _RESERVATIONS_RANGE_CACHE["result"] = {iso: list(day_rows) for iso, day_rows in result.items()}
    return result


def get_reservations_created_between(range_start: str, range_end: str) -> list[DbRow]:
    """Rezerwacje wpisane do systemu w oknie czasu (created_at)."""
    return db_rows(
        """
        SELECT *
        FROM reservations
        WHERE created_at >= ? AND created_at < ?
        ORDER BY created_at ASC, id ASC
        """,
        (range_start, range_end),
    )


def get_reservations_created_on_day(target_day: date) -> list[DbRow]:
    start, end = day_bounds(target_day)
    return get_reservations_created_between(start, end)


def get_reservations_for_month(year: int, month: int) -> list[DbRow]:
    """Rezerwacje wpisane w danym miesiącu kalendarzowym (po created_at)."""
    first = date(year, month, 1)
    nxt = next_calendar_month(first)
    return get_reservations_created_between(
        datetime.combine(first, time.min).isoformat(timespec="minutes"),
        datetime.combine(nxt, time.min).isoformat(timespec="minutes"),
    )


def reservation_party_date(row: DbRow | dict[str, object]) -> date | None:
    raw = str(row.get("start_at") or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def get_all_reservations() -> list[DbRow]:
    return db_rows(
        """
        SELECT *
        FROM reservations
        ORDER BY start_at ASC, id ASC
        """
    )


def get_history(reservation_id: int) -> list[DbRow]:
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
) -> dict[str, dict[str, object]]:
    statuses: dict[str, dict[str, object]] = {
        location: {
            "status": "free",
            "label": "Wolne",
            "color": "",
            "reservation_id": "",
            "tip": "",
        }
        for location in ALL_LOCATIONS
    }
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
        SELECT id, start_at, end_at, parent_name, birthday_child_name, birthday_child_age,
               birthday_children_json, assigned_waiter, assigned_animator, child_location, adult_location, status
        FROM reservations
        WHERE status = 'active'
          AND start_at < ?
          AND end_at > ?
          {exclude_sql}
        ORDER BY start_at ASC, id ASC
        """,
        tuple(params),
    )
    colors = reservation_color_map(rows)
    for row in rows:
        color = colors.get(int(row["id"]), "")
        tip = reservation_plan_tip(row)
        label = f"Zajęte: {tip}"
        for location in reservation_locations(row):
            if location in statuses:
                statuses[location] = {
                    "status": "occupied",
                    "label": label,
                    "color": color,
                    "reservation_id": str(row["id"]),
                    "tip": tip,
                }
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


SCHEDULE_DEPARTMENTS = [
    ("animatorzy", "Animatorzy i organizator urodzin"),
    ("managerowie", "Managerowie"),
    ("kuchnia", "Kuchnia"),
    ("serwis", "Serwis"),
]
SCHEDULE_DEPARTMENT_MATCHERS = {
    "animatorzy": ("animator", "organizator"),
    "managerowie": ("manager", "menedzer", "menedzer", "kierownik"),
    "kuchnia": ("kuchnia", "kucharz", "cukiernik", "pomoc kuchenna"),
    "serwis": (
        "barman",
        "kelner",
        "recepcja",
        "sprzataj",
        "sprzatajac",
        "pracownia tworcza",
        "serwis",
    ),
}
_SCHEDULE_CACHE: dict[str, object] = {"loaded_at": 0.0, "result": None}
_SCHEDULE_LOCK = threading.Lock()
_GOOGLE_TOKEN_CACHE: dict[str, object] = {"token": "", "expires_at": 0.0}
_RESERVATIONS_RANGE_CACHE: dict[str, object] = {"loaded_at": 0.0, "key": "", "result": None}
_SHIFT_REPORT_CACHE: dict[str, object] = {"schedule_loaded_at": 0.0, "reports": {}}


def normalize_search_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_text = ascii_text.replace("ł", "l").replace("Ł", "L")
    return ascii_text.lower().strip()


def schedule_clean_person_name(value: object) -> str:
    """Jedna spacja, bez NBSP — ten sam format co w arkuszu po ludzkim copy-paste."""
    raw = str(value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", raw).strip()


def schedule_person_key(value: object) -> str:
    return normalize_search_text(schedule_clean_person_name(value))


def schedule_grafik_short_name(value: object) -> str:
    """Imię + inicjał nazwiska w tabeli grafiku, np. Nikodem B."""
    name = schedule_clean_person_name(value)
    if not name:
        return ""
    parts = name.split()
    if len(parts) < 2:
        return name
    surname = parts[-1]
    initial = surname[0].upper()
    if not initial.isalpha():
        return name
    return f"{' '.join(parts[:-1])} {initial}."


def schedule_month_from_title(sheet_title: str) -> tuple[int, int] | None:
    match = re.search(r"(?<!\d)(0?[1-9]|1[0-2])\.(20\d{2})(?!\d)", sheet_title)
    if not match:
        return None
    return int(match.group(2)), int(match.group(1))


def schedule_date_from_cell(value: object, sheet_title: str) -> date | None:
    match = re.search(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?!\d)", str(value or ""))
    title_month = schedule_month_from_title(sheet_title)
    if not match or title_month is None:
        return None
    year = title_month[0]
    day_value = int(match.group(1))
    month_value = int(match.group(2))
    try:
        return date(year, month_value, day_value)
    except ValueError:
        return None


def schedule_month_key(value: date | None) -> str:
    return value.strftime("%Y-%m") if value else ""


def schedule_dates_form_contiguous_week(week_dates: list[date]) -> bool:
    """Daty tygodnia muszą tworzyć spójny zakres (max 7 dni bez dziur w kalendarzu)."""
    if len(week_dates) <= 1:
        return True
    ordered = sorted(set(week_dates))
    span_days = (ordered[-1] - ordered[0]).days
    if span_days > 6:
        return False
    return len(ordered) == span_days + 1


def dominant_schedule_month(week_dates: list[date]) -> str:
    """Miesiąc bloku z pól dat (np. 20.07…26.07 → 2026-07), nie z nazwy zakładki."""
    if not week_dates:
        return ""
    counts: dict[str, int] = {}
    for day in week_dates:
        key = schedule_month_key(day)
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=lambda key: (counts[key], key))


def schedule_month_label(month_key: str) -> str:
    try:
        year_value, month_value = (int(part) for part in month_key.split("-", 1))
    except ValueError:
        return month_key
    return f"{MONTH_STANDALONE_LABELS[month_value - 1]} {year_value}"


def schedule_week_label(week_key: str) -> str:
    try:
        start_day = date.fromisoformat(week_key)
    except ValueError:
        return week_key
    end_day = start_day + timedelta(days=6)
    return f"{start_day.strftime('%d.%m')} - {end_day.strftime('%d.%m')}"


def schedule_department_for(sheet_title: str, position: str) -> str:
    haystack = f"{normalize_search_text(sheet_title)} {normalize_search_text(position)}"
    for key, matchers in SCHEDULE_DEPARTMENT_MATCHERS.items():
        if any(matcher in haystack for matcher in matchers):
            return key
    return "serwis"


def load_google_service_account_info() -> dict[str, object]:
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        raw = GOOGLE_SERVICE_ACCOUNT_JSON
        if raw.startswith("{"):
            return json.loads(raw)
        path = Path(raw)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    if GOOGLE_SERVICE_ACCOUNT_JSON_PATH:
        return json.loads(Path(GOOGLE_SERVICE_ACCOUNT_JSON_PATH).read_text(encoding="utf-8"))
    raise RuntimeError("Brak GOOGLE_SERVICE_ACCOUNT_JSON albo GOOGLE_SERVICE_ACCOUNT_JSON_PATH w .env.")


def google_sheets_access_token() -> str:
    now = datetime.now().timestamp()
    cached_token = str(_GOOGLE_TOKEN_CACHE.get("token") or "")
    expires_at = float(_GOOGLE_TOKEN_CACHE.get("expires_at") or 0.0)
    # Odśwież ~60 s przed wygaśnięciem, żeby uniknąć race przy równoległych requestach.
    if cached_token and now < expires_at - 60:
        return cached_token

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise RuntimeError("Brakuje pakietu google-auth. Uruchom: pip install -r requirements.txt") from exc

    credentials = Credentials.from_service_account_info(
        load_google_service_account_info(),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    credentials.refresh(Request())
    token = str(credentials.token)
    # google-auth trzyma expiry jako naive datetime w UTC — nie wolno brać .timestamp() lokalnie.
    if credentials.expiry is not None:
        expiry = credentials.expiry
        if expiry.tzinfo is None:
            expires_at = expiry.replace(tzinfo=timezone.utc).timestamp()
        else:
            expires_at = expiry.timestamp()
    else:
        expires_at = now + 3500
    _GOOGLE_TOKEN_CACHE["token"] = token
    _GOOGLE_TOKEN_CACHE["expires_at"] = expires_at
    return token


def google_sheets_get(path: str, token: str | None = None) -> dict[str, object]:
    token = token or google_sheets_access_token()
    request = urllib.request.Request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def spreadsheet_sheet_titles(token: str | None = None) -> list[str]:
    payload = google_sheets_get("?fields=sheets.properties.title", token=token)
    sheets = payload.get("sheets", [])
    titles = []
    for item in sheets if isinstance(sheets, list) else []:
        properties = item.get("properties", {}) if isinstance(item, dict) else {}
        title = str(properties.get("title", "")).strip()
        if title:
            titles.append(title)
    return titles


def _normalize_sheet_values(values: object) -> list[list[str]]:
    if not isinstance(values, list):
        return []
    return [[str(cell) for cell in row] for row in values if isinstance(row, list)]


def google_sheet_values(sheet_title: str, token: str | None = None) -> list[list[str]]:
    encoded_range = quote(f"'{sheet_title}'!{GOOGLE_SHEETS_RANGE}", safe="")
    payload = google_sheets_get(f"/values/{encoded_range}?majorDimension=ROWS", token=token)
    return _normalize_sheet_values(payload.get("values", []))


def google_sheet_values_batch(
    sheet_titles: list[str],
    token: str | None = None,
) -> dict[str, list[list[str]]]:
    """Pobierz wartości wielu zakładek jednym requestem (values.batchGet)."""
    if not sheet_titles:
        return {}
    query = ["majorDimension=ROWS"]
    for title in sheet_titles:
        encoded_range = quote(f"'{title}'!{GOOGLE_SHEETS_RANGE}", safe="")
        query.append(f"ranges={encoded_range}")
    payload = google_sheets_get(f"/values:batchGet?{'&'.join(query)}", token=token)
    value_ranges = payload.get("valueRanges", [])
    if not isinstance(value_ranges, list):
        value_ranges = []
    by_title: dict[str, list[list[str]]] = {}
    for title, item in zip(sheet_titles, value_ranges):
        values = item.get("values", []) if isinstance(item, dict) else []
        by_title[title] = _normalize_sheet_values(values)
    for title in sheet_titles:
        by_title.setdefault(title, [])
    return by_title


def row_cell(row: list[str], index: int) -> str:
    return row[index].strip() if 0 <= index < len(row) else ""


def find_schedule_header(rows: list[list[str]]) -> tuple[int, int, int, int] | None:
    for index, row in enumerate(rows[:25]):
        normalized = [normalize_search_text(cell) for cell in row]
        name_index = next((i for i, cell in enumerate(normalized) if "imie" in cell and "nazwisko" in cell), -1)
        position_index = next((i for i, cell in enumerate(normalized) if "stanowisko" in cell), -1)
        total_index = next((i for i, cell in enumerate(normalized) if "ilosc godzin" in cell), -1)
        if name_index >= 0 and position_index >= 0 and total_index >= 0:
            return index, name_index, position_index, total_index
    return None


def find_schedule_headers(rows: list[list[str]]) -> list[tuple[int, int, int, int]]:
    headers = []
    for index, row in enumerate(rows):
        normalized = [normalize_search_text(cell) for cell in row]
        name_index = next((i for i, cell in enumerate(normalized) if "imie" in cell and "nazwisko" in cell), -1)
        position_index = next((i for i, cell in enumerate(normalized) if "stanowisko" in cell), -1)
        total_index = next((i for i, cell in enumerate(normalized) if "ilosc godzin" in cell), -1)
        if name_index >= 0 and position_index >= 0 and total_index >= 0:
            headers.append((index, name_index, position_index, total_index))
    return headers


def parse_schedule_block(
    sheet_title: str,
    rows: list[list[str]],
    header_index: int,
    next_header_index: int,
    name_index: int,
    position_index: int,
    total_index: int,
) -> list[dict[str, object]]:
    header_row = rows[header_index]
    date_row = rows[header_index - 1] if header_index > 0 else []
    day_columns: list[dict[str, object]] = []
    day_names = {"poniedzialek", "wtorek", "sroda", "czwartek", "piatek", "sobota", "niedziela"}
    for column_index, value in enumerate(header_row):
        day_name = normalize_search_text(value)
        if day_name in day_names:
            day_date = schedule_date_from_cell(row_cell(date_row, column_index), sheet_title)
            day_columns.append(
                {
                    "name": value.strip(),
                    "date": day_date.isoformat() if day_date else "",
                    "date_label": row_cell(date_row, column_index),
                    "month": schedule_month_key(day_date),
                    "shift_index": column_index,
                    "hours_index": column_index + 1,
                }
            )

    week_dates = [date.fromisoformat(str(day["date"])) for day in day_columns if day.get("date")]
    if week_dates and not schedule_dates_form_contiguous_week(week_dates):
        return []
    if not week_dates:
        return []
    week_start = min(week_dates).isoformat()
    week_months = sorted({schedule_month_key(day) for day in week_dates})
    primary_month = schedule_month_key(week_dates[-1])
    # Miesiąc bloku bierzemy z dat nad tabelką (20.07 → lipiec), nie z nazwy zakładki 08.2026.
    block_month = dominant_schedule_month(week_dates) or primary_month

    best_in_block: dict[str, tuple[int, dict[str, object]]] = {}
    for row in rows[header_index + 1 : next_header_index]:
        name = schedule_clean_person_name(row_cell(row, name_index))
        position = row_cell(row, position_index)
        if not name and not position:
            continue
        normalized_name = schedule_person_key(name)
        normalized_position = normalize_search_text(position)
        if normalized_name in {"razem", "suma", "lp.", "lp"} or "imie" in normalized_name:
            continue
        if not name and not normalized_position:
            continue
        shifts = []
        has_shift = False
        for day in day_columns:
            shift = row_cell(row, int(day["shift_index"]))
            hours = row_cell(row, int(day["hours_index"]))
            if shift or hours:
                has_shift = True
            shifts.append(
                {
                    "day": day["name"],
                    "date": day["date"],
                    "date_label": day["date_label"],
                    "month": day["month"],
                    "shift": shift,
                    "hours": hours,
                }
            )
        if not name and not has_shift:
            continue
        entry: dict[str, object] = {
            "sheet": sheet_title,
            "sheet_month": block_month,
            "department": schedule_department_for(sheet_title, position),
            "week_start": week_start,
            "week_months": week_months,
            "primary_month": primary_month,
            "name": name or "Bez nazwiska",
            "position": position,
            "total_hours": row_cell(row, total_index),
            "shifts": shifts,
        }
        person_key = schedule_person_key(name)
        score = schedule_entry_row_score(entry)
        previous = best_in_block.get(person_key)
        if previous:
            previous_score, previous_entry = previous
            if score < previous_score:
                continue
            if score == previous_score and not str(entry.get("position") or "").strip():
                if str(previous_entry.get("position") or "").strip():
                    continue
        best_in_block[person_key] = (score, entry)
    return [item[1] for item in best_in_block.values()]


def parse_schedule_sheet(sheet_title: str, rows: list[list[str]]) -> list[dict[str, object]]:
    headers = find_schedule_headers(rows)
    entries: list[dict[str, object]] = []
    best_by_person_week: dict[tuple[str, str, str], tuple[int, dict[str, object]]] = {}
    for index, (header_index, name_index, position_index, total_index) in enumerate(headers):
        next_header_index = headers[index + 1][0] if index + 1 < len(headers) else len(rows)
        block_entries = parse_schedule_block(
            sheet_title,
            rows,
            header_index,
            next_header_index,
            name_index,
            position_index,
            total_index,
        )
        for entry in block_entries:
            shifts = entry.get("shifts", [])
            shift_score = 0
            if isinstance(shifts, list):
                for shift in shifts:
                    if not isinstance(shift, dict):
                        continue
                    if str(shift.get("shift") or "").strip() not in {"", "-", "wolne"}:
                        shift_score += 2
                    if str(shift.get("hours") or "").strip():
                        shift_score += 1
            total_hours = str(entry.get("total_hours") or "").strip()
            score = shift_score + (2 if total_hours else 0)
            person_week_key = (
                str(entry.get("week_start") or ""),
                schedule_person_key(str(entry.get("name") or "")),
            )
            previous = best_by_person_week.get(person_week_key)
            if not previous or score > previous[0]:
                best_by_person_week[person_week_key] = (score, entry)
    return [item[1] for item in best_by_person_week.values()]


def schedule_entry_score(entry: dict[str, object]) -> int:
    shifts = entry.get("shifts", [])
    shift_score = 0
    if isinstance(shifts, list):
        for shift in shifts:
            if not isinstance(shift, dict):
                continue
            if str(shift.get("shift") or "").strip() not in {"", "-", "wolne"}:
                shift_score += 2
            if str(shift.get("hours") or "").strip():
                shift_score += 1
    total_hours = str(entry.get("total_hours") or "").strip()
    score = shift_score + (2 if total_hours else 0)
    if str(entry.get("position") or "").strip():
        score += 1
    # Przy skopiowanej tabelce lipiec→sierpień preferuj zakładkę zgodną z datami bloku.
    title_month = schedule_month_from_title(str(entry.get("sheet") or ""))
    block_month = str(entry.get("sheet_month") or entry.get("primary_month") or "")
    if title_month is not None and block_month == f"{title_month[0]:04d}-{title_month[1]:02d}":
        score += 8
    return score


def dedupe_schedule_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    best_by_person_week: dict[tuple[str, str], tuple[int, dict[str, object]]] = {}
    for entry in entries:
        person_week_key = (
            str(entry.get("week_start") or ""),
            schedule_person_key(str(entry.get("name") or "")),
        )
        score = schedule_entry_score(entry)
        previous = best_by_person_week.get(person_week_key)
        if not previous or score > previous[0]:
            best_by_person_week[person_week_key] = (score, entry)
    return [item[1] for item in best_by_person_week.values()]


def load_live_schedule(force: bool = False) -> dict[str, object]:
    if not GOOGLE_SHEET_ID:
        return {"ok": False, "error": "Brak GOOGLE_SHEET_ID w .env.", "entries": [], "loaded_at": None}

    now = datetime.now().timestamp()
    if not force:
        cached_result = _SCHEDULE_CACHE.get("result")
        if cached_result and now - float(_SCHEDULE_CACHE.get("loaded_at", 0.0)) < GOOGLE_SHEETS_CACHE_SECONDS:
            return cached_result  # type: ignore[return-value]

    # One Sheets fetch at a time — concurrent /grafiki during cold start used to stampede.
    with _SCHEDULE_LOCK:
        now = datetime.now().timestamp()
        if not force:
            cached_result = _SCHEDULE_CACHE.get("result")
            if cached_result and now - float(_SCHEDULE_CACHE.get("loaded_at", 0.0)) < GOOGLE_SHEETS_CACHE_SECONDS:
                return cached_result  # type: ignore[return-value]

        try:
            token = google_sheets_access_token()
            titles = spreadsheet_sheet_titles(token=token)
            sheets_by_title = google_sheet_values_batch(titles, token=token)
            entries: list[dict[str, object]] = []
            for title in titles:
                entries.extend(parse_schedule_sheet(title, sheets_by_title.get(title, [])))
            entries = dedupe_schedule_entries(entries)
            result = {
                "ok": True,
                "error": "",
                "entries": entries,
                "sheet_titles": titles,
                "loaded_at": datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
            }
        except Exception as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "entries": [],
                "sheet_titles": [],
                "loaded_at": datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
            }
        _SCHEDULE_CACHE["loaded_at"] = now
        _SCHEDULE_CACHE["result"] = result
        return result


def field_time(values: dict[str, object], field: str) -> str:
    return format_time(values.get(field))


def is_enabled(row: DbRow | dict[str, object], field: str) -> bool:
    try:
        value = row.get(field) if isinstance(row, dict) else row[field]
    except (KeyError, IndexError, TypeError):
        return False
    try:
        return int(value or 0) == 1
    except (TypeError, ValueError):
        return False


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


def date_title(target_day: date) -> str:
    return (
        f"{WEEKDAY_FULL_LABELS[target_day.weekday()]}, "
        f"{target_day.day:02d} {MONTH_FULL_LABELS[target_day.month - 1]}"
    )


_BOOT_READY = threading.Event()
_BOOT_ERROR: str | None = None


def boot_wait_page(message: str = "Uruchamianie iKids Park…") -> bytes:
    safe = escape(message)
    icon_src = f"/app-icon-512.png?v={pwa_icon_version()}"
    return f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#ffffff">
  <title>iKids Park</title>
  <style>
    html, body {{
      margin: 0;
      min-height: 100%;
      height: 100%;
      background: #ffffff;
    }}
    body {{
      display: grid;
      place-items: center;
      font-family: system-ui, sans-serif;
      color: #8a8a8a;
    }}
    .boot {{
      display: grid;
      justify-items: center;
      gap: 18px;
      padding: 24px;
    }}
    .boot img {{
      width: min(58vw, 220px);
      height: auto;
      display: block;
      border: 0;
      /* brak karty / cienia — białe tło logo zlewa się z ekranem */
      background: transparent;
      border-radius: 0;
      box-shadow: none;
    }}
    .boot p {{
      margin: 0;
      font-size: 0.95rem;
      letter-spacing: .02em;
      color: #9a9a9a;
    }}
  </style>
</head>
<body>
  <div class="boot">
    <img src="{icon_src}" alt="iKids Park" width="512" height="512">
    <p>{safe}</p>
  </div>
  <script>
    (() => {{
      let tries = 0;
      const poll = async () => {{
        tries += 1;
        try {{
          const response = await fetch("/api/ready", {{ cache: "no-store" }});
          const data = await response.json();
          if (data && data.ready) {{
            window.location.reload();
            return;
          }}
        }} catch (_) {{}}
        window.setTimeout(poll, tries < 20 ? 700 : 1500);
      }};
      window.setTimeout(poll, 400);
    }})();
  </script>
</body>
</html>""".encode("utf-8")


def pwa_icon_version() -> str:
    """Stable until the logo file changes — avoids endless WebAPK reinstalls from cache bumps."""
    for path in (PWA_LOGO_PATH, LOGO_PATH):
        if path.exists():
            return f"{int(path.stat().st_mtime)}-{path.stat().st_size}-splash2"
    return f"{PWA_CACHE_NAME}-splash2"


def app_icon_png(size: int = 512, *, solid: bool = False) -> bytes:
    """PWA icon from pwalogo.png."""
    return build_app_icon_png(PWA_LOGO_PATH, LOGO_PATH, size, solid=solid)
def manifest_response() -> bytes:
    icon_v = pwa_icon_version()
    icons: list[dict[str, str]] = []
    for size in PWA_ICON_SIZES:
        # Transparent „any” — Android splash blends into white background_color (no square tile).
        icons.append(
            {
                "src": f"/app-icon-{size}.png?v={icon_v}",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any",
            }
        )
        # Solid white maskable — home-screen adaptive icon still looks correct.
        icons.append(
            {
                "src": f"/app-icon-{size}-solid.png?v={icon_v}",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "maskable",
            }
        )
    manifest = {
        "name": APP_TITLE,
        "short_name": APP_SHORT_TITLE,
        "description": "Panel rezerwacji urodzin i atrakcji iKids Park.",
        "id": PWA_MANIFEST_ID,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
        "icons": icons,
        "categories": ["business", "productivity"],
        "lang": "pl",
        "dir": "ltr",
        "prefer_related_applications": False,
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


def service_worker_response() -> bytes:
    icon_v = pwa_icon_version()
    icon_assets = ",\n  ".join(
        json.dumps(url)
        for size in PWA_ICON_SIZES
        for url in (
            f"/app-icon-{size}.png?v={icon_v}",
            f"/app-icon-{size}-solid.png?v={icon_v}",
        )
    )
    payload = """const IKIDS_CACHE = "__CACHE_NAME__";
const STATIC_ASSETS = [
  "/offline",
  "/favicon.ico",
  __ICON_ASSETS__
];

function fetchWithTimeout(url, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { signal: ctrl.signal, cache: "reload" }).finally(() => clearTimeout(timer));
}

function fetchFresh(request) {
  return fetch(request, { cache: "no-store" });
}

function shouldFetchFresh(request) {
  const accept = request.headers.get("accept") || "";
  if (request.headers.has("X-IKids-Navigation")) return true;
  if (accept.includes("text/html")) return true;
  try {
    const url = new URL(request.url);
    return url.pathname === "/" || url.pathname === "/schema" || url.pathname === "/history" || url.pathname.startsWith("/api/");
  } catch (_) {
    return false;
  }
}

self.addEventListener("install", (event) => {
  // Never use cache.addAll — one hung request freezes Chrome on "Instaluje aplikację…".
  event.waitUntil((async () => {
    try {
      const cache = await caches.open(IKIDS_CACHE);
      await Promise.all(STATIC_ASSETS.map(async (url) => {
        try {
          const response = await fetchWithTimeout(url, 8000);
          if (response && response.ok) await cache.put(url, response);
        } catch (_) {}
      }));
    } catch (_) {}
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key !== IKIDS_CACHE).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  if (event.request.mode === "navigate" || shouldFetchFresh(event.request)) {
    event.respondWith(
      fetchFresh(event.request).catch(() => caches.match("/offline"))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
      if (!response || response.status !== 200 || response.type !== "basic") return response;
      const copy = response.clone();
      caches.open(IKIDS_CACHE).then((cache) => cache.put(event.request, copy)).catch(() => undefined);
      return response;
    }).catch(() => cached))
  );
});
"""
    payload = payload.replace("__CACHE_NAME__", PWA_CACHE_NAME).replace("__ICON_ASSETS__", icon_assets)
    return payload.encode("utf-8")


def offline_response() -> bytes:
    return f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes, viewport-fit=cover">
  <meta name="theme-color" content="#ffffff" media="(max-width: 640px)">
  <meta name="theme-color" content="#139bd7" media="(min-width: 641px)">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="{APP_SHORT_TITLE}">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/app-icon-192-solid.png?v={pwa_icon_version()}" type="image/png">
  <link rel="apple-touch-icon" href="/app-icon-192-solid.png?v={pwa_icon_version()}">
  <title>{APP_SHORT_TITLE} offline</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #000;
      background: #b4b4b4;
    }}
    main {{
      max-width: 460px;
      border: 1px solid #dddddd;
      background: #ffffff;
      padding: 24px;
      border-radius: 8px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 1.35rem;
    }}
    p {{
      margin: 0;
      color: #555555;
      line-height: 1.5;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Brak połączenia</h1>
    <p>System iKids Park jest zainstalowany jako PWA. Wróć do aplikacji po odzyskaniu połączenia z serwerem.</p>
  </main>
</body>
</html>""".encode("utf-8")


def pwa_install_script() -> str:
    return """
  <script>
    (() => {
      const installButton = document.querySelector("[data-install-app]");
      const popup = document.querySelector("[data-install-popup]");
      const step = document.querySelector("[data-install-step]");
      const closeBtn = document.querySelector("[data-install-popup-close]");
      if (!installButton || !popup || !step) return;

      let deferredPrompt = null;
      const standaloneQuery = window.matchMedia("(display-mode: standalone)");
      const ua = window.navigator.userAgent || "";
      const isAppleMobile = /iphone|ipad|ipod/i.test(ua)
        || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
      const isAndroid = /android/i.test(ua);

      function isStandalone() {
        return standaloneQuery.matches || window.navigator.standalone === true;
      }

      function updateButton() {
        installButton.hidden = isStandalone();
      }

      function openPopup(text) {
        step.textContent = text;
        popup.hidden = false;
        document.documentElement.classList.add("install-popup-open");
      }

      function closePopup() {
        popup.hidden = true;
        document.documentElement.classList.remove("install-popup-open");
      }

      function helpText() {
        if (isAppleMobile) return "Udostępnij → Dodaj do ekranu początkowego";
        if (isAndroid) return "Menu ⋮ → Zainstaluj aplikację";
        return "W pasku adresu: Zainstaluj aplikację";
      }

      window.addEventListener("beforeinstallprompt", (event) => {
        event.preventDefault();
        deferredPrompt = event;
        updateButton();
      });

      window.addEventListener("appinstalled", () => {
        deferredPrompt = null;
        closePopup();
        updateButton();
      });

      installButton.addEventListener("click", async () => {
        if (isStandalone()) return;
        if (deferredPrompt) {
          try {
            deferredPrompt.prompt();
            await deferredPrompt.userChoice;
          } catch (_) {}
          deferredPrompt = null;
          updateButton();
          return;
        }
        openPopup(helpText());
      });

      closeBtn?.addEventListener("click", closePopup);
      popup.addEventListener("click", (event) => {
        if (event.target === popup) closePopup();
      });

      if ("serviceWorker" in navigator && window.isSecureContext) {
        navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).catch(() => undefined);
      }

      standaloneQuery.addEventListener?.("change", updateButton);
      updateButton();
    })();
  </script>
"""


def date_navigation_script() -> str:
    return """
  <script>
    (() => {
      function initDateNavigation() {
      const strip = document.querySelector("[data-date-strip]");
      if (!strip) return;
      if (strip.dataset.dateNavReady === "true") return;
      strip.dataset.dateNavReady = "true";

      const weeks = () => [...strip.querySelectorAll(".date-week")];
      const finePointer = window.matchMedia?.("(pointer: fine)")?.matches === true;
      const toolbar = strip.closest(".date-toolbar");

      function buildWeekButton(direction) {
        const button = document.createElement("button");
        button.className = `date-week-jump date-week-jump-${direction}`;
        button.type = "button";
        button.setAttribute(`data-date-week-${direction}`, "");
        button.setAttribute("aria-label", direction === "prev" ? "Poprzedni tydzień" : "Następny tydzień");
        button.innerHTML = direction === "prev"
          ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>'
          : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
        return button;
      }

      function ensureWeekButtons() {
        if (!toolbar) return { prev: null, next: null };
        let prev = toolbar.querySelector("[data-date-week-prev]");
        let next = toolbar.querySelector("[data-date-week-next]");
        if (!prev) {
          prev = buildWeekButton("prev");
          toolbar.insertBefore(prev, strip);
        }
        if (!next) {
          next = buildWeekButton("next");
          toolbar.appendChild(next);
        }
        prev.hidden = false;
        next.hidden = false;
        prev.disabled = false;
        next.disabled = false;
        return { prev, next };
      }

      const weekButtons = ensureWeekButtons();
      const prevWeekButton = weekButtons.prev;
      const nextWeekButton = weekButtons.next;

      function scrollToWeek(week, behavior = "auto") {
        if (!week) return;
        strip.scrollTo({ left: week.offsetLeft, behavior });
      }

      function snapToNearestWeek() {
        const pages = weeks();
        if (!pages.length) return;
        const targetLeft = strip.scrollLeft;
        const nearest = pages.reduce((best, week) => {
          const distance = Math.abs(week.offsetLeft - targetLeft);
          return distance < best.distance ? { week, distance } : best;
        }, { week: pages[0], distance: Number.POSITIVE_INFINITY }).week;
        scrollToWeek(nearest);
      }

      function nearestWeek() {
        const pages = weeks();
        if (!pages.length) return null;
        const targetLeft = strip.scrollLeft;
        return pages.reduce((best, week) => {
          const distance = Math.abs(week.offsetLeft - targetLeft);
          return distance < best.distance ? { week, distance } : best;
        }, { week: pages[0], distance: Number.POSITIVE_INFINITY }).week;
      }

      function updateWeekButtons() {
        if (prevWeekButton) prevWeekButton.disabled = false;
        if (nextWeekButton) nextWeekButton.disabled = false;
      }

      function shiftWeek(direction) {
        const pages = weeks();
        const current = nearestWeek();
        const currentIndex = current ? pages.indexOf(current) : -1;
        if (currentIndex < 0) return;
        const nextIndex = Math.max(0, Math.min(pages.length - 1, currentIndex + direction));
        const next = pages[nextIndex];
        if (!next || next === current) return;
        showMonthLabel();
        scrollToWeek(next, "smooth");
        window.setTimeout(() => {
          updateMonthLabel();
          updateWeekButtons();
          hideMonthLabel();
        }, 260);
      }

      let initialScroll = true;
      let stripUserActive = false;
      let scrollIdleTimer = 0;

      function applyInitialWeek() {
        const activeWeek = document.querySelector("[data-active-week]")
          || document.querySelector("[data-default-week]");
        scrollToWeek(activeWeek);
        window.setTimeout(() => {
          initialScroll = false;
          hideMonthLabel();
          updateWeekButtons();
        }, 150);
      }

      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(applyInitialWeek);
      });

      if ("onscrollend" in strip) {
        strip.addEventListener("scrollend", () => {
          if (initialScroll) return;
          snapToNearestWeek();
          if (stripUserActive) {
            endStripInteraction();
          }
        });
      }

      let dragActive = false;
      let dragMoved = false;
      let suppressClick = false;
      let startX = 0;
      let startScrollLeft = 0;

      strip.addEventListener("dragstart", (event) => {
        event.preventDefault();
      });

      strip.addEventListener("mousedown", (event) => {
        if (finePointer) return;
        if (event.button !== 0) return;
        dragActive = true;
        dragMoved = false;
        suppressClick = false;
        startX = event.pageX;
        startScrollLeft = strip.scrollLeft;
      });

      window.addEventListener("mousemove", (event) => {
        if (finePointer) return;
        if (!dragActive) return;
        const delta = event.pageX - startX;
        if (!dragMoved && Math.abs(delta) > 5) {
          dragMoved = true;
          suppressClick = true;
          strip.classList.add("is-dragging");
          beginStripInteraction();
        }
        if (!dragMoved) return;
        event.preventDefault();
        strip.scrollLeft = startScrollLeft - delta;
        updateMonthLabel();
      });

      const endDrag = () => {
        if (!dragActive) return;
        dragActive = false;
        strip.classList.remove("is-dragging");
        if (dragMoved) {
          snapToNearestWeek();
          updateMonthLabel();
          endStripInteraction();
        }
        dragMoved = false;
      };

      window.addEventListener("mouseup", endDrag);

      strip.addEventListener("click", (event) => {
        if (!suppressClick) return;
        event.preventDefault();
        event.stopPropagation();
        suppressClick = false;
      }, true);

      let touchStartX = 0;
      let touchStartY = 0;
      let touchMoved = false;

      strip.addEventListener("touchstart", (event) => {
        if (!event.touches.length) return;
        const touch = event.touches[0];
        touchStartX = touch.clientX;
        touchStartY = touch.clientY;
        touchMoved = false;
      }, { passive: true });

      strip.addEventListener("touchmove", (event) => {
        if (!event.touches.length) return;
        const touch = event.touches[0];
        const deltaX = Math.abs(touch.clientX - touchStartX);
        const deltaY = Math.abs(touch.clientY - touchStartY);
        if (deltaX > 10 && deltaX > deltaY) {
          touchMoved = true;
          beginStripInteraction();
        }
      }, { passive: true });

      strip.addEventListener("touchend", (event) => {
        if (touchMoved) return;
        const link = event.target.closest("a.date-day");
        if (!link) return;
        event.preventDefault();
        if (window.IKIDSNavigate?.(link.href)) return;
        window.location.assign(link.href);
      });

      const monthLabel = document.querySelector("[data-date-month-label]");

      function updateMonthLabel() {
        if (!monthLabel) return;
        const week = nearestWeek();
        const month = week?.dataset.monthLabel;
        const year = week?.dataset.yearLabel;
        if (month && year) {
          monthLabel.textContent = `${month} ${year}`;
        }
      }

      function showMonthLabel() {
        if (!monthLabel) return;
        monthLabel.classList.add("is-visible");
        monthLabel.setAttribute("aria-hidden", "false");
      }

      function hideMonthLabel() {
        if (!monthLabel) return;
        monthLabel.classList.remove("is-visible");
        monthLabel.setAttribute("aria-hidden", "true");
      }

      function beginStripInteraction() {
        if (initialScroll) return;
        stripUserActive = true;
        showMonthLabel();
        updateMonthLabel();
      }

      function endStripInteraction() {
        stripUserActive = false;
        hideMonthLabel();
      }

      hideMonthLabel();

      strip.addEventListener("scroll", () => {
        if (!stripUserActive) return;
        updateMonthLabel();
        updateWeekButtons();
        if (!("onscrollend" in strip)) {
          window.clearTimeout(scrollIdleTimer);
          scrollIdleTimer = window.setTimeout(() => {
            snapToNearestWeek();
            endStripInteraction();
          }, 120);
        }
      }, { passive: true });

      prevWeekButton?.addEventListener("click", () => shiftWeek(-1));
      nextWeekButton?.addEventListener("click", () => shiftWeek(1));
      updateWeekButtons();
      }

      window.IKIDSInitDateNavigation = initDateNavigation;
      initDateNavigation();
    })();
</script>
"""


def fast_navigation_script() -> str:
    return """
  <script>
    (() => {
      if (!window.fetch || !window.DOMParser || !window.history?.pushState) return;

      const allowedPaths = new Set(["/", "/schema", "/history"]);
      const pageCache = new Map();
      const maxCachedPages = 18;
      const pageCacheTtlMs = 12000;
      let navigationToken = 0;
      let prefetchTimer = 0;

      function toUrl(href) {
        try {
          return new URL(href, window.location.href);
        } catch {
          return null;
        }
      }

      function isRouteUrl(url) {
        return url
          && url.origin === window.location.origin
          && allowedPaths.has(url.pathname);
      }

      function isPlainNavigation(event, link) {
        if (!link || link.download || link.target && link.target !== "_self") return false;
        if (event && (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) {
          return false;
        }
        return isRouteUrl(toUrl(link.href));
      }

      function rememberPage(url, html) {
        const key = url.href;
        if (pageCache.has(key)) pageCache.delete(key);
        pageCache.set(key, { html, createdAt: performance.now() });
        while (pageCache.size > maxCachedPages) {
          pageCache.delete(pageCache.keys().next().value);
        }
      }

      async function fetchPage(url) {
        const key = url.href;
        const cached = pageCache.get(key);
        if (cached && performance.now() - cached.createdAt < pageCacheTtlMs) return cached.html;
        if (cached) pageCache.delete(key);
        const response = await fetch(key, {
          credentials: "same-origin",
          cache: "no-store",
          headers: { "X-IKids-Navigation": "1" },
        });
        const contentType = response.headers.get("content-type") || "";
        if (!response.ok || !contentType.includes("text/html")) {
          throw new Error("Navigation response was not HTML.");
        }
        const html = await response.text();
        rememberPage(url, html);
        return html;
      }

      function executeInsertedScripts(container) {
        container.querySelectorAll("script").forEach((oldScript) => {
          const script = document.createElement("script");
          Array.from(oldScript.attributes).forEach((attribute) => {
            script.setAttribute(attribute.name, attribute.value);
          });
          script.textContent = oldScript.textContent;
          oldScript.replaceWith(script);
        });
      }

      function updateContext(url) {
        const params = url.searchParams;
        window.IKIDS_CONTEXT = {
          role: params.get("role") || "manager",
          day: params.get("day") || "today",
        };
      }

      function applyPage(html, url, options = {}) {
        const nextDocument = new DOMParser().parseFromString(html, "text/html");
        const nextMain = nextDocument.querySelector("main");
        const currentMain = document.querySelector("main");
        if (!nextMain || !currentMain) {
          window.location.assign(url.href);
          return;
        }

        document.title = nextDocument.title || document.title;
        document.body.className = nextDocument.body.className;
        currentMain.replaceWith(document.importNode(nextMain, true));
        updateContext(url);
        executeInsertedScripts(document.querySelector("main"));
        window.requestAnimationFrame(() => {
          window.IKIDSInitDateNavigation?.();
          window.IKIDSInitPlanTip?.();
        });

        if (options.history !== "none") {
          window.history.pushState({}, "", url.href);
        }
        if (options.scroll !== false) {
          window.scrollTo({ top: 0, left: 0, behavior: "auto" });
        }
        schedulePrefetch();
      }

      async function navigate(href, options = {}) {
        const url = toUrl(href);
        if (!isRouteUrl(url)) return false;
        if (url.href === window.location.href && options.history !== "none") return true;

        const token = ++navigationToken;
        document.documentElement.classList.add("is-fast-navigating");
        document.querySelector("main")?.setAttribute("aria-busy", "true");
        try {
          const html = await fetchPage(url);
          if (token !== navigationToken) return true;
          applyPage(html, url, options);
          return true;
        } catch {
          window.location.assign(url.href);
          return true;
        } finally {
          if (token === navigationToken) {
            document.documentElement.classList.remove("is-fast-navigating");
            document.querySelector("main")?.removeAttribute("aria-busy");
          }
        }
      }

      function prefetch(href) {
        const url = toUrl(href);
        if (!isRouteUrl(url) || pageCache.has(url.href) || url.href === window.location.href) return Promise.resolve();
        return fetchPage(url).catch(() => undefined);
      }

      function schedulePrefetch() {
        window.clearTimeout(prefetchTimer);
        prefetchTimer = window.setTimeout(() => {
          // Never prefetch date-day links — 14 full HTML pages were freezing the single Fly VM.
          const links = Array.from(document.querySelectorAll(".tabs a[href]"))
            .filter((link) => isRouteUrl(toUrl(link.href)))
            .slice(0, 4);
          const run = async () => {
            for (const link of links) {
              await prefetch(link.href);
            }
          };
          if ("requestIdleCallback" in window) window.requestIdleCallback(() => { run(); }, { timeout: 2000 });
          else window.setTimeout(run, 400);
        }, 400);
      }

      window.IKIDSNavigate = (href, options) => {
        navigate(href, options);
        return true;
      };

      document.addEventListener("click", (event) => {
        const link = event.target.closest("a[href]");
        if (!isPlainNavigation(event, link)) return;
        event.preventDefault();
        navigate(link.href);
      });

      document.addEventListener("pointerover", (event) => {
        const link = event.target.closest("a[href]");
        if (isRouteUrl(toUrl(link?.href))) prefetch(link.href);
      }, { passive: true });

      document.addEventListener("touchstart", (event) => {
        const link = event.target.closest("a[href]");
        if (isRouteUrl(toUrl(link?.href))) prefetch(link.href);
      }, { passive: true });

      window.addEventListener("popstate", () => {
        navigate(window.location.href, { history: "none", scroll: false });
      });

      schedulePrefetch();
    })();
  </script>
"""


def page_template(
    content: str,
    message: str = "",
    errors: dict[str, str] | None = None,
    role: str = "manager",
    day: str = "today",
    page_class: str = "",
    logo_href: str = "",
    hub: str = "",
) -> bytes:
    errors = errors or {}
    alert = ""
    if message:
        alert = f'<div class="alert success">{escape(message)}</div>'
    elif errors:
        alert = '<div class="alert error">Popraw zaznaczone pola formularza.</div>'

    page_role = normalize_page_role(role)
    role = normalize_role(role)
    day = normalize_day(day)
    hub = normalize_hub(hub)
    logo_src = logo_asset_url()
    classes = []
    is_schedules = "page-schedules" in (page_class or "")
    is_inventory = "page-inventory" in (page_class or "")
    if page_role == "home" and not is_schedules and not is_inventory:
        classes.append("page-home")
        classes.append("page-home-landing")
    if page_class:
        classes.append(page_class)
    body_class = f' class="{" ".join(classes)}"' if classes else ""
    day_q = day_query(selected_day(day))
    home_href = logo_href or hub_home_href(day)
    brand_markup = (
        f'<a class="brand-link" href="{escape(home_href)}" aria-label="Strona główna">'
        f'<img class="logo" src="{logo_src}" alt="iKids Park"></a>'
    )

    document = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes, viewport-fit=cover">
  <meta name="theme-color" content="#ffffff" media="(max-width: 640px)">
  <meta name="theme-color" content="#139bd7" media="(min-width: 641px)">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="{APP_SHORT_TITLE}">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/app-icon-192-solid.png?v={pwa_icon_version()}" type="image/png">
  <link rel="apple-touch-icon" href="/app-icon-192-solid.png?v={pwa_icon_version()}">
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #000000;
      --muted: #555555;
      --line: #dddddd;
      --surface: #ffffff;
      --surface-strong: #ffffff;
      --soft: #f8f8f8;
      --brand: #139bd7;
      --brand-dark: #0b78ad;
      --logo-park: #b4b4b4;
      --logo-park-soft: color-mix(in srgb, var(--logo-park) 22%, white);
      --orange: #f58212;
      --lime: #7a9a12;
      --accent: #f58212;
      --danger: #dc2626;
      --danger-soft: #fde8e8;
      --ok: #65a30d;
      --ok-soft: #ecfccb;
      --busy: #ea580c;
      --busy-soft: #ffedd5;
      --focus: rgba(19, 155, 215, 0.25);
      --field: #ffffff;
      --field-strong: #ffffff;
      --menu-glow-blue: #139bd7;
      --menu-glow-orange: #f58212;
      --menu-glow-lime: #7a9a12;
      --menu-glow-noise: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160' viewBox='0 0 160 160'%3E%3Cfilter id='n' x='0' y='0'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.72' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)' opacity='.08'/%3E%3C/svg%3E");
    }}

    @keyframes ambient-blue-cycle {{
      0%, 16.666% {{
        opacity: 1;
      }}

      33.333%, 83.333% {{
        opacity: 0;
      }}

      100% {{
        opacity: 1;
      }}
    }}

    @keyframes ambient-orange-cycle {{
      0%, 16.666% {{
        opacity: 0;
      }}

      33.333%, 50% {{
        opacity: 1;
      }}

      66.666%, 100% {{
        opacity: 0;
      }}
    }}

    @keyframes ambient-lime-cycle {{
      0%, 50% {{
        opacity: 0;
      }}

      66.666%, 83.333% {{
        opacity: 1;
      }}

      100% {{
        opacity: 0;
      }}
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #ffffff;
      min-height: 100vh;
      overflow-x: hidden;
    }}

    body > header {{
      background: rgba(255, 255, 255, 0.88);
      position: sticky;
      top: 0;
      z-index: 20;
      overflow: visible;
    }}

    .topbar {{
      position: relative;
      max-width: 1380px;
      margin: 0 auto;
      padding: 10px 24px;
      min-height: 58px;
      overflow: visible;
      display: flex;
      align-items: center;
      justify-content: center;
    }}

    .install-button {{
      position: absolute;
      top: 6px;
      right: 10px;
      z-index: 6;
      box-sizing: border-box;
      padding: 6px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #a6a6a6;
      border-radius: 7px;
      background: #000;
      color: #fff;
      cursor: pointer;
    }}

    .install-button:hover {{
      background: #1a1a1a;
    }}

    .install-button:focus-visible {{
      outline: 2px solid var(--brand);
      outline-offset: 2px;
    }}

    .install-button__icon {{
      width: 15px;
      height: 15px;
      flex: 0 0 15px;
      display: block;
      fill: currentColor;
      stroke: none;
    }}

    .install-button[hidden] {{
      display: none;
    }}

    .install-popup {{
      position: fixed;
      inset: 0;
      z-index: 90;
      display: grid;
      place-items: center;
      padding: 20px;
      background:
        radial-gradient(circle at 20% 15%, color-mix(in srgb, var(--brand) 28%, transparent), transparent 42%),
        radial-gradient(circle at 85% 80%, color-mix(in srgb, var(--orange) 22%, transparent), transparent 40%),
        rgba(255, 255, 255, 0.55);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
    }}

    .install-popup[hidden] {{
      display: none !important;
    }}

    .install-popup__card {{
      width: min(100%, 320px);
      padding: 22px 20px 16px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--surface);
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.12);
      text-align: center;
    }}

    .install-popup__icon {{
      width: 56px;
      height: 56px;
      margin: 0 auto 12px;
      border-radius: 14px;
      display: block;
      object-fit: cover;
      border: 1px solid var(--line);
    }}

    .install-popup__title {{
      margin: 0 0 8px;
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: var(--ink);
    }}

    .install-popup__step {{
      margin: 0 0 16px;
      font-size: 0.98rem;
      line-height: 1.35;
      color: var(--muted);
      font-weight: 550;
    }}

    .install-popup__close {{
      width: 100%;
      border: 0;
      border-radius: 10px;
      padding: 11px 14px;
      background: var(--brand);
      color: #fff;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}

    .install-popup__close:hover {{
      background: var(--brand-dark);
    }}

    html.install-popup-open {{
      overflow: hidden;
    }}

    .date-month-label {{
      position: absolute;
      left: 50%;
      top: 0;
      transform: translateX(-50%);
      z-index: 3;
      margin: 0;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid var(--line);
      color: var(--ink);
      text-transform: uppercase;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      line-height: 1;
      white-space: nowrap;
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transition: opacity 0.24s ease, visibility 0.24s ease;
    }}

    .date-month-label.is-visible {{
      opacity: 1;
      visibility: visible;
    }}

    .brand {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      height: 38px;
      min-width: 0;
      overflow: visible;
      pointer-events: none;
    }}

    .brand-link {{
      pointer-events: auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      text-decoration: none;
      -webkit-tap-highlight-color: transparent;
    }}

    .brand-link:focus-visible {{
      outline: 2px solid var(--brand);
      outline-offset: 4px;
    }}

    .logo {{
      --logo-scale: 2.85;
      height: 38px;
      width: auto;
      max-width: 120px;
      object-fit: contain;
      flex: 0 0 auto;
      display: block;
      transform: scale(var(--logo-scale));
      transform-origin: center center;
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
      position: relative;
      z-index: 1;
    }}

    .toolbar {{
      display: grid;
      gap: 12px;
      margin-bottom: 18px;
      min-width: 0;
    }}

    .organizer-tools {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 0;
      align-items: center;
    }}

    .organizer-report-panel {{
      margin: 0;
      display: inline-flex;
      align-items: center;
    }}

    .shift-report-copy {{
      appearance: none;
      border: 1px solid var(--line);
      background: var(--surface-strong);
      color: var(--ink);
      font: inherit;
      font-weight: 800;
      font-size: 0.82rem;
      min-height: 36px;
      padding: 0 10px 0 12px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      text-decoration: none;
    }}

    .shift-report-copy:hover {{
      background: #eeeeee;
    }}

    .shift-report-copy:focus-visible {{
      outline: 2px solid var(--brand);
      outline-offset: 2px;
    }}

    .shift-report-copy.is-copied {{
      background: color-mix(in srgb, var(--ok) 14%, white);
      border-color: var(--ok);
    }}

    .shift-report-copy__icon {{
      width: 16px;
      height: 16px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
    }}

    .shift-report-copy__icon svg {{
      width: 100%;
      height: 100%;
      display: block;
    }}

    .cooperation-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-weight: 700;
      margin: 0;
    }}

    .cooperation-toggle input {{
      width: 18px;
      height: 18px;
      min-height: 18px;
      accent-color: var(--brand);
    }}

    .profile-tag-cooperation {{
      background: color-mix(in srgb, var(--orange) 18%, white);
      border-color: color-mix(in srgb, var(--orange) 45%, white);
      color: #9a4d00;
    }}

    .organizer-layout {{
      margin-top: 18px;
    }}

    .organizer-tools-board .section-head {{
      align-items: center;
      border-radius: 18px;
      border-bottom: 1px solid var(--line);
    }}

    .organizer-form-board form {{
      padding: 0;
      border: 1px solid var(--line);
      border-top: 0;
      border-radius: 0 0 18px 18px;
      background: #ffffff;
    }}

    .organizer-form-board .form-board {{
      border: 0;
      border-radius: 0;
    }}

    .organizer-form-board .actions {{
      padding: 14px 16px 18px;
    }}

    .organizer-day-list {{
      display: grid;
      gap: 14px;
      padding: 16px;
      border: 1px solid var(--line);
      border-top: 0;
      border-radius: 0 0 18px 18px;
      background:
        linear-gradient(180deg, rgba(19, 155, 215, 0.04), rgba(122, 154, 18, 0.05)),
        #ffffff;
    }}

    .organizer-day-list .timeline-card {{
      margin: 0;
    }}

    .organizer-day-list .empty {{
      margin: 0;
    }}

    .guest-count-block {{
      display: grid;
      gap: 8px;
      grid-column: 1 / span 2;
      min-width: 0;
    }}

    .guest-count-title {{
      font-size: 0.88rem;
      font-weight: 800;
      color: var(--ink);
    }}

    .guest-count-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      align-items: start;
    }}

    .guest-count-field {{
      display: grid;
      gap: 6px;
      min-width: 0;
    }}

    .guest-count-input {{
      width: 5.5rem;
      max-width: 100%;
      min-height: 40px;
    }}

    .date-toolbar {{
      min-width: 0;
      position: relative;
      display: block;
      width: 100%;
      max-width: 100%;
      padding: 0 52px;
      box-sizing: border-box;
    }}

    .date-week-jump {{
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      z-index: 5;
      width: 40px;
      height: 40px;
      border: 1px solid #000000;
      border-radius: 50%;
      background: #000000;
      color: #ffffff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
      transition: background 0.15s ease, color 0.15s ease, opacity 0.15s ease;
    }}

    .date-week-jump-prev {{
      left: 0;
    }}

    .date-week-jump-next {{
      right: 0;
    }}

    .date-week-jump:hover {{
      background: #000000;
      color: #ffffff;
    }}

    .date-week-jump:disabled {{
      opacity: 1;
      cursor: default;
    }}

    .date-week-jump svg {{
      width: 20px;
      height: 20px;
      stroke: currentColor;
      stroke-width: 2.5;
      stroke-linecap: round;
      stroke-linejoin: round;
      fill: none;
    }}

    .date-day {{
      position: relative;
      border: 0;
      background: transparent;
      color: var(--ink);
      text-decoration: none;
      font-weight: 900;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      user-select: none;
      cursor: pointer;
      touch-action: manipulation;
      -webkit-tap-highlight-color: transparent;
    }}

    .date-day-surface {{
      position: absolute;
      inset: 0;
      z-index: 1;
      border: 0;
      background: var(--surface-strong);
      border-radius: inherit;
      pointer-events: none;
    }}

    .date-strip {{
      --date-gap: 6px;
      position: relative;
      width: 100%;
      max-width: 100%;
      min-width: 0;
      gap: 0;
      overflow-x: auto;
      overscroll-behavior-x: contain;
      scroll-snap-type: x mandatory;
      scroll-behavior: auto;
      scrollbar-width: none;
      margin: 0;
      padding: 2px 0;
      -webkit-overflow-scrolling: touch;
      display: flex;
    }}

    .date-week {{
      flex: 0 0 100%;
      width: 100%;
      min-width: 100%;
      display: flex;
      gap: var(--date-gap);
      scroll-snap-align: start;
      scroll-snap-stop: always;
    }}

    .date-week .date-day {{
      flex: 1 1 0;
      width: auto;
      min-width: 0;
    }}

    .date-strip.is-dragging {{
      cursor: grabbing;
      user-select: none;
    }}

    .date-strip::-webkit-scrollbar {{
      display: none;
    }}

    .date-day {{
      flex: 0 0 auto;
      width: 44px;
      min-height: 44px;
      padding: 5px 6px;
      border-radius: 16px;
      flex-direction: column;
      gap: 3px;
    }}

    .date-day-name {{
      position: relative;
      z-index: 3;
      font-size: 0.68rem;
      color: var(--brand);
      line-height: 1;
      text-transform: uppercase;
    }}

    .date-day-number {{
      position: relative;
      z-index: 3;
      font-size: 0.95rem;
      line-height: 1.05;
    }}

    .date-day.is-active .date-day-surface {{
      background: #000000;
      box-shadow: none;
    }}

    .date-day.is-active {{
      color: white;
    }}

    .date-day.is-active .date-day-name {{
      color: rgba(255, 255, 255, 0.86);
    }}

    .date-day.is-today .date-day-surface {{
      background: var(--logo-park-soft);
    }}

    .date-day.is-today {{
      color: var(--ink);
    }}

    .date-day.is-today .date-day-name {{
      color: var(--brand);
    }}

    .date-day.is-today.is-active .date-day-surface {{
      background: #000000;
      box-shadow: none;
    }}

    .date-day.is-today.is-active {{
      color: white;
    }}

    .date-day.is-today.is-active .date-day-name {{
      color: rgba(255, 255, 255, 0.86);
    }}

    .date-day.is-today.is-active .date-day-number {{
      color: white;
    }}

    form {{
      display: grid;
      gap: 10px;
    }}

    .birthday-children-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}

    .birthday-child-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 120px auto;
      gap: 10px;
      align-items: end;
      padding: 10px;
      border: 1px solid var(--line);
      background: var(--field-strong);
    }}

    .location-picker {{
      display: grid;
      gap: 14px;
      padding: 14px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, var(--field-strong) 0%, var(--field) 100%);
      border-radius: 10px;
      transition: border-color 0.25s ease, box-shadow 0.25s ease;
    }}

    .location-picker.is-confirmed {{
      border-color: rgba(179, 211, 22, 0.55);
      box-shadow: 0 0 0 1px rgba(179, 211, 22, 0.18);
    }}

    .location-forms {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: stretch;
    }}

    .location-panel {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 8px;
      min-width: 0;
      min-height: 460px;
    }}

    .location-panel-body {{
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
    }}

    .location-panel-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}

    .location-form-title {{
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      font-weight: 900;
    }}

    .location-panel-badge {{
      font-size: 0.72rem;
      font-weight: 800;
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(19, 155, 215, 0.14);
      color: var(--brand);
      border: 1px solid rgba(19, 155, 215, 0.28);
      max-width: 58%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .location-panel-adult .location-panel-badge {{
      background: rgba(245, 130, 18, 0.12);
      color: var(--orange);
      border-color: rgba(245, 130, 18, 0.28);
    }}

    .location-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .location-chips-loft {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }}

    .location-chips-tables {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(40px, 1fr));
      gap: 6px;
    }}

    .location-chip {{
      display: grid;
      gap: 2px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--field);
      color: var(--ink);
      cursor: pointer;
      text-align: left;
      font: inherit;
      line-height: 1.15;
      transition: border-color 0.18s ease, background 0.18s ease, transform 0.12s ease;
      min-width: 0;
      touch-action: manipulation;
      -webkit-tap-highlight-color: transparent;
    }}

    .location-chip-loft {{
      min-height: 58px;
      align-content: center;
      justify-items: start;
    }}

    .location-chip-loft.location-chip-none {{
      justify-items: center;
      text-align: center;
    }}

    .location-chip:hover:not(:disabled) {{
      border-color: rgba(19, 155, 215, 0.55);
      background: rgba(19, 155, 215, 0.08);
    }}

    .location-chip:active:not(:disabled) {{
      transform: scale(0.98);
    }}

    .location-chip.is-selected {{
      border-color: var(--brand);
      background: rgba(19, 155, 215, 0.16);
      box-shadow: inset 0 0 0 1px rgba(19, 155, 215, 0.35);
    }}

    .location-panel-adult .location-chip.is-selected {{
      border-color: var(--orange);
      background: rgba(245, 130, 18, 0.14);
      box-shadow: inset 0 0 0 1px rgba(245, 130, 18, 0.35);
    }}

    .location-panel-adult .location-chip-adult-none {{
      justify-items: center;
      text-align: center;
      font-weight: 800;
    }}

    .location-chip.is-busy {{
      opacity: 0.45;
      cursor: not-allowed;
      text-decoration: line-through;
    }}

    .location-chip-main {{
      font-size: 0.82rem;
      font-weight: 800;
    }}

    .location-chip-sub {{
      font-size: 0.72rem;
      color: var(--muted);
    }}

    .location-chip-none {{
      min-height: 58px;
      align-content: center;
    }}

    .location-chip-table {{
      min-height: 36px;
      justify-items: center;
      text-align: center;
      padding: 6px 4px;
    }}

    .location-accordions {{
      display: grid;
      gap: 8px;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding-right: 4px;
    }}

    .location-accordion-range {{
      font-size: 0.72rem;
      font-weight: 700;
      color: var(--muted);
    }}

    .location-accordion {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--field-strong);
      overflow: hidden;
    }}

    .location-accordion-head {{
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border: 0;
      background: transparent;
      color: var(--ink);
      font: inherit;
      font-weight: 800;
      font-size: 0.84rem;
      cursor: pointer;
      text-align: left;
    }}

    .location-accordion-head-main {{
      display: grid;
      gap: 2px;
      min-width: 0;
    }}

    .location-accordion-head:hover {{
      background: #f3f3f3;
    }}

    .location-accordion-label {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .location-accordion-meta {{
      font-size: 0.72rem;
      font-weight: 800;
      color: var(--muted);
      padding: 2px 7px;
      border-radius: 999px;
      background: #eeeeee;
    }}

    .location-accordion-chevron {{
      width: 18px;
      height: 18px;
      display: grid;
      place-items: center;
      color: var(--muted);
      transition: transform 0.25s ease;
    }}

    .location-accordion-chevron::before {{
      content: "";
      width: 7px;
      height: 7px;
      border-right: 2px solid currentColor;
      border-bottom: 2px solid currentColor;
      transform: rotate(45deg) translate(-1px, -1px);
      transition: transform 0.25s ease;
    }}

    .location-accordion.is-open .location-accordion-chevron::before {{
      transform: rotate(-135deg) translate(-1px, -1px);
    }}

    .location-accordion-panel {{
      display: grid;
      grid-template-rows: 0fr;
      transition: grid-template-rows 0.28s ease;
      overflow: hidden;
    }}

    .location-accordion.is-open .location-accordion-panel {{
      grid-template-rows: 1fr;
    }}

    .location-accordion-body {{
      overflow: hidden;
      min-height: 0;
      padding: 0;
    }}

    .location-accordion.is-open .location-accordion-body {{
      padding: 0 10px 10px;
    }}

    .location-range-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
      gap: 8px;
      align-items: end;
      margin-bottom: 10px;
      padding: 8px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fafafa;
    }}

    .location-range-row label {{
      display: grid;
      gap: 4px;
      font-size: 0.72rem;
      font-weight: 800;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}

    .location-range-row input {{
      width: 100%;
      min-height: 34px;
      padding: 6px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--field);
      color: var(--ink);
      font: inherit;
      font-weight: 700;
    }}

    .location-range-apply {{
      min-height: 34px;
      padding: 6px 10px;
      font-size: 0.78rem;
      white-space: nowrap;
    }}

    .location-none-row {{
      margin-bottom: 8px;
    }}

    .location-none-row .location-chip {{
      width: 100%;
    }}

    .location-confirm-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-strong);
    }}

    .location-summary {{
      display: grid;
      gap: 4px;
      min-width: 0;
      flex: 1 1 220px;
    }}

    .location-summary-label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      font-weight: 900;
    }}

    .location-summary-values {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 0.84rem;
      font-weight: 700;
      line-height: 1.35;
    }}

    .location-summary-item {{
      padding: 4px 8px;
      border-radius: 6px;
      background: var(--field);
      border: 1px solid var(--line);
    }}

    .location-summary-item.is-empty {{
      color: var(--muted);
      font-weight: 600;
    }}

    .location-confirm-btn.is-confirmed {{
      background: var(--ok);
      border-color: var(--ok);
      color: #10140a;
    }}

    .location-select-native {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}

    .overlap-hint, .overlap-notice {{
      color: var(--danger);
      font-size: 0.79rem;
      font-weight: 800;
      line-height: 1.35;
    }}

    .overlap-notice {{
      padding: 0 12px 8px;
    }}

    .plan-block {{
      width: 100%;
    }}

    .plan-block-title {{
      display: block;
      width: 100%;
      text-align: center;
      padding: 16px 18px 8px;
      border: 0;
      background: transparent;
      color: var(--ink);
      font: inherit;
      font-size: clamp(1.25rem, 3.5vw, 1.85rem);
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      line-height: 1.1;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }}

    .plan-block-title:hover,
    .plan-block-title:focus-visible {{
      color: var(--brand);
      outline: none;
    }}

    .plan-block .plan-legend-bottom {{
      display: flex;
      justify-content: center;
      gap: 20px;
      margin-top: 12px;
      padding-bottom: 4px;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}

    .location-picker .plan-accordion {{
      margin-top: 14px;
      width: 100%;
    }}

    .plan-accordion {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      overflow: hidden;
      width: 100%;
    }}

    .plan-accordion .location-accordion-head-main {{
      display: grid;
      gap: 6px;
    }}

    .plan-accordion .location-accordion-head {{
      grid-template-columns: minmax(0, 1fr) auto;
    }}

    .plan-accordion .location-accordion-body {{
      padding: 0;
    }}

    .plan-accordion.is-open .location-accordion-body {{
      padding: 0 10px 10px;
    }}

    .plan-accordion .plan-wrap {{
      padding: 0;
    }}

    .plan-accordion.is-open .plan-wrap {{
      padding: 0 4px 4px;
    }}

    .plan-accordion:not(.is-open) .room-plan {{
      visibility: hidden;
    }}

    .location-picker .plan-wrap {{
      margin-top: 0;
      padding: 0;
    }}

    .location-hint {{
      padding: 0;
      margin: 0;
    }}

    .service-catalog {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      align-items: start;
    }}

    .reservation-type-switch {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      background: var(--field-strong);
    }}

    .reservation-type-switch legend {{
      padding: 0 4px;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 900;
      text-transform: uppercase;
    }}

    .reservation-type-switch label {{
      width: auto;
      min-height: 36px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--field);
    }}

    .reservation-type-switch input {{
      width: 18px;
      height: 18px;
      min-height: 18px;
      accent-color: var(--brand);
    }}

    .table-only {{
      display: none;
    }}

    #reservation-form.is-table-reservation .table-only {{
      display: grid;
    }}

    #reservation-form.is-table-reservation .banquet-only {{
      display: none !important;
    }}

    .service-catalog-item {{
      border: 1px solid var(--line);
      background: var(--field-strong);
      display: grid;
      gap: 0;
      align-self: start;
    }}

    .service-catalog-head, .service-catalog-body {{
      padding: 10px;
      display: grid;
      gap: 10px;
    }}

    .service-catalog-head {{
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      border-bottom: 1px solid var(--line);
    }}

    .service-catalog-item:not(.is-open) .service-catalog-head {{
      border-bottom: 0;
    }}

    .service-catalog-body.is-hidden {{
      display: none;
    }}

    .service-toggle-btn {{
      box-sizing: border-box;
      width: 36px;
      height: 36px;
      padding: 0;
      border: 1px solid rgba(101, 163, 13, 0.55);
      border-radius: 8px;
      background: var(--ok);
      color: #ffffff;
      font-size: 1.45rem;
      font-weight: 800;
      line-height: 1;
      cursor: pointer;
      display: inline-grid;
      place-items: center;
      -webkit-tap-highlight-color: transparent;
    }}

    .service-toggle-btn:hover,
    .service-toggle-btn:focus-visible {{
      outline: 3px solid var(--focus);
      filter: brightness(1.05);
    }}

    .service-toggle-btn.is-active {{
      background: var(--danger);
      border-color: rgba(220, 38, 38, 0.65);
    }}

    .service-toggle-btn .service-toggle-minus {{
      display: none;
    }}

    .service-toggle-btn.is-active .service-toggle-plus {{
      display: none;
    }}

    .service-toggle-btn.is-active .service-toggle-minus {{
      display: block;
    }}

    .animation-list {{
      display: grid;
      gap: 12px;
    }}

    .animation-row {{
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(0, 0.9fr) auto;
      gap: 10px;
      align-items: end;
      padding: 10px;
      border: 1px solid var(--line);
      background: #ffffff;
    }}

    .animation-row .service-extra {{
      margin: 0;
    }}

    .service-enabled-input {{
      position: absolute;
      opacity: 0;
      pointer-events: none;
      width: 0;
      height: 0;
      min-height: 0;
    }}

    .reservation-details {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}

    .stack > .timeline-card {{
      margin-left: 18px;
      margin-right: 18px;
    }}

    .day-heading {{
      text-align: center;
      padding: 16px 18px 8px;
      color: var(--ink);
      font-size: clamp(1.75rem, 5vw, 2.5rem);
      font-weight: 800;
      letter-spacing: 0.03em;
      line-height: 1.1;
    }}

    .stack > .day-heading + .timeline-card {{
      margin-top: 12px;
    }}

    .stack > .section-head + .timeline-card {{
      margin-top: 16px;
    }}

    .stack > .timeline-card:last-of-type {{
      margin-bottom: 16px;
    }}

    .stack > .timeline-card + .timeline-card {{
      margin-top: 14px;
    }}

    .timeline-card {{
      display: grid;
      gap: 16px;
      padding: 22px 24px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #ffffff;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
    }}

    .timeline-card.is-cancelled {{
      opacity: 0.72;
    }}

    .timeline-header {{
      display: flex;
      align-items: center;
      gap: clamp(16px, 2.5vw, 28px);
      width: 100%;
      flex-wrap: wrap;
      background: transparent;
      border: 0;
      position: static;
      z-index: auto;
      padding: 0;
      margin: 0;
    }}

    .timeline-header .animator-assign--waiter {{
      margin-left: auto;
      flex: 0 1 auto;
      max-width: min(100%, 28rem);
    }}

    .timeline-header.has-color .animator-assign__icon-btn {{
      background: #ffffff;
      border-color: rgba(0, 0, 0, 0.18);
      color: #111111;
    }}

    .timeline-header.has-color .animator-assign__chip {{
      background: rgba(255, 255, 255, 0.92);
    }}

    .timeline-card {{
      overflow: visible;
    }}

    .timeline-header.has-color {{
      background: var(--reservation-color);
      border-radius: 12px;
      padding: 14px 16px;
      color: #ffffff;
    }}

    .timeline-header.has-color .timeline-start,
    .timeline-header.has-color .profile-name {{
      color: #ffffff;
    }}

    .timeline-header.has-color .profile-tag {{
      background: rgba(255, 255, 255, 0.22);
      color: #ffffff;
      border-color: rgba(255, 255, 255, 0.35);
    }}

    .timeline-header.has-color .profile-guardian {{
      color: rgba(255, 255, 255, 0.92);
    }}

    .waiter-assignment {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: nowrap;
      flex: 1 1 420px;
      justify-content: flex-end;
      margin-left: auto;
      min-width: 0;
    }}

    .waiter-assignment-label {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 36px;
      padding: 7px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--field-strong);
      font-size: 0.84rem;
      color: var(--muted);
      white-space: nowrap;
      min-width: 0;
    }}

    .timeline-header.has-color .waiter-assignment-label {{
      background: #ffffff;
      border-color: rgba(0, 0, 0, 0.08);
      color: #374151;
    }}

    .waiter-assignment-label strong {{
      color: var(--ink);
      font-weight: 800;
    }}

    .timeline-header.has-color .waiter-assignment-label strong {{
      color: #000000;
    }}

    .waiter-picker {{
      position: relative;
      flex: 0 0 auto;
    }}

    .waiter-assignment .inline-form {{
      flex: 0 0 auto;
    }}

    .waiter-picker > summary,
    .waiter-remove-btn {{
      box-sizing: border-box;
      width: 86px;
      height: 36px;
      min-height: 36px;
      padding: 0 12px;
      border-radius: 999px;
      font-family: inherit;
      font-size: 0.84rem;
      font-weight: 800;
      line-height: 1;
    }}

    .waiter-picker > summary {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      list-style: none;
      cursor: pointer;
    }}

    .waiter-picker > summary::-webkit-details-marker {{
      display: none;
    }}

    .waiter-options {{
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      z-index: 30;
      min-width: 220px;
      max-height: 260px;
      overflow-y: auto;
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
      padding: 6px;
      display: grid;
      gap: 4px;
    }}

    .waiter-option-form {{
      padding: 0;
      margin: 0;
    }}

    .waiter-option {{
      width: 100%;
      justify-content: flex-start;
      background: transparent;
      color: var(--ink);
      border: 0;
      min-height: 36px;
      padding: 8px 10px;
      font-weight: 700;
    }}

    .waiter-option:hover {{
      background: #f3f3f3;
    }}

    .waiter-remove-btn {{
      color: var(--danger);
    }}

    .visually-hidden {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}

    .task-label-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }}

    .task-label-row .task-label {{
      flex: 1 1 auto;
      min-width: 0;
    }}

    .animator-assign {{
      position: relative;
      display: inline-flex;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 4px;
      flex: 0 0 auto;
      margin: 0;
      padding: 0;
      border: 0;
      background: transparent;
    }}

    .animator-assign__chip {{
      display: inline-flex;
      align-items: center;
      gap: 2px;
      flex: 0 0 auto;
      width: max-content;
      height: 32px;
      max-width: min(100%, 14rem);
      padding: 0 1px 0 2px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--ok) 14%, white);
      color: #3f6212;
      font-size: 0.74rem;
      font-weight: 800;
      line-height: 1;
    }}

    .animator-assign__chip-initials {{
      width: 22px;
      height: 22px;
      border-radius: 999px;
      display: inline-grid;
      place-items: center;
      flex: 0 0 auto;
      background: color-mix(in srgb, var(--ok) 22%, white);
      font-size: 0.62rem;
      letter-spacing: 0.02em;
      line-height: 1;
    }}

    .animator-assign__chip-name {{
      min-width: 0;
      flex: 0 1 auto;
      padding: 0 2px 0 1px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      line-height: 1;
    }}

    .animator-assign__chip-remove-form {{
      margin: 0;
      padding: 0;
      display: inline-flex;
      align-items: center;
      flex: 0 0 auto;
      width: auto;
    }}

    button.animator-assign__chip-remove {{
      appearance: none;
      box-sizing: border-box;
      width: 18px;
      height: 18px;
      min-height: 0;
      min-width: 0;
      padding: 0;
      margin: 0;
      border: 0;
      border-radius: 999px;
      background: transparent;
      color: #3f6212;
      font-family: inherit;
      font-size: 0.85rem;
      font-weight: 800;
      line-height: 1;
      letter-spacing: 0;
      display: inline-grid;
      place-items: center;
      cursor: pointer;
    }}

    button.animator-assign__chip-remove:hover {{
      background: color-mix(in srgb, var(--danger) 14%, white);
      color: var(--danger);
    }}

    .animator-assign__avatar {{
      width: 28px;
      height: 28px;
      border-radius: 999px;
      display: inline-grid;
      place-items: center;
      flex: 0 0 auto;
      background: color-mix(in srgb, var(--brand) 16%, white);
      color: var(--brand-dark);
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.02em;
    }}

    .animator-assign__picker {{
      position: relative;
      flex: 0 0 auto;
      z-index: 1;
    }}

    .animator-assign__picker[open] {{
      z-index: 14000;
    }}

    .animator-assign__icon-btn {{
      list-style: none;
      cursor: pointer;
      display: inline-grid;
      place-items: center;
      width: 32px;
      height: 32px;
      padding: 0;
      border: 1px solid color-mix(in srgb, var(--brand) 28%, var(--line));
      border-radius: 999px;
      background: #ffffff;
      color: var(--brand-dark);
      box-shadow: none;
    }}

    .animator-assign__icon-btn::-webkit-details-marker {{
      display: none;
    }}

    .animator-assign__icon-btn:hover,
    .animator-assign__picker[open] > .animator-assign__icon-btn {{
      background: color-mix(in srgb, var(--brand) 10%, white);
      border-color: color-mix(in srgb, var(--brand) 45%, var(--line));
    }}

    .animator-assign.is-assigned .animator-assign__icon-btn {{
      border-color: var(--line);
      color: var(--ink);
    }}

    .animator-assign__icon-btn svg {{
      width: 16px;
      height: 16px;
      display: block;
    }}

    .animator-assign__sheet {{
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      left: auto;
      z-index: 14000;
      width: min(300px, 86vw);
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #ffffff;
      box-shadow: 0 18px 42px rgba(0, 0, 0, 0.2);
    }}

    .animator-assign__sheet.is-ported {{
      position: fixed;
      margin: 0;
    }}

    .animator-assign__search input {{
      width: 100%;
      min-height: 40px;
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f8f8f8;
      padding: 0 12px;
      font: inherit;
      font-size: 0.9rem;
    }}

    .animator-assign__search input:focus {{
      outline: 2px solid var(--brand);
      outline-offset: 1px;
      background: #ffffff;
    }}

    .animator-assign__list {{
      display: grid;
      gap: 4px;
      max-height: 240px;
      overflow-y: auto;
      padding-right: 2px;
    }}

    .animator-assign__option-form {{
      margin: 0;
      padding: 0;
    }}

    .animator-assign__option {{
      width: 100%;
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 40px;
      padding: 6px 8px;
      border: 0;
      border-radius: 10px;
      background: transparent;
      color: var(--ink);
      font: inherit;
      font-weight: 700;
      text-align: left;
      cursor: pointer;
    }}

    .animator-assign__option:hover {{
      background: color-mix(in srgb, var(--brand) 10%, white);
    }}

    .animator-assign__option-name {{
      min-width: 0;
      line-height: 1.25;
    }}

    .animator-assign__empty {{
      margin: 0;
      padding: 12px 8px;
      color: var(--muted);
      font-size: 0.86rem;
      text-align: center;
    }}

    .timeline-start {{
      flex: 0 0 auto;
      font-size: 2rem;
      font-weight: 700;
      line-height: 1;
      letter-spacing: -0.03em;
      color: var(--ink);
      font-variant-numeric: tabular-nums;
      min-width: 4.75rem;
    }}

    .profile-identity {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex: 0 1 auto;
      flex-wrap: wrap;
      min-width: 0;
    }}

    .profile-name {{
      margin: 0;
      font-size: 1.35rem;
      font-weight: 600;
      line-height: 1.25;
      color: var(--ink);
    }}

    .profile-tags {{
      display: inline-flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }}

    .profile-tag {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.8125rem;
      line-height: 1.35;
      white-space: nowrap;
    }}

    .profile-tag-age {{
      background: #eeeeee;
      color: var(--muted);
    }}

    .animator-card .profile-tag-guests,
    .kitchen-card .profile-tag-guests {{
      color: #000000;
      font-weight: 650;
    }}

    .profile-tag-room {{
      font-weight: 500;
    }}

    .profile-tag-room-winter {{
      background: rgba(59, 130, 246, 0.15);
      color: #60a5fa;
    }}

    .profile-tag-room-white-house {{
      background: rgba(148, 163, 184, 0.2);
      color: #475569;
    }}

    .profile-tag-room-forest {{
      background: rgba(34, 197, 94, 0.14);
      color: #4ade80;
    }}

    .profile-tag-room-fairy {{
      background: rgba(192, 132, 252, 0.14);
      color: #c084fc;
    }}

    .profile-tag-room-space {{
      background: rgba(129, 140, 248, 0.14);
      color: #818cf8;
    }}

    .profile-tag-room-football {{
      background: rgba(74, 222, 128, 0.14);
      color: #86efac;
    }}

    .profile-tag-room-default {{
      background: rgba(148, 163, 184, 0.2);
      color: #64748b;
    }}

    .profile-tag-icon {{
      font-size: 0.72rem;
      line-height: 1;
    }}

    .profile-guardian {{
      display: inline-flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      margin-left: auto;
      font-size: 0.95rem;
      color: var(--muted);
      line-height: 1.35;
      flex: 0 0 auto;
    }}

    .profile-phone {{
      color: #ffffff;
      background: #000000;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 0.84rem;
      font-weight: 800;
    }}

    .profile-guardian-svg {{
      width: 14px;
      height: 14px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex-shrink: 0;
    }}

    .timeline-status {{
      flex: 0 0 auto;
    }}

    .timeline-logistics {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding-top: 2px;
    }}

    .logistics-column {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-width: 0;
    }}

    .logistics-chip {{
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 8px;
      background: #f0f0f0;
      min-width: 0;
    }}

    .logistics-chip-icon {{
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      margin-top: 1px;
    }}

    .logistics-chip-svg {{
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}

    .logistics-chip-location .logistics-chip-icon {{
      color: #60a5fa;
    }}

    .logistics-chip-attraction .logistics-chip-icon {{
      color: #c084fc;
    }}

    .logistics-chip-kitchen .logistics-chip-icon {{
      color: #4ade80;
    }}

    .logistics-chip-cake .logistics-chip-icon {{
      color: #fb923c;
    }}

    .logistics-chip-content {{
      display: grid;
      gap: 2px;
      min-width: 0;
    }}

    .logistics-chip-text {{
      font-size: 0.9rem;
      line-height: 1.4;
      color: var(--ink);
      font-weight: 500;
    }}

    .logistics-chip-sub {{
      font-size: 0.78rem;
      line-height: 1.35;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}

    .reservation-callout {{
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 11px 14px 11px 12px;
      border-radius: 10px;
      border: 0;
    }}

    .reservation-callout-warning {{
      background: #fff7ed;
      border-left: 4px solid #f59e0b;
      color: #92400e;
    }}

    .reservation-callout-danger {{
      background: #fef2f2;
      border-left: 4px solid #ef4444;
      color: #991b1b;
    }}

    .reservation-callout-icon {{
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      margin-top: 1px;
      color: inherit;
      opacity: 0.9;
    }}

    .reservation-callout-text {{
      margin: 0;
      font-size: 0.88rem;
      line-height: 1.45;
      color: inherit;
      min-width: 0;
    }}

    .status-badge {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.01em;
      white-space: nowrap;
    }}

    .status-badge-active {{
      background: var(--ok-soft);
      color: #3f6212;
      border: 1px solid #84cc16;
    }}

    .status-badge-cancelled {{
      background: var(--danger-soft);
      color: var(--danger);
      border: 1px solid #fca5a5;
    }}

    .status-badge-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #34d399;
      box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55);
      animation: status-pulse 2s ease-in-out infinite;
    }}

    @keyframes status-pulse {{
      0%, 100% {{
        opacity: 1;
        box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.45);
      }}
      50% {{
        opacity: 0.65;
        box-shadow: 0 0 0 4px rgba(52, 211, 153, 0);
      }}
    }}

    .status-badge-reason {{
      display: block;
      margin-top: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--muted);
      white-space: normal;
      max-width: 220px;
    }}

    .timeline-footer {{
      padding-top: 2px;
    }}

    .reservation-author {{
      margin-top: 2px;
      padding: 10px 12px;
      border-top: 1px solid var(--line);
      background: rgba(19, 155, 215, 0.06);
      color: var(--brand-dark);
      font-size: 0.88rem;
      font-weight: 700;
      line-height: 1.35;
    }}

    .reservation-author strong {{
      font-weight: 900;
      color: var(--ink);
    }}

    .reservation-block {{
      display: grid;
      gap: 4px;
      padding: 10px;
      border: 1px solid var(--line);
      background: var(--field);
    }}

    .reservation-label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.02em;
      color: var(--muted);
      font-weight: 900;
    }}

    .reservation-list {{
      margin: 0;
      padding-left: 18px;
    }}

    .banquet-notes {{
      padding: 8px 12px;
      border-bottom: 1px solid var(--line);
      font-size: 0.86rem;
    }}

    .banquet-header-block .banquet-header {{
      padding: 0;
    }}

    .banquet-header-block {{
      padding: 0;
      border: 0;
      background: transparent;
    }}

    .banquet-header-inline .banquet-header {{
      padding: 0;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}

    @media (max-width: 1120px) {{
      .banquet-header {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}

    .table-node {{
      cursor: pointer;
    }}

    .room-node {{
      cursor: pointer;
    }}

    .tabs {{
      display: flex;
      flex-wrap: nowrap;
      gap: 20px;
      width: 100%;
      min-width: 0;
    }}

    .tab {{
      flex: 1 1 0;
      border: 1px solid var(--line);
      min-height: 52px;
      padding: 12px 16px;
      background: var(--surface-strong);
      color: var(--ink);
      text-decoration: none;
      font-weight: 800;
      font-size: 0.94rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      min-width: 0;
      text-align: center;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
    }}

    .tabs .tab:focus,
    .tabs .tab:focus-visible,
    .tabs .tab:active {{
      outline: none;
      box-shadow: none;
    }}

    .tab-icon {{
      width: 22px;
      height: 22px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      color: currentColor;
    }}

    .tab-icon svg {{
      width: 100%;
      height: 100%;
      display: block;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}

    .tab-label {{
      min-width: 0;
    }}

    .tab-label-mobile {{
      display: none;
    }}

    .tab[aria-current="page"] {{
      background: #000000;
      border-color: #000000;
      color: white;
    }}

    .tab-home {{
      flex: 1 1 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 52px;
      padding: 8px 12px;
    }}

    .tab-home[aria-current="page"] {{
      background: #000000;
      border-color: #000000;
    }}

    .tab-home-logo {{
      width: 40px;
      height: 40px;
      object-fit: contain;
      display: block;
      border: none;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}

    .home-summary {{
      margin-top: 4px;
    }}

    .home-summary .metrics {{
      margin-bottom: 0;
    }}

    .hub-choice {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}

    .hub-choice__btn {{
      box-sizing: border-box;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 14px;
      min-height: 132px;
      padding: 28px 18px;
      border: 2px solid #000000;
      border-radius: 18px;
      background: #ffffff;
      color: #000000;
      text-decoration: none;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
      transition: background 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
    }}

    .hub-choice__btn:hover {{
      background: #f3f3f3;
      box-shadow: 0 6px 18px rgba(0, 0, 0, 0.10);
      transform: translateY(-1px);
    }}

    .hub-choice__btn:focus-visible {{
      outline: 3px solid #000000;
      outline-offset: 3px;
    }}

    .hub-choice__icon {{
      width: 42px;
      height: 42px;
      display: block;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}

    .hub-choice__label {{
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
      font-size: clamp(1.15rem, 3.6vw, 1.45rem);
      font-weight: 700;
      letter-spacing: -0.02em;
      line-height: 1;
    }}

    .inventory-lines-block {{
      display: grid;
      gap: 12px;
    }}

    .inventory-line-row {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      align-items: end;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
    }}

    .inventory-line-row .full {{
      grid-column: 1 / -1;
    }}

    .inventory-line-row .remove-inventory-line {{
      justify-self: start;
    }}

    .inventory-page {{
      display: grid;
      gap: 16px;
      min-width: 0;
    }}

    .inventory-board {{
      overflow: hidden;
    }}

    .inventory-board > .section-head {{
      margin: 0;
    }}

    .inventory-board--intro > .section-head {{
      align-items: center;
      border-radius: 18px;
      border-bottom: 1px solid var(--line);
    }}

    .inventory-jump {{
      display: none;
      gap: 8px;
      padding: 12px;
      border: 1px solid var(--line);
      border-top: 0;
      border-radius: 0 0 18px 18px;
      background: #ffffff;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: none;
    }}

    .inventory-jump::-webkit-scrollbar {{
      display: none;
    }}

    .inventory-jump a {{
      flex: 1 0 auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 8px 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--soft);
      color: var(--ink);
      font-size: 0.84rem;
      font-weight: 800;
      text-decoration: none;
      white-space: nowrap;
    }}

    .inventory-jump a:hover {{
      border-color: var(--brand);
      color: var(--brand-dark);
      background: rgba(19, 155, 215, 0.08);
    }}

    .inventory-body {{
      display: grid;
      gap: 12px;
      padding: 14px 16px 16px;
      border: 1px solid var(--line);
      border-top: 0;
      border-radius: 0 0 18px 18px;
      background:
        linear-gradient(180deg, rgba(19, 155, 215, 0.04), rgba(122, 154, 18, 0.05)),
        #ffffff;
      min-width: 0;
    }}

    .inventory-list {{
      display: grid;
      gap: 10px;
      min-width: 0;
    }}

    @media (min-width: 720px) {{
      .inventory-list {{
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      }}
    }}

    @media (max-width: 860px) {{
      .inventory-add-form {{
        grid-template-columns: 1fr;
      }}
    }}

    .inventory-card {{
      display: grid;
      gap: 8px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #ffffff;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
      min-width: 0;
    }}

    .inventory-card__head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
    }}

    .inventory-card__kicker {{
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 900;
      letter-spacing: 0.03em;
      line-height: 1.25;
      text-transform: uppercase;
    }}

    .inventory-card__title {{
      margin: 0;
      color: var(--ink);
      font-size: 1.05rem;
      font-weight: 900;
      line-height: 1.25;
      word-break: break-word;
    }}

    .inventory-card__meta {{
      margin: 0;
      color: var(--muted);
      font-size: 0.86rem;
      font-weight: 700;
      line-height: 1.35;
      word-break: break-word;
    }}

    .inventory-card__qty {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 2.4rem;
      min-height: 2.4rem;
      padding: 4px 10px;
      border-radius: 12px;
      border: 1px solid rgba(122, 154, 18, 0.28);
      background: rgba(122, 154, 18, 0.12);
      color: var(--ink);
      font-size: 1.05rem;
      font-weight: 900;
      line-height: 1;
      flex-shrink: 0;
    }}

    .inventory-empty {{
      margin: 0;
      padding: 18px 14px;
      border: 1px dashed var(--line);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--muted);
      font-size: 0.9rem;
      font-weight: 700;
      text-align: center;
      line-height: 1.4;
    }}

    .inventory-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 2px;
    }}

    .inventory-actions form {{
      padding: 0;
      margin: 0;
      display: contents;
    }}

    .inventory-actions button,
    .inventory-actions .button {{
      flex: 1 1 auto;
      min-width: 0;
    }}

    .inventory-section-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
    }}

    .inventory-section-actions .count {{
      margin: 0;
    }}

    .inventory-empty-actions {{
      display: grid;
      gap: 12px;
      justify-items: center;
      padding: 8px 0 4px;
    }}

    .inventory-empty-actions .inventory-empty {{
      margin: 0;
      width: 100%;
    }}

    .inventory-add-block.is-collapsed:not([open]) > *:not(summary) {{
      display: none;
    }}

    .inventory-add-block > summary {{
      cursor: pointer;
      list-style: none;
      font-weight: 900;
      font-size: 0.76rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      min-height: 36px;
      display: flex;
      align-items: center;
    }}

    .inventory-add-block > summary::-webkit-details-marker {{
      display: none;
    }}

    .inventory-filters {{
      display: grid;
      gap: 10px;
      padding: 12px 14px 14px;
      border-top: 1px solid var(--line);
      background: var(--surface);
    }}

    .inventory-filters__search {{
      width: 100%;
      min-height: 44px;
      margin: 0;
    }}

    .inventory-filter-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .inventory-filter-chips button {{
      border: 1px solid var(--line);
      background: var(--surface-strong);
      color: var(--ink);
      min-height: 36px;
      padding: 6px 12px;
      border-radius: 999px;
      font-weight: 800;
      font-size: 0.82rem;
      cursor: pointer;
    }}

    .inventory-filter-chips button.is-active {{
      background: var(--brand);
      border-color: var(--brand);
      color: #fff;
    }}

    .inventory-card[hidden],
    .inventory-empty[hidden] {{
      display: none !important;
    }}

    .inventory-edit {{
      margin-top: 8px;
      border-top: 1px dashed var(--line);
      padding-top: 8px;
    }}

    .inventory-edit > summary {{
      cursor: pointer;
      font-weight: 800;
      font-size: 0.86rem;
      color: var(--brand);
      list-style: none;
      min-height: 36px;
      display: inline-flex;
      align-items: center;
    }}

    .inventory-edit > summary::-webkit-details-marker {{
      display: none;
    }}

    .inventory-edit-form {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }}

    .inventory-edit-form .full {{
      grid-column: 1 / -1;
    }}

    .inventory-edit-form label {{
      display: grid;
      gap: 4px;
      font-size: 0.78rem;
      font-weight: 800;
      color: var(--muted);
    }}

    .inventory-edit-form input,
    .inventory-edit-form select {{
      min-height: 40px;
    }}

    .inventory-edit-form .inventory-edit-actions {{
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .inventory-add-block {{
      display: grid;
      gap: 10px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #ffffff;
    }}

    .inventory-add-block__title {{
      margin: 0;
      color: var(--ink);
      font-size: 0.76rem;
      font-weight: 900;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}

    .inventory-scan-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}

    .inventory-scan-status {{
      margin: 0;
      min-height: 1.2em;
      color: var(--muted);
      font-size: 0.86rem;
      font-weight: 700;
    }}

    .inventory-scan-status.is-ok {{
      color: #166534;
    }}

    .inventory-scan-status.is-error {{
      color: var(--danger);
    }}

    .inventory-scan-overlay {{
      position: fixed;
      inset: 0;
      z-index: 80;
      display: none;
      grid-template-rows: auto 1fr auto;
      background: rgba(0, 0, 0, 0.88);
      color: #fff;
      padding: max(12px, env(safe-area-inset-top)) 12px max(12px, env(safe-area-inset-bottom));
    }}

    .inventory-scan-overlay.is-open {{
      display: grid;
    }}

    .inventory-scan-overlay__head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 4px 4px 12px;
    }}

    .inventory-scan-overlay__head h3 {{
      margin: 0;
      font-size: 1.05rem;
    }}

    .inventory-scan-overlay__body {{
      position: relative;
      min-height: 0;
      border-radius: 16px;
      overflow: hidden;
      background: #111;
    }}

    .inventory-scan-overlay__body video,
    .inventory-scan-overlay__body #inventory-scan-reader {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      min-height: 280px;
    }}

    .inventory-scan-overlay__hint {{
      margin: 12px 4px 0;
      text-align: center;
      font-size: 0.9rem;
      opacity: 0.9;
    }}

    .inventory-new-ean {{
      display: none;
      gap: 10px;
      padding: 12px;
      border: 1px dashed rgba(19, 155, 215, 0.45);
      border-radius: 12px;
      background: rgba(19, 155, 215, 0.06);
    }}

    .inventory-new-ean.is-open {{
      display: grid;
    }}

    .inventory-new-ean__title {{
      margin: 0;
      font-size: 0.9rem;
      font-weight: 800;
      color: var(--brand-dark);
    }}

    .inventory-card__ean {{
      margin: 0;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      letter-spacing: 0.02em;
    }}

    .inventory-add-form {{
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 1.4fr) minmax(5.5rem, 0.55fr);
      gap: 10px;
      align-items: end;
      padding: 0;
      margin: 0;
    }}

    .inventory-add-form .full {{
      grid-column: 1 / -1;
    }}

    .inventory-add-form .inventory-add-submit {{
      grid-column: 1 / -1;
      justify-self: start;
    }}

    .inventory-status {{
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      font-size: 0.78rem;
      font-weight: 900;
      line-height: 1.2;
      white-space: nowrap;
      flex-shrink: 0;
    }}

    .inventory-status.is-issued {{
      background: #e8f7ee;
      border-color: #8dcf9f;
      color: #166534;
    }}

    .inventory-status.is-open {{
      background: #fff7e8;
      border-color: #f0c36d;
      color: #92400e;
    }}

    .inventory-status.is-bought {{
      background: rgba(19, 155, 215, 0.1);
      border-color: rgba(19, 155, 215, 0.28);
      color: var(--brand-dark);
    }}

    .inventory-status.is-todo {{
      background: #fff7e8;
      border-color: #f0c36d;
      color: #92400e;
    }}

    html:has(body.page-home) {{
      overflow: hidden;
      height: 100%;
    }}

    html:has(body.page-schedules),
    html:has(body.page-inventory) {{
      overflow-y: auto;
      overflow-x: clip;
      height: auto;
    }}

    body.page-home {{
      overflow: hidden;
      height: 100dvh;
      overscroll-behavior: none;
    }}

    body.page-schedules,
    body.page-inventory {{
      overflow-y: auto;
      overflow-x: clip;
      height: auto;
      min-height: 100vh;
      overscroll-behavior: auto;
    }}

    body.page-home main {{
      overflow: hidden;
    }}

    body.page-schedules main,
    body.page-inventory main {{
      overflow-x: clip;
      overflow-y: visible;
      min-width: 0;
      max-width: 100%;
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

    .manager-layout {{
      margin-top: 18px;
    }}

    .manager-layout .plan-accordion {{
      width: 100%;
    }}

    .stack > .section-head {{
      background: var(--surface);
      border: 1px solid var(--line);
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
      background: #f0f0f0;
      color: var(--ink);
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

    .category-fields.termin {{
      grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr) minmax(0, 0.9fr);
      column-gap: 28px;
      row-gap: 16px;
    }}

    .category-fields.termin > label {{
      min-width: 0;
    }}

    .category-fields.termin input[type="date"],
    .category-fields.termin input[name="party_start_time"] {{
      min-width: 0;
    }}

    .category-fields.services {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
      align-items: start;
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
      background: var(--field);
    }}

    input::placeholder, textarea::placeholder {{
      color: #888888;
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

    .cake-photo-control {{
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
    }}

    .cake-photo-file {{
      display: none;
    }}

    .cake-photo-trigger {{
      width: 44px;
      height: 44px;
      min-height: 44px;
      padding: 0;
      border-radius: 999px;
      background: var(--surface-strong);
      color: var(--ink);
      border: 1px solid var(--line);
      grid-column: 2;
    }}

    .cake-photo-trigger svg {{
      width: 22px;
      height: 22px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}

    .cake-photo-menu {{
      position: absolute;
      right: 0;
      top: calc(44px + 8px);
      z-index: 20;
      min-width: 150px;
      display: grid;
      gap: 4px;
      padding: 6px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #ffffff;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
    }}

    .cake-photo-menu button {{
      min-height: 36px;
      justify-content: flex-start;
      background: transparent;
      color: var(--ink);
      border: 0;
      padding: 8px 10px;
      font-size: 0.86rem;
      font-weight: 800;
    }}

    .cake-photo-menu button:hover {{
      background: #f3f3f3;
    }}

    .cake-photo-preview {{
      grid-column: 1 / -1;
      display: grid;
      gap: 8px;
    }}

    .cake-photo-preview img {{
      width: 100%;
      height: auto;
      object-fit: contain;
      border: 1px solid var(--line);
      background: #ffffff;
    }}

    .cake-photo-preview button {{
      justify-self: start;
      min-height: 34px;
      padding: 7px 10px;
      border-radius: 999px;
      background: #f3f3f3;
      color: var(--danger);
      font-size: 0.82rem;
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
      touch-action: manipulation;
      -webkit-tap-highlight-color: transparent;
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
      background: #eeeeee;
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
      border-color: #84cc16;
    }}

    .alert.error {{
      color: var(--danger);
      background: var(--danger-soft);
      border-color: #fca5a5;
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
      color: var(--ink);
      background: #f0f0f0;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0;
    }}

    tbody tr:nth-child(even) {{
      background: #f3f3f3;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      background: #e0f2fe;
      color: #0369a1;
      font-weight: 900;
      font-size: 0.76rem;
      margin: 0 4px 4px 0;
      white-space: nowrap;
    }}

    .pill.ok {{
      background: var(--ok-soft);
      color: #3f6212;
      border: 1px solid #84cc16;
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
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}

    .metric {{
      display: grid;
      gap: 8px;
      place-items: center;
      text-align: center;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #ffffff;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
    }}

    a.metric {{
      color: inherit;
      text-decoration: none;
      cursor: pointer;
      transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
    }}

    a.metric:hover {{
      border-color: #000000;
      box-shadow: 0 6px 18px rgba(0, 0, 0, 0.10);
      transform: translateY(-1px);
    }}

    a.metric:focus-visible {{
      outline: 3px solid #000000;
      outline-offset: 3px;
    }}

    .metric-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      color: var(--brand);
    }}

    .metric-icon svg {{
      width: 30px;
      height: 30px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}

    .metric-icon-animacje {{
      color: #9333ea;
    }}

    .metric-icon-bankiety {{
      color: #0d9488;
    }}

    .metric-icon-warsztaty {{
      color: #65a30d;
    }}

    .metric-icon-torty {{
      color: #ea580c;
    }}

    .metric-icon-piniaty {{
      color: #db2777;
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

    .role-board {{
      border: 0;
      border-radius: 18px;
      background: linear-gradient(180deg, #ffffff 0%, #f8fbfd 100%);
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
      overflow: hidden;
    }}

    .animator-board {{
      overflow: visible;
    }}

    .animator-board .banquet-grid {{
      overflow: visible;
    }}

    .animator-card .banquet-tasks,
    .animator-card .banquet-task,
    .animator-card .task-detail,
    .animator-card .task-label-row {{
      overflow: visible;
    }}

    .role-board .section-head {{
      border: 1px solid var(--line);
      border-bottom: 0;
      border-radius: 18px 18px 0 0;
      background:
        linear-gradient(135deg, rgba(19, 155, 215, 0.13), rgba(255, 255, 255, 0) 42%),
        linear-gradient(315deg, rgba(245, 130, 18, 0.12), rgba(255, 255, 255, 0) 38%),
        #ffffff;
      padding: 18px 20px;
    }}

    .role-board .section-head h2 {{
      font-size: 1.22rem;
      font-weight: 900;
    }}

    .role-board .count {{
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 5px 11px;
      border-radius: 999px;
      border: 1px solid rgba(19, 155, 215, 0.22);
      background: rgba(19, 155, 215, 0.09);
      color: var(--brand-dark);
      font-weight: 900;
    }}

    .role-board .banquet-grid {{
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      padding: 16px;
      border: 1px solid var(--line);
      border-top: 0;
      border-radius: 0 0 18px 18px;
      background:
        linear-gradient(180deg, rgba(19, 155, 215, 0.04), rgba(122, 154, 18, 0.05)),
        #ffffff;
    }}

    .kitchen-columns {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 0;
      border-top: 1px solid var(--line);
    }}

    .kitchen-column-cell {{
      padding: 10px 12px;
      border-right: 1px solid var(--line);
      min-height: 72px;
    }}

    .kitchen-column-cell:last-child {{
      border-right: 0;
    }}

    .kitchen-column-label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.02em;
      color: var(--muted);
      font-weight: 900;
      margin-bottom: 6px;
    }}

    .kitchen-cell-empty {{
      color: var(--muted);
    }}

    .banquet-card, .kitchen-column {{
      border: 1px solid var(--line);
      background: var(--field);
      min-width: 0;
    }}

    .role-card {{
      border-radius: 16px;
      background: #ffffff;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
      overflow: hidden;
    }}

    .kitchen-card {{
      overflow: visible;
    }}

    .role-card.animator-card {{
      position: relative;
      overflow: visible;
      z-index: 1;
    }}

    .role-card.animator-card:has(.animator-assign__picker[open]) {{
      z-index: 14000;
    }}

    .role-card-head {{
      display: grid;
      gap: 8px;
      padding: 16px;
      border-bottom: 1px solid var(--line);
      background:
        linear-gradient(135deg, rgba(19, 155, 215, 0.11), rgba(255, 255, 255, 0) 52%),
        #ffffff;
    }}

    .role-card-kicker {{
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 900;
      letter-spacing: 0.03em;
      line-height: 1;
      text-transform: uppercase;
    }}

    .role-card-head .profile-identity {{
      gap: 10px;
    }}

    .role-card-head .profile-name {{
      font-size: 1.28rem;
      font-weight: 900;
    }}

    .role-guest-summary {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      max-width: 100%;
      padding: 6px 10px;
      border: 1px solid rgba(122, 154, 18, 0.24);
      border-radius: 999px;
      background: rgba(122, 154, 18, 0.1);
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 800;
      line-height: 1.25;
      white-space: normal;
    }}

    .role-guest-summary strong {{
      color: var(--ink);
      margin-left: 4px;
    }}

    .banquet-title, .kitchen-title {{
      margin: 0;
      padding: 0;
      border-bottom: 1px solid var(--line);
      background: #f0f0f0;
      color: var(--ink);
    }}

    .role-card .banquet-title {{
      background:
        linear-gradient(135deg, rgba(19, 155, 215, 0.1), rgba(255, 255, 255, 0) 52%),
        #ffffff;
    }}

    .role-card .banquet-header {{
      gap: 10px;
      padding: 14px;
    }}

    .role-card .banquet-header-item {{
      padding: 9px 10px;
      border-radius: 10px;
      background: #f7f7f7;
    }}

    .banquet-header {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px 18px;
      padding: 12px 14px;
    }}

    .banquet-header-item {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}

    .banquet-header-label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      color: var(--muted);
      font-weight: 900;
    }}

    .banquet-header-value {{
      font-size: 0.95rem;
      font-weight: 900;
      line-height: 1.3;
      color: var(--ink);
      word-break: break-word;
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

    .role-card .banquet-task {{
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 12px;
      padding: 12px 14px;
      background: #ffffff;
    }}

    .role-card .banquet-task:nth-child(even) {{
      background: #fbfbfb;
    }}

    .task-time {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 58px;
      min-height: 34px;
      padding: 5px 9px;
      border-radius: 999px;
      background: #000000;
      color: #ffffff;
      font-size: 0.95rem;
      font-weight: 900;
      font-variant-numeric: tabular-nums;
      line-height: 1;
    }}

    .task-detail {{
      min-width: 0;
    }}

    .task-label {{
      color: var(--ink);
      font-size: 0.96rem;
      font-weight: 900;
      line-height: 1.3;
      word-break: break-word;
    }}

    .task-meta {{
      margin-top: 2px;
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 700;
      line-height: 1.35;
      word-break: break-word;
    }}

    .kitchen-task--cake .task-detail {{
      width: 100%;
    }}

    .kitchen-cake-fold {{
      margin-top: 8px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fbfbfb;
      overflow: hidden;
    }}

    .kitchen-cake-fold-summary {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-height: 42px;
      padding: 10px 12px;
      cursor: pointer;
      list-style: none;
      user-select: none;
      -webkit-tap-highlight-color: transparent;
    }}

    .kitchen-cake-fold-summary::-webkit-details-marker {{
      display: none;
    }}

    .kitchen-cake-fold-summary::after {{
      content: "+";
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 1.1rem;
      font-weight: 900;
      line-height: 1;
    }}

    .kitchen-cake-fold[open] > .kitchen-cake-fold-summary {{
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}

    .kitchen-cake-fold[open] > .kitchen-cake-fold-summary::after {{
      content: "−";
    }}

    .kitchen-cake-fold-title {{
      color: var(--brand-dark);
      font-size: 0.86rem;
      font-weight: 900;
      white-space: nowrap;
    }}

    .kitchen-cake-fold-theme {{
      min-width: 0;
      margin-left: auto;
      color: var(--ink);
      font-size: 0.88rem;
      font-weight: 800;
      text-align: right;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .kitchen-cake-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) max-content;
      gap: 12px 14px;
      margin: 0;
      padding: 12px;
      align-items: start;
      background: #ffffff;
    }}

    @media (max-width: 640px) {{
      .kitchen-card {{
        overflow: hidden;
        min-width: 0;
      }}

      .role-card .kitchen-task--cake {{
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        min-width: 0;
      }}

      .role-card .kitchen-task--cake .task-detail {{
        display: contents;
      }}

      .role-card .kitchen-task--cake .task-label {{
        min-width: 0;
      }}

      .role-card .kitchen-task--cake .kitchen-cake-fold {{
        grid-column: 1 / -1;
        width: 100%;
        max-width: 100%;
        min-width: 0;
        margin-top: 10px;
      }}

      .kitchen-cake-layout {{
        grid-template-columns: minmax(0, 1fr);
        width: 100%;
        max-width: 100%;
        min-width: 0;
        box-sizing: border-box;
      }}

      .kitchen-cake-specs,
      .kitchen-cake-photo-col {{
        width: 100%;
        max-width: 100%;
        min-width: 0;
      }}

      .kitchen-cake-photo {{
        display: block;
        width: 100%;
        max-width: 100%;
        height: auto;
      }}
    }}

    .kitchen-cake-specs {{
      margin: 0;
      display: grid;
      gap: 7px;
      align-content: start;
    }}

    .kitchen-cake-spec {{
      display: grid;
      grid-template-columns: minmax(5.5rem, auto) minmax(0, 1fr);
      gap: 4px 10px;
      align-items: baseline;
      padding: 6px 8px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #ffffff;
    }}

    .kitchen-cake-spec dt {{
      margin: 0;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 900;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      line-height: 1.25;
    }}

    .kitchen-cake-spec dd {{
      margin: 0;
      color: var(--ink);
      font-size: 0.92rem;
      font-weight: 800;
      line-height: 1.3;
      word-break: break-word;
    }}

    .kitchen-cake-photo-col {{
      margin: 0;
      padding: 0;
      border: 0;
      background: transparent;
      overflow: visible;
      line-height: 0;
    }}

    .kitchen-cake-photo {{
      display: block;
      width: auto;
      height: auto;
      max-width: none;
      max-height: none;
      object-fit: unset;
      border: 0;
      background: transparent;
    }}

    .kitchen-cake-photo-empty {{
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 800;
      text-align: left;
      padding: 0;
      line-height: 1.3;
    }}

    .role-card .kitchen-task--cake {{
      align-items: start;
    }}

    .role-extra {{
      border-top: 1px solid var(--line);
      background: #fbfbfb;
    }}

    .role-extra > summary {{
      min-height: 42px;
      padding: 11px 14px;
      color: var(--brand-dark);
      cursor: pointer;
      font-size: 0.86rem;
      font-weight: 900;
      list-style: none;
      user-select: none;
    }}

    .role-extra > summary::-webkit-details-marker {{
      display: none;
    }}

    .role-extra > summary::after {{
      content: "+";
      float: right;
      color: var(--muted);
      font-weight: 900;
    }}

    .role-extra[open] > summary {{
      border-bottom: 1px solid var(--line);
    }}

    .role-extra[open] > summary::after {{
      content: "−";
    }}

    .role-extra-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 12px 14px 14px;
    }}

    .role-extra-item {{
      display: grid;
      gap: 3px;
      min-width: 0;
      padding: 9px 10px;
      border-radius: 10px;
      background: #ffffff;
      border: 1px solid #eeeeee;
    }}

    .role-extra-item-full {{
      grid-column: 1 / -1;
    }}

    .role-extra-label {{
      color: var(--muted);
      font-size: 0.7rem;
      font-weight: 900;
      letter-spacing: 0.03em;
      line-height: 1.2;
      text-transform: uppercase;
    }}

    .role-extra-value {{
      color: var(--ink);
      font-size: 0.88rem;
      font-weight: 800;
      line-height: 1.35;
      word-break: break-word;
    }}

    .role-card .banquet-notes {{
      border-bottom: 1px solid var(--line);
      background: #fff7ed;
      color: #92400e;
      font-weight: 700;
    }}

    .role-card .kitchen-columns {{
      border-top: 0;
    }}

    .role-card .kitchen-column-cell {{
      display: grid;
      align-content: start;
      gap: 5px;
      min-height: 96px;
      padding: 14px;
      font-weight: 800;
      line-height: 1.35;
      background: #ffffff;
    }}

    .role-card .kitchen-column-cell:nth-child(1) {{
      background: linear-gradient(180deg, rgba(122, 154, 18, 0.1), rgba(255, 255, 255, 0) 72%);
    }}

    .role-card .kitchen-column-cell:nth-child(2) {{
      background: linear-gradient(180deg, rgba(245, 130, 18, 0.12), rgba(255, 255, 255, 0) 72%);
    }}

    .role-card .kitchen-column-cell:nth-child(3) {{
      background: linear-gradient(180deg, rgba(19, 155, 215, 0.1), rgba(255, 255, 255, 0) 72%);
    }}

    .role-card .kitchen-column-label {{
      width: fit-content;
      margin-bottom: 4px;
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.06);
      color: var(--ink);
      font-size: 0.7rem;
      letter-spacing: 0;
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
      color: var(--ink);
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
      padding: 0;
      width: 100%;
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

    .plan-legend-compact {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      font-size: 0.72rem;
      font-weight: 700;
      color: var(--muted);
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

    .plan-block .room-plan {{
      border: 0;
      background: #ffffff;
    }}

    @media (min-width: 641px) {{
      .plan-block-title[data-open-plan-fs] {{
        display: none;
      }}
    }}

    .room-plan {{
      width: 100%;
      height: auto;
      aspect-ratio: 1378 / 554;
      border: 1px solid var(--line);
      background: #ffffff;
      display: block;
    }}

    .plan-canvas {{
      fill: #ffffff;
    }}

    .plan-base {{
      pointer-events: none;
    }}

    .plan-node {{
      cursor: pointer;
      pointer-events: all;
    }}

    .plan-field {{
      fill: transparent;
      stroke: transparent;
      stroke-width: 2.4;
      pointer-events: all;
      transition: fill 0.15s ease, stroke 0.15s ease, stroke-width 0.15s ease;
    }}

    .plan-label {{
      fill: transparent;
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      font-weight: 800;
      text-anchor: middle;
      dominant-baseline: central;
      pointer-events: none;
      user-select: none;
      transition: fill 0.15s ease;
    }}

    .plan-node.is-busy .plan-field,
    .plan-node.is-selected .plan-field {{
      fill: #ffffff;
      stroke: var(--node-color, var(--busy));
      stroke-width: 3.2;
    }}

    .plan-node.is-selected .plan-field {{
      stroke: var(--brand);
    }}

    .plan-node.is-busy.is-selected .plan-field {{
      stroke: var(--node-color, var(--brand));
      stroke-width: 3.6;
    }}

    .plan-node.is-busy .plan-label {{
      fill: var(--node-color, var(--busy));
    }}

    .plan-node.is-selected .plan-label {{
      fill: var(--brand);
    }}

    .plan-node.is-busy.is-selected .plan-label {{
      fill: var(--node-color, var(--brand));
    }}

    .plan-fs {{
      position: fixed;
      inset: 0;
      z-index: 12000;
      display: none;
      background: #ffffff;
    }}

    .plan-fs.is-open {{
      display: block;
    }}

    body.plan-fs-open {{
      overflow: hidden;
    }}

    .plan-fs-close {{
      position: absolute;
      top: max(10px, env(safe-area-inset-top, 0px));
      right: max(10px, env(safe-area-inset-right, 0px));
      z-index: 4;
      width: 44px;
      height: 44px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      color: var(--ink);
      font-size: 1.4rem;
      font-weight: 700;
      line-height: 1;
      cursor: pointer;
    }}

    .plan-fs-stage {{
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      box-sizing: border-box;
      padding: 12px;
      transform-origin: center center;
    }}

    .plan-fs-stage .room-plan {{
      width: 100%;
      height: 100%;
      max-width: 100%;
      max-height: 100%;
      aspect-ratio: auto;
      border: 0;
      background: #ffffff;
    }}

    .plan-fs-placeholder {{
      display: none;
    }}

    .plan-fs.is-open .plan-fs-close {{
      position: fixed;
    }}

    .plan-tip {{
      position: fixed;
      z-index: 35;
      display: none;
      max-width: min(240px, calc(100vw - 24px));
      padding: 8px 12px;
      border-radius: 12px;
      background: #111111;
      color: #ffffff;
      font-size: 0.78rem;
      font-weight: 700;
      line-height: 1.35;
      letter-spacing: 0.01em;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.28);
      pointer-events: none;
      transform: translate(-50%, calc(-100% - 12px));
    }}

    body.plan-fs-open .plan-tip {{
      z-index: 13050;
    }}

    .plan-tip.is-visible {{
      display: block !important;
    }}

    .plan-tip.is-below {{
      transform: translate(-50%, 14px);
    }}

    .plan-tip::after {{
      content: "";
      position: absolute;
      left: 50%;
      top: 100%;
      width: 0;
      height: 0;
      margin-left: -7px;
      border: 7px solid transparent;
      border-top-color: #111111;
    }}

    .plan-tip.is-below::after {{
      top: auto;
      bottom: 100%;
      border-top-color: transparent;
      border-bottom-color: #111111;
    }}

    .plan-tip-name {{
      display: block;
    }}

    .plan-tip-waiter {{
      display: block;
      margin-top: 2px;
      color: rgba(255, 255, 255, 0.78);
      font-weight: 600;
      font-size: 0.72rem;
    }}

    .key-selected {{
      background: #dbeafe;
      border-color: var(--brand);
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

    .ambient-glow {{
      display: none;
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

      .animation-row {{
        grid-template-columns: 1fr;
      }}

      .metrics {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}
    }}

    @media (max-width: 640px) {{
      html {{
        background: #ffffff;
      }}

      body {{
        padding-bottom: calc(88px + env(safe-area-inset-bottom, 0px));
        position: relative;
        isolation: isolate;
      }}

      body > header {{
        z-index: 60;
      }}

      .ambient-glow {{
        display: block;
        position: fixed;
        top: 72px;
        left: -56px;
        right: -56px;
        bottom: -88px;
        z-index: 0;
        pointer-events: none;
        border-radius: 50% 50% 0 0 / 82px 82px 0 0;
        -webkit-mask-image: linear-gradient(0deg, #000 0%, rgba(0, 0, 0, 0.74) 56%, rgba(0, 0, 0, 0) 100%);
        mask-image: linear-gradient(0deg, #000 0%, rgba(0, 0, 0, 0.74) 56%, rgba(0, 0, 0, 0) 100%);
        overflow: hidden;
        contain: paint;
        filter: saturate(1.18);
        transform: translateZ(0);
      }}

      .ambient-glow-layer {{
        position: absolute;
        inset: 0;
        border-radius: inherit;
        background:
          linear-gradient(0deg, color-mix(in srgb, white 72%, var(--menu-glow)) 0%, color-mix(in srgb, var(--menu-glow) 66%, transparent) 12%, color-mix(in srgb, var(--menu-glow) 42%, transparent) 36%, color-mix(in srgb, var(--menu-glow) 22%, transparent) 70%, color-mix(in srgb, var(--menu-glow) 0%, transparent) 100%),
          radial-gradient(124% 124% at 50% 18%, color-mix(in srgb, var(--menu-glow) 92%, white) 0%, color-mix(in srgb, var(--menu-glow) 72%, transparent) 26%, color-mix(in srgb, var(--menu-glow) 28%, transparent) 62%, color-mix(in srgb, var(--menu-glow) 0%, transparent) 100%),
          var(--menu-glow-noise);
        opacity: 0;
        will-change: opacity;
      }}

      .ambient-glow-blue {{
        --menu-glow: var(--menu-glow-blue);
        animation: ambient-blue-cycle 18s linear infinite;
      }}

      .ambient-glow-orange {{
        --menu-glow: var(--menu-glow-orange);
        animation: ambient-orange-cycle 18s linear infinite;
      }}

      .ambient-glow-lime {{
        --menu-glow: var(--menu-glow-lime);
        animation: ambient-lime-cycle 18s linear infinite;
      }}

      body::after {{
        content: "";
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        height: env(safe-area-inset-bottom, 0px);
        background: #ffffff;
        pointer-events: none;
        z-index: 39;
      }}

      .topbar, main {{
        padding-left: 14px;
        padding-right: 14px;
      }}

      main {{
        padding-top: 8px;
        padding-bottom: calc(108px + env(safe-area-inset-bottom, 0px));
        overflow-x: hidden;
        z-index: 1;
      }}

      body.page-home-landing main {{
        padding-bottom: max(24px, env(safe-area-inset-bottom, 0px));
      }}

      .topbar {{
        align-items: center;
        min-height: 56px;
        overflow: visible;
      }}

      .install-button {{
        top: 4px;
        right: 6px;
        padding: 5px;
      }}

      .install-button__icon {{
        width: 14px;
        height: 14px;
        flex-basis: 14px;
      }}

      .hub-choice {{
        grid-template-columns: 1fr;
        gap: 12px;
        margin-top: 18px;
      }}

      .inventory-line-row,
      .inventory-add-form {{
        grid-template-columns: 1fr;
      }}

      .inventory-page {{
        gap: 12px;
      }}

      .inventory-jump {{
        display: flex;
        border-radius: 0 0 16px 16px;
      }}

      .inventory-board--intro > .section-head {{
        border-radius: 16px 16px 0 0;
        border-bottom: 0;
        align-items: stretch;
      }}

      .inventory-board--intro > .section-head .button {{
        width: 100%;
      }}

      .inventory-section-actions {{
        width: 100%;
        justify-content: stretch;
      }}

      .inventory-section-actions .button {{
        flex: 1 1 auto;
      }}

      .inventory-board[id] {{
        scroll-margin-top: 12px;
      }}

      .inventory-card {{
        padding: 12px;
        border-radius: 12px;
        gap: 6px;
      }}

      .inventory-card__head {{
        align-items: center;
      }}

      .inventory-card__qty {{
        min-width: 2.15rem;
        min-height: 2.15rem;
        font-size: 0.98rem;
      }}

      .inventory-body {{
        padding: 12px;
        border-radius: 0 0 16px 16px;
        gap: 10px;
      }}

      .inventory-card__title {{
        font-size: 1rem;
      }}

      .inventory-add-block {{
        padding: 12px;
        border-radius: 12px;
      }}

      .inventory-add-form .inventory-add-submit,
      .inventory-actions button,
      .inventory-actions .button {{
        width: 100%;
        justify-self: stretch;
      }}

      .inventory-actions {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 8px;
      }}

      .inventory-edit-form {{
        grid-template-columns: 1fr;
      }}

      .hub-choice__btn {{
        min-height: 118px;
        padding: 22px 14px;
        gap: 12px;
        border-radius: 16px;
      }}

      .hub-choice__icon {{
        width: 36px;
        height: 36px;
      }}

      .date-month-label {{
        padding: 4px 8px;
        font-size: 0.72rem;
      }}

      .date-day {{
        min-height: 46px;
        padding: 5px 2px;
        border-radius: 16px;
      }}

      .date-day-name {{
        font-size: 0.72rem;
      }}

      .date-day-number {{
        font-size: 1rem;
      }}

      .date-toolbar {{
        position: relative;
        z-index: 45;
        isolation: isolate;
        display: block;
        padding: 0;
      }}

      .date-week-jump {{
        display: none;
      }}

      .date-strip {{
        position: relative;
        z-index: 1;
      }}

      .date-day {{
        z-index: 1;
      }}

      .tabs {{
        --tab-bar-height: 62px;
        --tab-icon-row: 30px;
        --tab-label-row: 22px;
        --tab-oval-rise: 26px;
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        transform: none;
        z-index: 40;
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 2px;
        width: 100%;
        max-width: 100vw;
        padding: 10px 8px max(10px, env(safe-area-inset-bottom, 0px));
        border: 0;
        border-radius: 50% 50% 0 0 / var(--tab-oval-rise) var(--tab-oval-rise) 0 0;
        box-shadow: none;
        overflow: visible;
        isolation: isolate;
        background: transparent;
        -webkit-touch-callout: none;
      }}

      .tabs::before {{
        content: none;
      }}

      .tabs::after {{
        content: "";
        position: absolute;
        inset: 0;
        z-index: 1;
        pointer-events: none;
        border-radius: inherit;
        background: #ffffff;
      }}

      .tab {{
        height: var(--tab-bar-height);
        min-height: var(--tab-bar-height);
        max-height: var(--tab-bar-height);
        position: relative;
        z-index: 2;
        padding: 0 1px;
        border: 0;
        border-radius: 0;
        background: transparent;
        color: #000000;
        font-size: 0.62rem;
        line-height: 1.05;
        font-weight: 760;
        overflow-wrap: anywhere;
        display: grid;
        grid-template-rows: var(--tab-icon-row) var(--tab-label-row);
        align-content: center;
        justify-items: center;
        gap: 2px;
      }}

      .tab-home {{
        position: relative;
        z-index: 2;
        display: grid;
        grid-template-rows: var(--tab-icon-row) var(--tab-label-row);
        align-content: center;
        justify-items: center;
        height: var(--tab-bar-height);
        min-height: var(--tab-bar-height);
        max-height: var(--tab-bar-height);
        padding: 0 1px;
        border: 0;
        border-radius: 0;
        background: transparent;
      }}

      .tab-home[aria-current="page"] {{
        background: transparent;
        border: 0;
      }}

      .tab-home-logo {{
        grid-row: 1 / span 2;
        align-self: center;
        width: 36px;
        height: 36px;
        object-fit: contain;
        transform: none;
        box-shadow: none;
        user-select: none;
        -webkit-user-drag: none;
      }}

      .tab-icon {{
        grid-row: 1;
        align-self: center;
        width: 28px;
        height: 28px;
        border-radius: 999px;
        padding: 5px;
        color: currentColor;
        flex: 0 0 auto;
      }}

      .tab-label {{
        grid-row: 2;
        align-self: center;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        min-height: var(--tab-label-row);
        max-height: var(--tab-label-row);
        max-width: 100%;
        white-space: normal;
        overflow: hidden;
        text-overflow: ellipsis;
        text-align: center;
        line-height: 1.05;
      }}

      .tab-label-full {{
        display: none;
      }}

      .tab-label-mobile {{
        display: inline;
      }}

      .tab[aria-current="page"] {{
        background: transparent;
        border: 0;
        color: #000000;
      }}

      .tab[aria-current="page"] .tab-icon {{
        background: #000000;
        color: #ffffff;
      }}

      .brand {{
        height: 32px;
      }}

      .logo {{
        --logo-scale: 2.45;
        height: 32px;
        max-width: 96px;
      }}

      .grid, .choice-grid, .form-board, .category-fields, .category-fields.services, .category-fields.termin, .service-catalog, .reservation-details, .birthday-child-row, .kitchen-columns, .banquet-header, .location-forms {{
        grid-template-columns: 1fr;
      }}

      .category-fields.termin {{
        row-gap: 18px;
      }}

      .role-board {{
        border-radius: 16px;
      }}

      .role-board .section-head {{
        border-radius: 16px 16px 0 0;
        padding: 16px;
      }}

      .role-board .banquet-grid {{
        grid-template-columns: 1fr;
        padding: 12px;
        border-radius: 0 0 16px 16px;
      }}

      .role-card {{
        border-radius: 14px;
      }}

      .role-card .banquet-header {{
        gap: 8px;
        padding: 12px;
      }}

      .role-card-head {{
        padding: 14px;
      }}

      .role-extra-grid {{
        grid-template-columns: 1fr;
      }}

      .role-card .kitchen-column-cell {{
        min-height: 74px;
      }}

      .metrics {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 12px;
      }}

      .metric {{
        gap: 4px;
        padding: 8px 6px;
        border-radius: 12px;
      }}

      .metric-icon {{
        width: 22px;
        height: 22px;
      }}

      .metric-icon svg {{
        width: 22px;
        height: 22px;
      }}

      .metric strong {{
        font-size: 1.05rem;
        margin-bottom: 2px;
      }}

      .metric .muted {{
        font-size: 0.68rem;
        line-height: 1.15;
      }}

      .location-panel {{
        min-height: auto;
      }}

      .location-range-row {{
        grid-template-columns: 1fr 1fr;
      }}

      .location-range-apply {{
        grid-column: 1 / -1;
      }}

      .location-chips-loft {{
        grid-template-columns: 1fr;
      }}

      .kitchen-column-cell {{
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}

      .kitchen-column-cell:last-child {{
        border-bottom: 0;
      }}

      .timeline-card {{
        padding: 18px 16px;
        border-radius: 14px;
      }}

      .timeline-header {{
        gap: 12px;
        justify-content: space-between;
        align-items: flex-start;
      }}

      .timeline-start {{
        order: 2;
        margin-left: auto;
        font-size: 1.75rem;
        min-width: 4.25rem;
        text-align: right;
      }}

      .timeline-header .profile-identity {{
        order: 1;
        flex: 1 1 auto;
      }}

      .timeline-header .animator-assign--waiter,
      .timeline-header .timeline-status {{
        order: 3;
      }}

      .timeline-header .animator-assign--waiter {{
        width: 100%;
        flex-basis: 100%;
        flex-wrap: wrap;
        justify-content: flex-start;
        margin-left: 0;
      }}

      .waiter-picker > summary, .waiter-remove-btn {{
        width: 100%;
      }}

      .waiter-options {{
        position: static;
        min-width: 0;
        width: 100%;
        margin-top: 8px;
        box-shadow: none;
      }}

      .timeline-header .profile-guardian {{
        order: 4;
        margin-left: 0;
        width: 100%;
      }}

      .guest-count-block {{
        grid-column: 1 / -1;
      }}

      .guest-count-input {{
        width: 4.75rem;
      }}

      .organizer-day-list {{
        padding: 12px;
      }}

      .organizer-tools-board .section-head {{
        flex-direction: column;
        align-items: stretch;
      }}

      .timeline-logistics {{
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
<body{body_class}>
  <div class="ambient-glow" aria-hidden="true">
    <span class="ambient-glow-layer ambient-glow-blue"></span>
    <span class="ambient-glow-layer ambient-glow-orange"></span>
    <span class="ambient-glow-layer ambient-glow-lime"></span>
  </div>
  <header>
    <div class="topbar">
      <div class="brand">
        {brand_markup}
      </div>
      <button class="install-button" type="button" data-install-app aria-label="Pobierz aplikację" title="Pobierz">
        <svg class="install-button__icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M11 3a1 1 0 0 1 2 0v8.586l2.293-2.293a1 1 0 0 1 1.414 1.414l-4 4a1 1 0 0 1-1.414 0l-4-4a1 1 0 0 1 1.414-1.414L11 11.586V3z"/>
          <path d="M5 18a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1z"/>
        </svg>
      </button>
    </div>
  </header>
  <div class="install-popup" data-install-popup hidden>
    <div class="install-popup__card" role="dialog" aria-modal="true" aria-labelledby="install-popup-title">
      <img class="install-popup__icon" src="/app-icon-192-solid.png?v={pwa_icon_version()}" alt="">
      <p class="install-popup__title" id="install-popup-title">Dodaj do telefonu</p>
      <p class="install-popup__step" data-install-step></p>
      <button type="button" class="install-popup__close" data-install-popup-close>OK</button>
    </div>
  </div>
  <main>
    {alert}
    {content}
  </main>
  <script>
    window.IKIDS_CONTEXT = {json.dumps({"role": role, "day": day}, ensure_ascii=False)};
  </script>
  {pwa_install_script()}
  {date_navigation_script()}
  {fast_navigation_script()}
  {plan_tip_script()}
  {plan_fullscreen_script()}
</body>
</html>"""
    return document.encode("utf-8")


def plan_tip_script() -> str:
    return """
<script>
(() => {
  let tipTimer = null;
  let activeNode = null;
  const planTip = document.createElement("div");
  planTip.className = "plan-tip";
  planTip.setAttribute("role", "status");
  document.body.appendChild(planTip);

  function hidePlanTip() {
    activeNode = null;
    planTip.classList.remove("is-visible", "is-below");
    planTip.replaceChildren();
    window.clearTimeout(tipTimer);
  }

  function findPlanNode(event) {
    const path = typeof event.composedPath === "function" ? event.composedPath() : [];
    for (const el of path) {
      if (el?.classList?.contains?.("plan-node")) return el;
    }
    return event.target?.closest?.(".plan-node") || null;
  }

  function placePlanTip(node) {
    if (!node || !planTip.classList.contains("is-visible")) return;
    const rect = node.getBoundingClientRect();
    const offscreen =
      rect.bottom < 0 ||
      rect.top > window.innerHeight ||
      rect.right < 0 ||
      rect.left > window.innerWidth;
    if (offscreen) {
      hidePlanTip();
      return;
    }
    const tipWidth = Math.max(planTip.offsetWidth || 170, 130);
    const left = Math.max(
      12 + tipWidth / 2,
      Math.min(window.innerWidth - 12 - tipWidth / 2, rect.left + rect.width / 2)
    );
    const showBelow = rect.top < 56;
    planTip.style.left = `${left}px`;
    planTip.style.top = `${showBelow ? rect.bottom : rect.top}px`;
    planTip.style.transform = "";
    planTip.classList.toggle("is-below", showBelow);
  }

  function showPlanTip(node) {
    const tip = (node.getAttribute("data-tip") || node.dataset.tip || "").trim();
    if (!tip) return false;
    const parts = tip.split(" · ");
    planTip.replaceChildren();
    const nameEl = document.createElement("span");
    nameEl.className = "plan-tip-name";
    nameEl.textContent = parts[0] || tip;
    planTip.appendChild(nameEl);
    if (parts.length > 1) {
      const waiterEl = document.createElement("span");
      waiterEl.className = "plan-tip-waiter";
      waiterEl.textContent = parts.slice(1).join(" · ");
      planTip.appendChild(waiterEl);
    }
    activeNode = node;
    planTip.classList.add("is-visible");
    placePlanTip(node);
    window.clearTimeout(tipTimer);
    tipTimer = window.setTimeout(hidePlanTip, 4000);
    return true;
  }

  window.IKIDS_SHOW_PLAN_TIP = (node, event) => {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    showPlanTip(node);
  };
  window.IKIDS_HIDE_PLAN_TIP = hidePlanTip;

  window.addEventListener("scroll", () => placePlanTip(activeNode), true);
  window.addEventListener("resize", () => placePlanTip(activeNode));

  function handleBusyPlanNode(event) {
    const node = findPlanNode(event);
    if (!node || !node.classList.contains("is-busy")) return false;
    event.preventDefault();
    event.stopPropagation();
    showPlanTip(node);
    return true;
  }

  function initPlanTip() {
    document.querySelectorAll(".room-plan").forEach((svg) => {
      if (svg.dataset.planTipReady === "true") return;
      svg.dataset.planTipReady = "true";
      svg.addEventListener("pointerdown", handleBusyPlanNode, true);
      svg.addEventListener("click", handleBusyPlanNode, true);
      svg.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        handleBusyPlanNode(event);
      }, true);
    });
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-open-plan-fs], .plan-fs-close")) return;
    const node = findPlanNode(event);
    if (handleBusyPlanNode(event)) return;
    if (!node && !event.target.closest?.(".plan-tip")) hidePlanTip();
  }, true);

  window.IKIDSInitPlanTip = initPlanTip;
  initPlanTip();
})();
</script>
"""


def plan_fullscreen_script() -> str:
    return """
<script>
(() => {
  const openBtn = document.querySelector("[data-open-plan-fs]");
  const svg = document.querySelector(".room-plan");
  if (!openBtn || !svg) return;

  const homeParent = svg.parentElement;
  if (!homeParent) return;

  const overlay = document.createElement("div");
  overlay.className = "plan-fs";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Plan sali na pełnym ekranie");
  overlay.innerHTML = `
    <button type="button" class="plan-fs-close" aria-label="Zamknij plan">×</button>
    <div class="plan-fs-stage"></div>
  `;
  document.body.appendChild(overlay);

  const stage = overlay.querySelector(".plan-fs-stage");
  const closeBtn = overlay.querySelector(".plan-fs-close");
  const placeholder = document.createElement("div");
  placeholder.className = "plan-fs-placeholder";
  placeholder.setAttribute("aria-hidden", "true");

  function hideTip() {
    if (typeof window.IKIDS_HIDE_PLAN_TIP === "function") window.IKIDS_HIDE_PLAN_TIP();
  }

  function isOpen() {
    return overlay.classList.contains("is-open");
  }

  function isPhoneLike() {
    return Math.min(window.screen.width, window.screen.height) <= 920;
  }

  function deviceAngle() {
    if (screen.orientation && typeof screen.orientation.angle === "number") {
      return screen.orientation.angle;
    }
    if (typeof window.orientation === "number") return window.orientation;
    return window.innerWidth >= window.innerHeight ? 90 : 0;
  }

  function syncLockedStage() {
    if (!isOpen()) return;
    hideTip();
    if (!isPhoneLike()) {
      stage.style.inset = "0";
      stage.style.top = "";
      stage.style.left = "";
      stage.style.width = "";
      stage.style.height = "";
      stage.style.transform = "";
      stage.style.padding = "12px";
      return;
    }
    // Keep plan in fixed landscape orientation relative to the room,
    // countering the phone/browser rotation.
    const angle = ((deviceAngle() % 360) + 360) % 360;
    const longSide = Math.max(window.innerWidth, window.innerHeight);
    const shortSide = Math.min(window.innerWidth, window.innerHeight);
    stage.style.inset = "auto";
    stage.style.top = "50%";
    stage.style.left = "50%";
    stage.style.width = `${longSide}px`;
    stage.style.height = `${shortSide}px`;
    stage.style.padding = "16px 52px 16px 16px";
    stage.style.transform = `translate(-50%, -50%) rotate(${90 - angle}deg)`;
  }

  function openPlan() {
    if (isOpen()) return;
    hideTip();
    homeParent.insertBefore(placeholder, svg);
    stage.appendChild(svg);
    overlay.classList.add("is-open");
    document.body.classList.add("plan-fs-open");
    openBtn.setAttribute("aria-expanded", "true");
    syncLockedStage();
  }

  function closePlan() {
    if (!isOpen()) return;
    hideTip();
    homeParent.insertBefore(svg, placeholder);
    placeholder.remove();
    overlay.classList.remove("is-open");
    document.body.classList.remove("plan-fs-open");
    openBtn.setAttribute("aria-expanded", "false");
    stage.style.inset = "";
    stage.style.top = "";
    stage.style.left = "";
    stage.style.width = "";
    stage.style.height = "";
    stage.style.transform = "";
    stage.style.padding = "";
  }

  openBtn.setAttribute("aria-expanded", "false");
  openBtn.addEventListener("click", (event) => {
    event.preventDefault();
    openPlan();
  });
  closeBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closePlan();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) {
      event.preventDefault();
      closePlan();
    }
  });
  window.addEventListener("orientationchange", () => {
    window.setTimeout(syncLockedStage, 50);
  });
  window.addEventListener("resize", syncLockedStage);
  if (screen.orientation) {
    screen.orientation.addEventListener("change", () => {
      window.setTimeout(syncLockedStage, 50);
    });
  }
})();
</script>
"""


def render_organizer_tools(role: str, day: str) -> str:
    target_day = selected_day(normalize_day(day))
    report_text = format_organizer_report_text(target_day)
    report_json = json.dumps(report_text, ensure_ascii=False)
    return f"""
<section class="role-board organizer-tools-board">
  <div class="section-head">
    <div>
      <h2>Organizator urodzin</h2>
      <p class="subtitle">Nowa rezerwacja, edycja oraz narzędzia dnia.</p>
    </div>
    <div class="organizer-tools">
      <div class="shift-report-panel organizer-report-panel">
        <button type="button" id="organizer-report-copy" class="shift-report-copy" aria-label="Kopiuj raport organizatora">
          <span class="shift-report-copy__label">Raport</span>
          <span class="shift-report-copy__icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
          </span>
        </button>
      </div>
      <a class="button secondary" href="/export">Eksport CSV</a>
    </div>
  </div>
</section>
<script>
(() => {{
  const button = document.getElementById("organizer-report-copy");
  if (!button) return;
  const reportText = {report_json};
  let feedbackTimer = 0;
  function showCopied() {{
    button.classList.add("is-copied");
    window.clearTimeout(feedbackTimer);
    feedbackTimer = window.setTimeout(() => button.classList.remove("is-copied"), 1600);
  }}
  button.addEventListener("click", async () => {{
    try {{
      await navigator.clipboard.writeText(reportText);
      showCopied();
      return;
    }} catch (_err) {{
      const helper = document.createElement("textarea");
      helper.value = reportText;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.left = "-9999px";
      document.body.appendChild(helper);
      helper.select();
      try {{
        document.execCommand("copy");
        showCopied();
      }} finally {{
        helper.remove();
      }}
    }}
  }});
}})();
</script>
"""


def normalize_hub(hub: str | None) -> str:
    value = str(hub or "").strip().lower()
    return value if value in {"urodziny", "grafiki", "inwentura"} else ""


def hub_home_href(day: str, hub: str = "") -> str:
    day_q = day_query(selected_day(normalize_day(day)))
    params: dict[str, str] = {"role": "home", "day": day_q}
    normalized = normalize_hub(hub)
    if normalized:
        params["hub"] = normalized
    return "/?" + urlencode(params)


def default_grafiki_href(day: str, *, role: str = "home") -> str:
    return schedule_url(
        role=role if role != "home" else "home",
        day=normalize_day(day),
        department="animatorzy",
        month="",
        week="",
        view="week",
    )


def default_urodziny_href(day: str) -> str:
    day_q = day_query(selected_day(normalize_day(day)))
    return link_for("manager", day_q)


def default_inwentura_href(day: str) -> str:
    day_q = day_query(selected_day(normalize_day(day)))
    return "/inwentura?" + urlencode({"day": day_q})


def render_hub_choice(day: str) -> str:
    urodziny_href = default_urodziny_href(day)
    grafiki_href = default_grafiki_href(day)
    inwentura_href = default_inwentura_href(day)
    return f"""
<nav class="hub-choice" aria-label="Wybór sekcji">
  <a class="hub-choice__btn" href="{escape(urodziny_href)}" aria-label="Urodziny">
    <svg class="hub-choice__icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 21v-8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8"/>
      <path d="M4 16s.5-1 2-1 2.5 2 4 2 2.5-2 4-2 2.5 2 4 2 2-1 2-1"/>
      <path d="M2 21h20"/>
      <path d="M7 8v3"/>
      <path d="M12 8v3"/>
      <path d="M17 8v3"/>
      <path d="M7 4h.01"/>
      <path d="M12 4h.01"/>
      <path d="M17 4h.01"/>
    </svg>
    <span class="hub-choice__label">Urodziny</span>
  </a>
  <a class="hub-choice__btn" href="{escape(grafiki_href)}" aria-label="Grafiki">
    <svg class="hub-choice__icon" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="4" width="18" height="18" rx="2"/>
      <path d="M16 2v4M8 2v4M3 10h18"/>
      <path d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/>
    </svg>
    <span class="hub-choice__label">Grafiki</span>
  </a>
  <a class="hub-choice__btn" href="{escape(inwentura_href)}" aria-label="Inwentura">
    <svg class="hub-choice__icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
      <path d="M3.3 7 12 12l8.7-5"/>
      <path d="M12 22V12"/>
    </svg>
    <span class="hub-choice__label">Inwentura</span>
  </a>
</nav>
"""


def render_date_toolbar(page_role: str, day: str, *, hub: str = "") -> str:
    page_role = normalize_page_role(page_role)
    day = normalize_day(day)
    hub = normalize_hub(hub)
    target_day = selected_day(day)
    strip_anchor_day = current_app_date()
    active_week_offset = week_page_offset_for_day(strip_anchor_day, target_day)
    week_pages = calendar_week_pages(strip_anchor_day, include_day=target_day)
    week_blocks = []
    for week_offset, week_days in week_pages:
        week_attrs = [
            f'data-month-label="{escape(week_month_label(week_days))}"',
            f'data-year-label="{week_year_label(week_days)}"',
        ]
        if week_offset == 0:
            week_attrs.append("data-default-week")
        if week_offset == active_week_offset:
            week_attrs.append("data-active-week")
        week_attr_str = f' {" ".join(week_attrs)}' if week_attrs else ""
        day_links = []
        for strip_day in week_days:
            query = day_query(strip_day)
            weekday = WEEKDAY_LABELS[strip_day.weekday()][0].upper()
            is_active = strip_day == target_day
            is_today = strip_day == strip_anchor_day
            day_classes = ["date-day"]
            if is_active:
                day_classes.append("is-active")
            if is_today:
                day_classes.append("is-today")
            day_attrs = []
            if is_active:
                day_attrs.append('aria-current="page"')
                day_attrs.append("data-selected-day")
            if is_today:
                day_attrs.append("data-today-day")
            attrs = f' {" ".join(day_attrs)}' if day_attrs else ""
            href = link_for(page_role, query, hub=hub) if hub else link_for(page_role, query)
            day_links.append(
                f"""
            <a class="{" ".join(day_classes)}" href="{href}"{attrs}>
              <span class="date-day-surface" aria-hidden="true"></span>
              <span class="date-day-name">{escape(weekday)}</span>
              <span class="date-day-number">{escape(strip_day.strftime("%d"))}</span>
            </a>"""
            )
        week_blocks.append(
            f'<div class="date-week"{week_attr_str}>{"".join(day_links)}</div>'
        )

    return f"""
  <div class="date-toolbar">
    <p class="date-month-label" data-date-month-label aria-hidden="true"></p>
    <button class="date-week-jump date-week-jump-prev" type="button" data-date-week-prev aria-label="Poprzedni tydzień">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>
    </button>
    <div class="date-strip" data-date-strip>{"".join(week_blocks)}</div>
    <button class="date-week-jump date-week-jump-next" type="button" data-date-week-next aria-label="Następny tydzień">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>
    </button>
  </div>
"""


def render_role_tabs(page_role: str, day: str, *, hub: str = "") -> str:
    page_role = normalize_page_role(page_role)
    day = normalize_day(day)
    hub = normalize_hub(hub) or ("urodziny" if page_role in ROLE_DEFS else "urodziny")
    target_day = selected_day(day)
    day_q = day_query(target_day)
    role_links_by_key = {}
    mobile_labels = {
        "manager": "Kierownik<br>recepcja",
        "organizer": "Organizator<br>urodzin",
    }
    for key, meta in ROLE_DEFS.items():
        mobile_label = mobile_labels.get(key, escape(meta["label"]))
        tab_content = (
            f'<span class="tab-icon">{ROLE_NAV_ICONS[key]}</span>'
            f'<span class="tab-label"><span class="tab-label-full">{escape(meta["label"])}</span>'
            f'<span class="tab-label-mobile">{mobile_label}</span></span>'
        )
        role_links_by_key[key] = (
            f'<a class="tab" href="{link_for(key, day_q)}" aria-current="page"'
            f' title="{escape(meta["hint"])}">{tab_content}</a>'
            if key == page_role
            else f'<a class="tab" href="{link_for(key, day_q)}" title="{escape(meta["hint"])}">{tab_content}</a>'
        )
    home_current = ' aria-current="page"' if page_role == "home" else ""
    home_link = (
        f'<a class="tab tab-home" href="{escape(hub_home_href(day))}"'
        f'{home_current} aria-label="Strona główna">'
        f'<img class="tab-home-logo" src="{menu_logo_asset_url()}" alt="iKids Park"></a>'
    )
    role_links = [
        role_links_by_key["manager"],
        role_links_by_key["animators"],
        home_link,
        role_links_by_key["kitchen"],
        role_links_by_key["organizer"],
    ]
    return f'<div class="tabs">{"".join(role_links)}</div>'


def render_grafiki_department_tabs(
    *,
    role: str,
    day: str,
    selected_department: str = "",
    selected_month: str = "",
    selected_week: str = "",
    selected_view: str = "week",
    home_href: str = "",
) -> str:
    day = normalize_day(day)
    day_q = day_query(selected_day(day))
    home_url = home_href or hub_home_href(day)
    department_items = [
        ("managerowie", "Menedżerowie", "Menedżerowie", ROLE_NAV_ICONS["manager"]),
        ("animatorzy", "Animatorzy", "Animatorzy", ROLE_NAV_ICONS["animators"]),
        ("kuchnia", "Kuchnia", "Kuchnia", ROLE_NAV_ICONS["kitchen"]),
        ("serwis", "Serwis", "Serwis", ROLE_NAV_ICONS["organizer"]),
    ]

    def department_tab(key: str, label: str, mobile_label: str, icon: str) -> str:
        current = ' aria-current="page"' if key == selected_department else ""
        href = schedule_url(
            role=role if role != "home" else "home",
            day=day,
            department=key,
            month=selected_month,
            week=selected_week,
            view=selected_view or "week",
        )
        return (
            f'<a class="tab schedule-control" href="{escape(href)}"{current} title="{escape(label)}">'
            f'<span class="tab-icon">{icon}</span>'
            f'<span class="tab-label"><span class="tab-label-full">{escape(label)}</span>'
            f'<span class="tab-label-mobile">{escape(mobile_label)}</span></span></a>'
        )

    managers_tab = department_tab(*department_items[0])
    animators_tab = department_tab(*department_items[1])
    kitchen_tab = department_tab(*department_items[2])
    serwis_tab = department_tab(*department_items[3])
    home_tab = (
        f'<a class="tab tab-home" href="{escape(home_url)}" '
        f'aria-label="Strona główna" title="Strona główna">'
        f'<img class="tab-home-logo" src="{menu_logo_asset_url()}" alt="iKids Park"></a>'
    )
    return f"""
    <div class="tabs">
      {serwis_tab}
      {animators_tab}
      {home_tab}
      {kitchen_tab}
      {managers_tab}
    </div>
    """


def render_nav(page_role: str, day: str, *, hub: str = "", show_tabs: bool = True) -> str:
    page_role = normalize_page_role(page_role)
    day = normalize_day(day)
    hub = normalize_hub(hub)
    tabs = ""
    if show_tabs:
        if hub == "grafiki":
            tabs = render_grafiki_department_tabs(role=page_role, day=day, home_href=hub_home_href(day))
        else:
            tabs = render_role_tabs(page_role, day, hub=hub or "urodziny")
    return f"""
<div class="toolbar">
  {render_date_toolbar(page_role, day, hub=hub)}
  {tabs}
</div>
"""


def default_form_values(target_day: date) -> dict[str, object]:
    return {
        "id": "",
        "reservation_date": target_day.isoformat(),
        "party_start_time": "",
        "children_count": "",
        "adults_count": "",
        "guest_total": "",
        "reservation_type": "banquet",
        "parent_name": "",
        "parent_phone": "",
        "birthday_child_name": "",
        "birthday_child_age": "",
        "birthday_children": [{"name": "", "age": ""}],
        "birthday_children_json": "[]",
        "child_location": EMPTY_LOCATION,
        "adult_location": EMPTY_LOCATION,
        "animation_enabled": 0,
        "animation_type": "",
        "animation_at": "",
        "animations": [],
        "animations_json": "[]",
        "cake_enabled": 0,
        "cake_theme": "",
        "cake_weight": "",
        "cake_sponge": "",
        "cake_filling": "",
        "cake_cream": "",
        "cake_image_data": "",
        "cake_candle": "",
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
        "balloons_enabled": 0,
        "balloons_description": "",
        "balloons_at": "",
        "attraction_at": "",
        "notes": "",
        "created_by": "",
        "cooperation_enabled": 0,
        "status": "active",
        "cancellation_reason": "",
        "inventory_lines": [],
    }


def row_to_form_values(row: DbRow) -> dict[str, object]:
    values = dict(row)
    values["reservation_date"] = format_date(row["start_at"])
    values["party_start_time"] = format_time(row["start_at"])
    values["birthday_children"] = birthday_children_from_row(row)
    values["animations"] = animations_for_form(row)
    values["inventory_lines"] = inventory.form_lines_from_reservation(int(row["id"]))
    for field in (
        "animation_at",
        "cake_at",
        "fruit_at",
        "drinks_at",
        "culinary_workshops_at",
        "pinata_at",
        "mascot_at",
        "balloons_at",
        "attraction_at",
    ):
        values[field] = format_time(row[field])
    return values


def render_options(options: list[str], current: object) -> str:
    return "\n".join(
        f'<option value="{escape(option)}"{selected(current, option)}>{escape(option)}</option>' for option in options
    )


def render_labeled_options(labels: dict[str, str], current: object) -> str:
    return "\n".join(
        f'<option value="{escape(value)}"{selected(current, value)}>{escape(label)}</option>'
        for value, label in labels.items()
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


def render_child_location_options(current: object) -> str:
    current_value = normalize_location(current)
    none_selected = " selected" if current_value == EMPTY_LOCATION else ""
    options = f'<option value="{EMPTY_LOCATION}"{none_selected}>{EMPTY_LOCATION}</option>'
    options += render_grouped_options({"Loże tematyczne": PARTY_ROOMS}, current)
    return options


def render_adult_location_options(current: object) -> str:
    return render_grouped_options(ADULT_LOCATION_GROUPS, current)


def render_child_location_chip(room: str, current: object) -> str:
    current_value = normalize_location(current)
    selected_class = " is-selected" if current_value == room else ""
    if room == EMPTY_LOCATION:
        return (
            f'<button type="button" class="location-chip location-chip-loft location-chip-none{selected_class}" '
            f'data-location="{escape(EMPTY_LOCATION)}">{EMPTY_LOCATION}</button>'
        )
    short, theme = room.split(" - ", 1) if " - " in room else (room, "")
    if theme:
        title = short
        subtitle = theme
    else:
        title = room
        subtitle = ""
    subtitle_markup = f'<span class="location-chip-sub">{escape(subtitle)}</span>' if subtitle else ""
    return (
        f'<button type="button" class="location-chip location-chip-loft{selected_class}" data-location="{escape(room)}">'
        f'<span class="location-chip-main">{escape(title)}</span>'
        f"{subtitle_markup}"
        f"</button>"
    )


def render_child_location_picker(current: object) -> str:
    display_value = display_location(current)
    room_chips = render_child_location_chip(EMPTY_LOCATION, current) + "".join(
        render_child_location_chip(room, current) for room in PARTY_ROOMS
    )
    return f"""
              <div class="location-panel location-panel-child">
                <div class="location-panel-header">
                  <span class="location-form-title">Sala dzieci</span>
                  <span class="location-panel-badge" id="child-location-badge">{escape(display_value)}</span>
                </div>
                <div class="location-panel-body">
                  <div class="location-accordion is-open" data-accordion="child-rooms">
                    <button type="button" class="location-accordion-head" aria-expanded="true">
                      <span class="location-accordion-head-main">
                        <span class="location-accordion-label">Loże tematyczne</span>
                        <span class="location-accordion-range">Brak lub 1. Biały Dom – 6. Football</span>
                      </span>
                      <span class="location-accordion-meta">7 opcji</span>
                      <span class="location-accordion-chevron" aria-hidden="true"></span>
                    </button>
                    <div class="location-accordion-panel">
                      <div class="location-accordion-body">
                        <div class="location-chips location-chips-loft">{room_chips}</div>
                      </div>
                    </div>
                  </div>
                </div>
                <select name="child_location" id="child_location" class="location-select-native" tabindex="-1" aria-hidden="true">
                  {render_child_location_options(current)}
                </select>
              </div>
"""


def render_adult_zone_range_row(zone: str, numbers: list[int]) -> str:
    range_label = format_table_range(numbers)
    min_number = min(numbers)
    max_number = max(numbers)
    return f"""
                      <div class="location-range-row" data-zone="{escape(zone)}">
                        <label>
                          Od
                          <input type="number" class="location-range-from" min="{min_number}" max="{max_number}" placeholder="{min_number}">
                        </label>
                        <label>
                          Do
                          <input type="number" class="location-range-to" min="{min_number}" max="{max_number}" placeholder="{max_number}">
                        </label>
                        <button type="button" class="button secondary location-range-apply">Zaznacz {escape(range_label)}</button>
                      </div>
"""


def render_adult_location_picker(current: object) -> str:
    selected = set(location_values(current))
    selected_count = len(selected)
    badge = f"{selected_count} stol." if selected_count else EMPTY_LOCATION
    none_selected = " is-selected" if not selected_count else ""
    accordions = []
    for index, (zone, tables) in enumerate(ADULT_LOCATION_GROUPS.items()):
        numbers = TABLE_GROUP_NUMBERS[zone]
        range_label = format_table_range(numbers)
        zone_selected = sum(1 for table in tables if table in selected)
        table_chips = []
        for table in tables:
            number = int(table.rsplit(" ", 1)[-1])
            selected_class = " is-selected" if table in selected else ""
            table_chips.append(
                f'<button type="button" class="location-chip location-chip-table{selected_class}" '
                f'data-location="{escape(table)}" data-table-number="{number}" title="{escape(table)}">'
                f'<span class="location-chip-main">{number}</span>'
                f"</button>"
            )
        open_class = " is-open" if index == 0 else ""
        expanded = "true" if index == 0 else "false"
        meta = f"{zone_selected}/{len(tables)}" if zone_selected else str(len(tables))
        accordions.append(
            f"""
                <div class="location-accordion{open_class}" data-accordion="adult-{escape(zone)}" data-zone="{escape(zone)}">
                  <button type="button" class="location-accordion-head" aria-expanded="{expanded}">
                    <span class="location-accordion-head-main">
                      <span class="location-accordion-label">{escape(zone)}</span>
                      <span class="location-accordion-range">Stoliki {escape(range_label)}</span>
                    </span>
                    <span class="location-accordion-meta">{escape(meta)}</span>
                    <span class="location-accordion-chevron" aria-hidden="true"></span>
                  </button>
                  <div class="location-accordion-panel">
                    <div class="location-accordion-body">
                      {render_adult_zone_range_row(zone, numbers)}
                      <div class="location-chips location-chips-tables">{''.join(table_chips)}</div>
                    </div>
                  </div>
                </div>
"""
        )
    return f"""
              <div class="location-panel location-panel-adult">
                <div class="location-panel-header">
                  <span class="location-form-title">Stoliki rodziców</span>
                  <span class="location-panel-badge" id="adult-location-badge">{escape(badge)}</span>
                </div>
                <div class="location-panel-body">
                  <div class="location-none-row">
                    <button type="button" class="location-chip location-chip-none location-chip-adult-none{none_selected}" data-location="{escape(EMPTY_LOCATION)}">{EMPTY_LOCATION}</button>
                  </div>
                  <div class="location-accordions">{''.join(accordions)}</div>
                </div>
                <select name="adult_location" id="adult_location" class="location-select-native" multiple tabindex="-1" aria-hidden="true">
                  {render_adult_location_options(current)}
                </select>
              </div>
"""


def render_birthday_children_fields(values: dict[str, object], errors: dict[str, str]) -> str:
    children = values.get("birthday_children")
    if not isinstance(children, list) or not children:
        children = [{"name": values.get("birthday_child_name", ""), "age": values.get("birthday_child_age", "")}]
    rows = []
    for index, child in enumerate(children):
        remove_button = (
            '<button type="button" class="button secondary remove-birthday-child" aria-label="Usuń solenizanta">Usuń</button>'
            if index > 0
            else ""
        )
        rows.append(
            f"""
          <div class="birthday-child-row">
            <label>
              Imię solenizanta
              <input name="birthday_child_name" value="{escape(child.get("name", ""))}" required minlength="2" maxlength="80" pattern="[A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+([ '\\-][A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+)*" title="Tylko litery, spacje, myślnik lub apostrof" autocomplete="off">
            </label>
            <label>
              Wiek
              <input type="number" name="birthday_child_age" min="1" max="18" step="1" value="{escape(child.get("age", ""))}" required title="Wiek od 1 do 18 lat">
            </label>
            {remove_button}
          </div>
            """
        )
    return f"""
        <div class="birthday-children-block full">
          <div class="birthday-children-head">
            <strong>Solenizanci</strong>
            <button type="button" class="button secondary" id="add-birthday-child">+ Dodaj solenizanta</button>
          </div>
          <div id="birthday-children-list">{''.join(rows)}</div>
          {error_for(errors, "birthday_child_name")}
          {error_for(errors, "birthday_child_age")}
        </div>
"""


def reservation_duration_meta(row: DbRow | dict[str, object]) -> tuple[str, str]:
    try:
        start = datetime.fromisoformat(str(row["start_at"]))
        end = datetime.fromisoformat(str(row["end_at"]))
        minutes = max(0, int((end - start).total_seconds() // 60))
        return format_duration(minutes) or "", end.strftime("%H:%M")
    except (ValueError, TypeError):
        return "", ""


def render_note_callout(notes: str, *, tone: str = "warning", label: str = "Uwaga") -> str:
    if not notes:
        return ""
    del label
    icon = (
        '<svg class="logistics-chip-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
        "</svg>"
    )
    return f"""
      <div class="reservation-callout reservation-callout-{escape(tone)}" role="note">
        <span class="reservation-callout-icon" aria-hidden="true">{icon}</span>
        <p class="reservation-callout-text">{escape(notes)}</p>
      </div>
    """


LOGISTICS_CHIP_ICONS = {
    "location": (
        '<svg class="logistics-chip-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/>'
        '<circle cx="12" cy="10" r="3"/>'
        "</svg>"
    ),
    "attraction": (
        '<svg class="logistics-chip-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>'
        '<path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/>'
        "</svg>"
    ),
    "kitchen": (
        '<svg class="logistics-chip-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="M12 20.94c1.5 0 2.75 1.06 4 1.06 3 0 6-8 6-12.22A4.91 4.91 0 0 0 17 5c-2.22 0-4 1.44-5 2-1-.56-2.78-2-5-2a4.9 4.9 0 0 0-5 4.78C2 14 5 22 8 22c1.25 0 2.5-1.06 4-1.06Z"/>'
        '<path d="M10 2c1 .5 2 2 2 5"/>'
        "</svg>"
    ),
    "cake": (
        '<svg class="logistics-chip-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="M20 21v-8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8"/>'
        '<path d="M4 16s.5-1 2-1 2.5 2 4 2 2.5-2 4-2 2.5 2 4 2 2-1 2-1"/>'
        '<path d="M2 21h20"/><path d="M7 8v3"/><path d="M12 8v3"/><path d="M17 8v3"/>'
        '<path d="M7 4h.01"/><path d="M12 4h.01"/><path d="M17 4h.01"/>'
        "</svg>"
    ),
    "pinata": (
        '<svg class="logistics-chip-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<rect x="3" y="8" width="18" height="4" rx="1"/>'
        '<path d="M12 8v13"/>'
        '<path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/>'
        '<path d="M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/>'
        "</svg>"
    ),
}


METRIC_ICONS = {
    "bankiety": (
        '<svg class="metric-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="m16 2-2.3 2.3a3 3 0 0 0 0 4.2l1.8 1.8a3 3 0 0 0 4.2 0L22 8"/>'
        '<path d="M15 15 3.3 3.3a4.2 4.2 0 0 0 0 6l7.3 7.3c.7.7 2 .7 2.8 0L15 15Zm0 0 7 7"/>'
        '<path d="m2.1 21.8 6.4-6.3"/>'
        '<path d="m19 5-7 7"/>'
        "</svg>"
    ),
    "guests": (
        '<svg class="metric-svg" viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
        '<circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
        '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
        "</svg>"
    ),
    "animacje": LOGISTICS_CHIP_ICONS["attraction"].replace("logistics-chip-svg", "metric-svg"),
    "warsztaty": LOGISTICS_CHIP_ICONS["kitchen"].replace("logistics-chip-svg", "metric-svg"),
    "torty": LOGISTICS_CHIP_ICONS["cake"].replace("logistics-chip-svg", "metric-svg"),
    "piniaty": LOGISTICS_CHIP_ICONS["pinata"].replace("logistics-chip-svg", "metric-svg"),
}


def render_metric(value: int, label: str, icon_key: str, href: str = "") -> str:
    icon_class = f" metric-icon-{icon_key}" if icon_key in {"bankiety", "animacje", "warsztaty", "torty", "piniaty"} else ""
    tag = "a" if href else "div"
    href_attr = f' href="{escape(href)}"' if href else ""
    return (
        f'<{tag} class="metric"{href_attr}>'
        f'<span class="metric-icon{icon_class}" aria-hidden="true">{METRIC_ICONS[icon_key]}</span>'
        f"<strong>{value}</strong>"
        f'<span class="muted">{escape(label)}</span>'
        f"</{tag}>"
    )


def render_role_card_identity(
    row: DbRow | dict[str, object],
    *,
    show_guest_summary: bool = True,
    show_children_tag: bool = False,
    show_total_guests_tag: bool = False,
) -> str:
    if is_table_reservation(row):
        kicker = "Rezerwacja stolika"
        name_markup = escape(str(row.get("parent_name") or "gość"))
        age_tags = ""
    else:
        kicker = "Solenizant"
        children = birthday_children_from_row(row)
        names = [escape(str(child["name"])) for child in children if str(child.get("name", "")).strip()]
        name_markup = ", ".join(names) if names else "Brak solenizanta"
        age_tags = "".join(
            f'<span class="profile-tag profile-tag-age">{escape(format_age_label(child.get("age")))}</span>'
            for child in children
            if format_age_label(child.get("age"))
        )
    if show_children_tag and not is_table_reservation(row):
        age_tags += f'<span class="profile-tag profile-tag-guests">{escape(int_row_value(row, "children_count"))} dzieci</span>'
    if show_total_guests_tag:
        total_guests = int_row_value(row, "guest_total") or int_row_value(row, "children_count") + int_row_value(row, "adults_count")
        age_tags += f'<span class="profile-tag profile-tag-guests">{escape(total_guests)} osób</span>'
    if is_enabled(row, "cooperation_enabled"):
        age_tags += '<span class="profile-tag profile-tag-cooperation">Współpraca</span>'
    tags_block = f'<div class="profile-tags">{age_tags}</div>' if age_tags else ""
    guests = escape(guest_count_label(row))
    guest_summary = f'<div class="role-guest-summary">Goście: <strong>{guests}</strong></div>' if show_guest_summary else ""
    return f"""
      <div class="role-card-head">
        <span class="role-card-kicker">{kicker}</span>
        <div class="profile-identity">
          <h3 class="profile-name">{name_markup}</h3>
          {tags_block}
        </div>
        {guest_summary}
      </div>
    """


def render_role_extra_info(row: DbRow, notes: object = "") -> str:
    room = escape(display_location(row["child_location"]))
    adult = escape(display_locations(row["adult_location"]))
    parent = escape(str(row["parent_name"]))
    phone = escape(str(row.get("parent_phone") or ""))
    start = escape(format_time(row["start_at"]))
    children_count = escape(row["children_count"])
    adults_count = escape(row["adults_count"])
    notes_text = str(notes or "").strip()
    notes_markup = (
        f"""
          <div class="role-extra-item role-extra-item-full">
            <span class="role-extra-label">Notatka</span>
            <span class="role-extra-value">{escape(notes_text)}</span>
          </div>
        """
        if notes_text
        else ""
    )
    balloons_markup = ""
    if is_enabled(row, "balloons_enabled"):
        balloons_time = escape(format_time(row["balloons_at"]))
        balloons_description = escape(row["balloons_description"] or "(brak)")
        balloons_value = f"{balloons_description} · {balloons_time}" if balloons_time else balloons_description
        balloons_markup = f"""
          <div class="role-extra-item role-extra-item-full">
            <span class="role-extra-label">Balony</span>
            <span class="role-extra-value">{balloons_value}</span>
          </div>
        """
    return f"""
      <details class="role-extra">
        <summary>Info dodatkowe</summary>
        <div class="role-extra-grid">
          <div class="role-extra-item">
            <span class="role-extra-label">Sala</span>
            <span class="role-extra-value">{room}</span>
          </div>
          <div class="role-extra-item">
            <span class="role-extra-label">Stoliki</span>
            <span class="role-extra-value">{adult}</span>
          </div>
          <div class="role-extra-item">
            <span class="role-extra-label">Start bankietu</span>
            <span class="role-extra-value">{start}</span>
          </div>
          <div class="role-extra-item">
            <span class="role-extra-label">Rodzic</span>
            <span class="role-extra-value">{parent}</span>
          </div>
          <div class="role-extra-item">
            <span class="role-extra-label">Telefon</span>
            <span class="role-extra-value">{phone or EMPTY_LOCATION}</span>
          </div>
          <div class="role-extra-item">
            <span class="role-extra-label">Dzieci</span>
            <span class="role-extra-value">{children_count}</span>
          </div>
          <div class="role-extra-item">
            <span class="role-extra-label">Dorośli</span>
            <span class="role-extra-value">{adults_count}</span>
          </div>
          {balloons_markup}
          {notes_markup}
        </div>
      </details>
    """


def render_logistics_chip(kind: str, primary: str, secondary: str = "") -> str:
    if not primary:
        return ""
    icon = LOGISTICS_CHIP_ICONS.get(kind, LOGISTICS_CHIP_ICONS["kitchen"])
    secondary_markup = f'<span class="logistics-chip-sub">{secondary}</span>' if secondary else ""
    return f"""
      <div class="logistics-chip logistics-chip-{escape(kind)}">
        <span class="logistics-chip-icon" aria-hidden="true">{icon}</span>
        <span class="logistics-chip-content">
          <span class="logistics-chip-text">{primary}</span>
          {secondary_markup}
        </span>
      </div>
    """


def render_logistics_grid(left_chips: list[str], right_chips: list[str]) -> str:
    left_markup = "".join(chip for chip in left_chips if chip)
    right_markup = "".join(chip for chip in right_chips if chip)
    if not left_markup and not right_markup:
        return ""
    left_column = f'<div class="logistics-column">{left_markup}</div>' if left_markup else ""
    right_column = f'<div class="logistics-column">{right_markup}</div>' if right_markup else ""
    return f'<div class="timeline-logistics">{left_column}{right_column}</div>'


ROOM_TAG_THEMES = {
    "Zima": ("❄️", "winter"),
    "Biały Dom": ("🏛️", "white-house"),
    "Magiczny Las": ("🌲", "forest"),
    "Wróżki": ("✨", "fairy"),
    "Kosmos": ("🚀", "space"),
    "Football": ("⚽", "football"),
}

PROFILE_USER_ICON = (
    '<svg class="profile-guardian-svg" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>'
    '<circle cx="12" cy="7" r="4"/>'
    "</svg>"
)


def format_age_label(age: object) -> str:
    try:
        value = int(age)
    except (TypeError, ValueError):
        return ""
    if value == 1:
        return "1 rok"
    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        return f"{value} lata"
    return f"{value} lat"


def render_profile_room_tag(location: object) -> str:
    normalized = normalize_location(location)
    if normalized == EMPTY_LOCATION:
        return ""
    display = escape(display_location(normalized))
    theme_key = normalized.split(". ", 1)[-1] if ". " in normalized else normalized
    emoji, theme_class = ROOM_TAG_THEMES.get(theme_key, ("📍", "default"))
    return (
        f'<span class="profile-tag profile-tag-room profile-tag-room-{theme_class}">'
        f'<span class="profile-tag-icon" aria-hidden="true">{emoji}</span> {display}'
        f"</span>"
    )


def render_waiter_assignment(row: DbRow, role: str, day: str) -> str:
    if not can_assign_waiter(role):
        return ""

    reservation_id = int(row["id"])
    assigned = assigned_waiters_from_row(row)
    action = f"/assign-waiter?role={escape(role)}&day={escape(day)}"
    available_waiters = [name for name in WAITERS if name not in assigned]
    assigned_class = " is-assigned" if assigned else ""
    aria_label = "Dodaj kelnera"

    def option_button(name: str) -> str:
        return f"""
        <form method="post" action="{action}" class="animator-assign__option-form">
          <input type="hidden" name="id" value="{reservation_id}">
          <input type="hidden" name="waiter" value="{escape(name)}">
          <button type="submit" class="animator-assign__option" data-staff-option>
            <span class="animator-assign__avatar" aria-hidden="true">{escape(staff_initials(name))}</span>
            <span class="animator-assign__option-name">{escape(name)}</span>
          </button>
        </form>
        """

    def assigned_chip(name: str) -> str:
        return f"""
        <span class="animator-assign__chip" title="{escape(name)}">
          <span class="animator-assign__chip-initials" aria-hidden="true">{escape(staff_initials(name))}</span>
          <span class="animator-assign__chip-name">{escape(name)}</span>
          <form method="post" action="{action}" class="animator-assign__chip-remove-form">
            <input type="hidden" name="id" value="{reservation_id}">
            <input type="hidden" name="remove_waiter" value="{escape(name)}">
            <button type="submit" class="animator-assign__chip-remove" aria-label="Usuń {escape(name)}" title="Usuń">×</button>
          </form>
        </span>
        """

    options = "".join(option_button(name) for name in available_waiters)
    chips = "".join(assigned_chip(name) for name in assigned)
    empty_options = (
        '<p class="animator-assign__empty">Wszyscy kelnerzy już przypisani</p>'
        if assigned
        else '<p class="animator-assign__empty">Brak kelnerów na liście</p>'
    )
    add_picker = (
        f"""
        <details class="animator-assign__picker" data-staff-picker>
          <summary class="animator-assign__icon-btn" aria-label="{escape(aria_label)}" title="{escape(aria_label)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
              <circle cx="9" cy="7" r="4"/>
              <path d="M19 8v6M22 11h-6"/>
            </svg>
          </summary>
          <div class="animator-assign__sheet">
            <label class="animator-assign__search">
              <span class="visually-hidden">Szukaj kelnera</span>
              <input type="search" placeholder="Szukaj po imieniu…" autocomplete="off" data-staff-filter>
            </label>
            <div class="animator-assign__list" data-staff-list>
              {options or empty_options}
            </div>
          </div>
        </details>
        """
        if available_waiters or not assigned
        else ""
    )

    return f"""
      <div class="animator-assign animator-assign--waiter{assigned_class}" data-staff-assign>
        {chips}
        {add_picker}
      </div>
    """


def render_animator_assignment(row: DbRow, role: str, day: str, slot: str = "anim:0") -> str:
    if not can_assign_animator(role):
        return ""

    normalized_slot = normalize_animator_slot(slot)
    if normalized_slot is None:
        return ""

    reservation_id = int(row["id"])
    assigned = assigned_animators_for_slot(row, normalized_slot)
    action = f"/assign-animator?role={escape(role)}&day={escape(day)}"
    available_animators = [name for name in ANIMATORS if name not in assigned]
    assigned_class = " is-assigned" if assigned else ""
    aria_label = "Dodaj animatora"
    slot_field = f'<input type="hidden" name="slot" value="{escape(normalized_slot)}">'

    def option_button(name: str) -> str:
        return f"""
        <form method="post" action="{action}" class="animator-assign__option-form">
          <input type="hidden" name="id" value="{reservation_id}">
          {slot_field}
          <input type="hidden" name="animator" value="{escape(name)}">
          <button type="submit" class="animator-assign__option" data-staff-option>
            <span class="animator-assign__avatar" aria-hidden="true">{escape(staff_initials(name))}</span>
            <span class="animator-assign__option-name">{escape(name)}</span>
          </button>
        </form>
        """

    def assigned_chip(name: str) -> str:
        return f"""
        <span class="animator-assign__chip" title="{escape(name)}">
          <span class="animator-assign__chip-initials" aria-hidden="true">{escape(staff_initials(name))}</span>
          <span class="animator-assign__chip-name">{escape(name)}</span>
          <form method="post" action="{action}" class="animator-assign__chip-remove-form">
            <input type="hidden" name="id" value="{reservation_id}">
            {slot_field}
            <input type="hidden" name="remove_animator" value="{escape(name)}">
            <button type="submit" class="animator-assign__chip-remove" aria-label="Usuń {escape(name)}" title="Usuń">×</button>
          </form>
        </span>
        """

    options = "".join(option_button(name) for name in available_animators)
    chips = "".join(assigned_chip(name) for name in assigned)
    empty_options = (
        '<p class="animator-assign__empty">Wszyscy animatorzy już przypisani</p>'
        if assigned
        else '<p class="animator-assign__empty">Brak animatorów na liście</p>'
    )
    add_picker = (
        f"""
        <details class="animator-assign__picker" data-staff-picker>
          <summary class="animator-assign__icon-btn" aria-label="{escape(aria_label)}" title="{escape(aria_label)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
              <circle cx="9" cy="7" r="4"/>
              <path d="M19 8v6M22 11h-6"/>
            </svg>
          </summary>
          <div class="animator-assign__sheet">
            <label class="animator-assign__search">
              <span class="visually-hidden">Szukaj animatora</span>
              <input type="search" placeholder="Szukaj po imieniu…" autocomplete="off" data-staff-filter>
            </label>
            <div class="animator-assign__list" data-staff-list>
              {options or empty_options}
            </div>
          </div>
        </details>
        """
        if available_animators or not assigned
        else ""
    )

    return f"""
      <div class="animator-assign{assigned_class}" data-staff-assign>
        {chips}
        {add_picker}
      </div>
    """


def staff_initials(name: str) -> str:
    parts = [part for part in str(name or "").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def staff_assignment_script() -> str:
    return """
<script>
(() => {
  if (window.__IKIDS_STAFF_ASSIGN_BOUND) return;
  window.__IKIDS_STAFF_ASSIGN_BOUND = true;

  function normalize(value) {
    return String(value || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\\u0300-\\u036f]/g, "");
  }

  function ownerRoot(sheet) {
    return sheet?._ownerRoot || null;
  }

  function placeSheet(picker) {
    const sheet = picker.querySelector(".animator-assign__sheet") || picker._portedSheet;
    const trigger = picker.querySelector(".animator-assign__icon-btn");
    const root = picker.closest("[data-staff-assign]");
    if (!sheet || !trigger) return;
    picker._portedSheet = sheet;
    sheet._ownerRoot = root;
    sheet._ownerPicker = picker;
    if (sheet.parentElement !== document.body) {
      document.body.appendChild(sheet);
    }
    sheet.classList.add("is-ported");
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(300, window.innerWidth - 16);
    const maxHeight = Math.min(340, window.innerHeight - 16);
    let left = rect.right - width;
    if (left < 8) left = 8;
    if (left + width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - width - 8);
    }
    let top = rect.bottom + 6;
    if (top + Math.min(maxHeight, 220) > window.innerHeight - 8) {
      top = Math.max(8, rect.top - Math.min(maxHeight, 280) - 6);
    }
    sheet.style.top = top + "px";
    sheet.style.left = left + "px";
    sheet.style.right = "auto";
    sheet.style.width = width + "px";
    sheet.style.maxHeight = maxHeight + "px";
  }

  function restoreSheet(picker) {
    const sheet = picker._portedSheet || picker.querySelector(".animator-assign__sheet");
    if (!sheet) return;
    sheet.classList.remove("is-ported");
    sheet.style.top = "";
    sheet.style.left = "";
    sheet.style.right = "";
    sheet.style.width = "";
    sheet.style.maxHeight = "";
    if (sheet.parentElement !== picker) {
      picker.appendChild(sheet);
    }
  }

  function discardPortedSheet(root) {
    if (!root) return;
    const picker = root.querySelector("[data-staff-picker]");
    const sheet = picker?._portedSheet;
    if (sheet && sheet.parentElement === document.body) {
      sheet.remove();
    }
    document.querySelectorAll(".animator-assign__sheet.is-ported").forEach((node) => {
      if (ownerRoot(node) === root) node.remove();
    });
  }

  function closeOtherPickers(activeRoot) {
    document.querySelectorAll("[data-staff-assign]").forEach((other) => {
      if (other === activeRoot) return;
      other.querySelectorAll("[data-staff-picker][open]").forEach((node) => {
        node.open = false;
      });
    });
  }

  function replaceAssignRoot(root, html) {
    discardPortedSheet(root);
    const wrap = document.createElement("div");
    wrap.innerHTML = String(html || "").trim();
    const next = wrap.firstElementChild;
    if (!next) return null;
    root.replaceWith(next);
    return next;
  }

  document.addEventListener("toggle", (event) => {
    const picker = event.target;
    if (!(picker instanceof HTMLDetailsElement) || !picker.matches("[data-staff-picker]")) return;
    const root = picker.closest("[data-staff-assign]");
    const filter = () =>
      (picker._portedSheet || picker).querySelector("[data-staff-filter]");
    const listRoot = () =>
      (picker._portedSheet || picker).querySelector("[data-staff-list]");
    const options = () =>
      Array.from((picker._portedSheet || picker).querySelectorAll("[data-staff-option]"));

    if (!picker.open) {
      const input = filter();
      if (input) input.value = "";
      options().forEach((btn) => {
        const form = btn.closest(".animator-assign__option-form");
        if (form) form.hidden = false;
      });
      listRoot()?.querySelector("[data-staff-empty-filter]")?.remove();
      restoreSheet(picker);
      return;
    }
    closeOtherPickers(root);
    placeSheet(picker);
    const sheet = picker._portedSheet;
    if (sheet && !sheet.dataset.filterBound) {
      sheet.dataset.filterBound = "1";
      sheet.addEventListener("input", (inputEvent) => {
        const target = inputEvent.target;
        if (!(target instanceof Element) || !target.matches("[data-staff-filter]")) return;
        const query = normalize(target.value.trim());
        let visible = 0;
        options().forEach((btn) => {
          const form = btn.closest(".animator-assign__option-form");
          const name = normalize(btn.textContent);
          const show = !query || name.includes(query);
          if (form) form.hidden = !show;
          if (show) visible += 1;
        });
        const list = listRoot();
        let empty = list?.querySelector("[data-staff-empty-filter]");
        if (visible === 0) {
          if (!empty && list) {
            empty = document.createElement("p");
            empty.className = "animator-assign__empty";
            empty.setAttribute("data-staff-empty-filter", "");
            empty.textContent = "Brak wyników";
            list.appendChild(empty);
          }
        } else {
          empty?.remove();
        }
      });
    }
    window.requestAnimationFrame(() => filter()?.focus());
  }, true);

  function repositionOpenSheets() {
    document.querySelectorAll("[data-staff-picker][open]").forEach((picker) => {
      placeSheet(picker);
    });
  }

  window.addEventListener("resize", repositionOpenSheets);
  window.addEventListener("scroll", repositionOpenSheets, true);

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("[data-staff-assign]") || target.closest(".animator-assign__sheet")) return;
    document.querySelectorAll("[data-staff-picker][open]").forEach((node) => {
      node.open = false;
    });
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches(".animator-assign__option-form, .animator-assign__chip-remove-form")) return;

    const sheet = form.closest(".animator-assign__sheet");
    const root = form.closest("[data-staff-assign]") || ownerRoot(sheet);
    if (!root) return;

    event.preventDefault();
    if (form.dataset.busy === "1") return;
    form.dataset.busy = "1";

    const submitter = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
    if (submitter) submitter.disabled = true;

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new URLSearchParams(new FormData(form)),
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-Requested-With": "ikids-assign",
        },
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !payload || !payload.ok || !payload.html) {
        window.alert((payload && payload.message) || "Nie udało się zaktualizować przypisania.");
        return;
      }
      replaceAssignRoot(root, payload.html);
    } catch {
      window.alert("Nie udało się zaktualizować przypisania.");
    } finally {
      form.dataset.busy = "0";
      if (submitter) submitter.disabled = false;
    }
  });
})();
</script>
"""


def render_profile_identity(row: DbRow | dict[str, object]) -> str:
    if is_table_reservation(row):
        name_markup = f"Rezerwacja: {escape(str(row.get('parent_name') or 'gość'))}"
        age_tags = ""
    else:
        children = birthday_children_from_row(row)
        names = [escape(str(child["name"])) for child in children if str(child.get("name", "")).strip()]
        name_markup = ", ".join(names) if names else "Brak solenizanta"

        age_tags = "".join(
            f'<span class="profile-tag profile-tag-age">{escape(format_age_label(child.get("age")))}</span>'
            for child in children
            if format_age_label(child.get("age"))
        )
    child_location = row["child_location"] if not isinstance(row, dict) else row.get("child_location", "")
    room_tag = render_profile_room_tag(child_location)
    coop_tag = (
        '<span class="profile-tag profile-tag-cooperation">Współpraca</span>'
        if is_enabled(row, "cooperation_enabled")
        else ""
    )
    tags_markup = age_tags + room_tag + coop_tag
    tags_block = f'<div class="profile-tags">{tags_markup}</div>' if tags_markup else ""
    guests_markup = f'<span class="profile-tag profile-tag-guests">{escape(guest_count_label(row))}</span>'
    if tags_block:
        tags_block = tags_block.replace("</div>", f"{guests_markup}</div>", 1)
    else:
        tags_block = f'<div class="profile-tags">{guests_markup}</div>'

    return f"""
      <div class="profile-identity">
        <h3 class="profile-name">{name_markup}</h3>
        {tags_block}
      </div>
    """


def render_profile_guardian(row: DbRow | dict[str, object]) -> str:
    parent_raw = row["parent_name"] if not isinstance(row, dict) else row.get("parent_name", "")
    parent = escape(str(parent_raw))
    phone = escape(str(row.get("parent_phone") or ""))
    if not parent:
        return ""
    phone_markup = f'<span class="profile-phone">{phone}</span>' if phone else ""
    return f'<div class="profile-guardian">{PROFILE_USER_ICON}<span>{parent}</span>{phone_markup}</div>'


def render_profile_header(row: DbRow | dict[str, object]) -> str:
    return render_profile_identity(row) + render_profile_guardian(row)


def render_status_badge(status: str, cancellation_reason: str = "") -> str:
    if status == "active":
        return (
            f'<span class="status-badge status-badge-active">'
            f'<span class="status-badge-dot" aria-hidden="true"></span>'
            f"{escape(STATUS_LABELS[status])}</span>"
        )
    reason = (
        f'<span class="status-badge-reason">{escape(cancellation_reason)}</span>'
        if cancellation_reason
        else ""
    )
    return (
        f'<span class="status-badge status-badge-cancelled">{escape(STATUS_LABELS[status])}</span>{reason}'
    )


def render_reservation_details(
    row: DbRow,
    include_notes: bool = True,
    show_status: bool = False,
    footer_markup: str = "",
    role: str = "",
    day: str = "today",
    assign_waiter: bool = False,
    assign_animator: bool = False,
    color: str = "",
) -> str:
    start_label = escape(format_time(row["start_at"]))

    status = row["status"]
    cancelled_class = " is-cancelled" if status == "cancelled" else ""

    status_markup = ""
    if show_status and status == "cancelled":
        cancellation_reason = str(row["cancellation_reason"] or "")
        status_markup = f'<div class="timeline-status">{render_status_badge(status, cancellation_reason)}</div>'

    animator_items: list[tuple[str, str]] = []
    for item in animations_from_row(row):
        label = escape(str(item.get("type") or "Animacja"))
        window = escape(format_service_window(item.get("at"), SERVICE_DURATIONS["animation_at"]))
        animator_items.append((label, window))
    if is_enabled(row, "pinata_enabled"):
        label = escape(f"Piniata: {row['pinata_theme'] or '(brak)'}")
        window = escape(format_service_window(row["pinata_at"], SERVICE_DURATIONS["pinata_at"]))
        animator_items.append((label, window))
    if is_enabled(row, "mascot_enabled"):
        label = escape(f"Maskotka: {row['mascot_type'] or '(brak)'}")
        window = escape(format_service_window(row["mascot_at"], SERVICE_DURATIONS["mascot_at"]))
        animator_items.append((label, window))
    if is_enabled(row, "balloons_enabled"):
        label = escape(f"Balony: {row['balloons_description'] or '(brak)'}")
        animator_items.append((label, ""))

    left_chips: list[str] = []
    location_label = escape(display_locations(row["adult_location"]))
    if location_label:
        left_chips.append(render_logistics_chip("location", location_label))

    if is_enabled(row, "fruit_enabled"):
        plates = f"{row['fruit_plates']} tal." if row["fruit_plates"] else "brak liczby talerzy"
        left_chips.append(render_logistics_chip("kitchen", f"Owoce ({escape(plates)})"))
    if is_enabled(row, "cake_enabled"):
        label = escape(f"Tort: {row['cake_theme'] or '(brak)'}")
        cake_meta = [format_service_window(row["cake_at"], SERVICE_DURATIONS["cake_at"])]
        details = cake_details_label(row)
        if details:
            cake_meta.append(details)
        left_chips.append(render_logistics_chip("cake", label, escape(" · ".join(item for item in cake_meta if item))))
    if is_enabled(row, "culinary_workshops_enabled"):
        label = escape(f"Warsztaty: {row['culinary_workshops_type'] or '(brak)'} ({workshop_children_label(row)})")
        window = escape(format_service_window(row["culinary_workshops_at"], SERVICE_DURATIONS["culinary_workshops_at"]))
        left_chips.append(render_logistics_chip("kitchen", label, window))

    right_chips = [
        render_logistics_chip("attraction", label, window)
        for label, window in animator_items
    ]
    logistics_markup = render_logistics_grid(left_chips, right_chips)

    callouts: list[str] = []
    if include_notes and row["notes"]:
        callouts.append(render_note_callout(str(row["notes"])))
    if status == "cancelled" and row["cancellation_reason"]:
        callouts.append(render_note_callout(str(row["cancellation_reason"]), tone="danger", label="Anulowanie"))
    notes_markup = "".join(callouts)

    footer = f'<footer class="timeline-footer">{footer_markup}</footer>' if footer_markup else ""
    waiter_markup = render_waiter_assignment(row, role, day) if assign_waiter else ""
    animator_markup = render_animator_assignment(row, role, day) if assign_animator else ""
    color_attr = f' style="--reservation-color: {escape(color)}"' if color else ""
    color_class = " has-color" if color else ""
    created_by = str(row.get("created_by") or "").strip()
    author_markup = (
        f'<div class="reservation-author">Dodał(a): <strong>{escape(created_by)}</strong></div>'
        if created_by
        else ""
    )

    return f"""
      <article class="timeline-card{cancelled_class}">
        <header class="timeline-header{color_class}"{color_attr}>
          <time class="timeline-start" datetime="{escape(str(row['start_at']))}">{start_label}</time>
          {render_profile_identity(row)}
          {waiter_markup}
          {animator_markup}
          {render_profile_guardian(row)}
          {status_markup}
        </header>
        {logistics_markup}
        {notes_markup}
        {author_markup}
        {footer}
      </article>
    """


def render_animation_type_select(values: dict[str, object], errors: dict[str, str]) -> str:
    animations = values.get("animations")
    if not isinstance(animations, list) or not animations:
        if int(values.get("animation_enabled") or 0) == 1 or values.get("animation_type"):
            animations = [{"type": values.get("animation_type", ""), "at": values.get("animation_at", "")}]
        else:
            animations = [{"type": "", "at": ""}]
    rows = []
    for item in animations:
        rows.append(
            f"""
        <div class="animation-row">
          <label class="service-extra">
            Rodzaj animacji
            <select name="animation_type">
              <option value="">Wybierz animację</option>
              {render_grouped_options(ANIMATION_GROUPS, item.get("type", ""))}
            </select>
          </label>
          <label class="service-extra">
            Start
            <div class="service-time">
              <input type="text" inputmode="numeric" autocomplete="off" placeholder="00:00" maxlength="5" name="animation_at" value="{escape(item.get("at", ""))}" data-time-input="1" data-duration-minutes="{SERVICE_DURATIONS["animation_at"]}" aria-label="Start animacji">
              <span class="service-end"></span>
            </div>
          </label>
          <button type="button" class="service-toggle-btn is-active remove-animation-row" aria-label="Usuń animację">
            <span class="service-toggle-plus" aria-hidden="true">+</span>
            <span class="service-toggle-minus" aria-hidden="true">−</span>
          </button>
        </div>
"""
        )
    return f"""
        <div class="animation-list" id="animation-list" data-animation-duration="{SERVICE_DURATIONS["animation_at"]}">
          {''.join(rows)}
        </div>
        <button type="button" class="button secondary" id="add-animation-row">+ Kolejna animacja</button>
        <div class="overlap-hint is-hidden">Godziny się pokrywają</div>
        {error_for(errors, "animation_type")}
        {error_for(errors, "animation_at")}
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
    cake_image = str(values.get("cake_image_data") or "")
    preview_class = "cake-photo-preview" if cake_image else "cake-photo-preview is-hidden"
    preview_image = f'<img src="{escape(cake_image)}" alt="Zdjęcie tortu">' if cake_image else '<img alt="Zdjęcie tortu">'
    return f"""
        <div class="cake-photo-control service-extra">
          <input type="hidden" name="cake_image_data" id="cake_image_data" value="{escape(cake_image)}">
          <input type="file" id="cake_camera_input" accept="image/*" capture="environment" class="cake-photo-file" aria-hidden="true">
          <input type="file" id="cake_gallery_input" accept="image/*" class="cake-photo-file" aria-hidden="true">
          <button type="button" class="cake-photo-trigger" id="cake_photo_trigger" aria-label="Dodaj zdjęcie tortu">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h2l1.3-2h4.4l1.3 2h2A2.5 2.5 0 0 1 20 8.5v8A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5v-8Z"/>
              <circle cx="12" cy="12.5" r="3.5"/>
            </svg>
          </button>
          <div class="cake-photo-menu is-hidden" id="cake_photo_menu">
            <button type="button" id="cake_camera_btn">Aparat</button>
            <button type="button" id="cake_gallery_btn">Galeria</button>
          </div>
          <div class="{preview_class}" id="cake_photo_preview">
            {preview_image}
            <button type="button" id="cake_photo_remove" aria-label="Usuń zdjęcie tortu">Usuń zdjęcie</button>
          </div>
          {error_for(errors, "cake_image_data")}
        </div>
        <label class="service-extra">
          Motyw tortu
          <input name="cake_theme" value="{escape(values.get("cake_theme", ""))}" placeholder="(brak)">
          {error_for(errors, "cake_theme")}
        </label>
        <label class="service-extra">
          Waga tortu
          <input name="cake_weight" value="{escape(values.get("cake_weight", ""))}" placeholder="np. 2 kg">
        </label>
        <label class="service-extra">
          Smak biszkoptu
          <input name="cake_sponge" value="{escape(values.get("cake_sponge", ""))}" placeholder="np. waniliowy">
        </label>
        <label class="service-extra">
          Nadzienie
          <input name="cake_filling" value="{escape(values.get("cake_filling", ""))}" placeholder="np. truskawkowe">
        </label>
        <label class="service-extra">
          Krem
          <input name="cake_cream" value="{escape(values.get("cake_cream", ""))}" placeholder="np. śmietankowy">
        </label>
        <label class="service-extra">
          &#346;wieczka
          <select name="cake_candle">
            <option value="">Brak / nie wiadomo</option>
            {render_labeled_options(CAKE_CANDLE_LABELS, values.get("cake_candle"))}
          </select>
          {error_for(errors, "cake_candle")}
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


def render_balloons_description_input(values: dict[str, object], errors: dict[str, str]) -> str:
    return f"""
        <label class="service-extra">
          Opis balonów
          <input name="balloons_description" value="{escape(values.get("balloons_description", ""))}" placeholder="np. girlanda z imieniem, 10 balonów helowych">
          {error_for(errors, "balloons_description")}
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
    enabled = int(values.get(enabled_field) or 0) == 1
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
        <div class="overlap-hint is-hidden">Godziny się pokrywają</div>
        {error_for(errors, time_field)}
"""
        if show_time
        else ""
    )
    body_class = "" if enabled else "is-hidden"
    toggle_class = "service-toggle-btn is-active" if enabled else "service-toggle-btn"
    toggle_label = "Usuń dodatek" if enabled else "Dodaj dodatek"
    return f"""
      <div class="service-catalog-item{' is-open' if enabled else ''}" data-service="{escape(enabled_field)}">
        <div class="service-catalog-head">
          <span>{escape(label)}{duration_label}</span>
          <button type="button" class="{toggle_class}" data-target="{escape(enabled_field)}" aria-label="{toggle_label}" aria-pressed="{'true' if enabled else 'false'}">
            <span class="service-toggle-plus" aria-hidden="true">+</span>
            <span class="service-toggle-minus" aria-hidden="true">−</span>
          </button>
        </div>
        <div class="service-catalog-body {body_class}">
          <input type="checkbox" class="service-enabled-input" name="{escape(enabled_field)}" value="1"{checked(values.get(enabled_field))}>
          {time_markup}
          {extra_markup}
        </div>
      </div>
"""


def render_room_plan(values: dict[str, object], errors: dict[str, str], *, compact: bool = False) -> str:
    statuses = availability_for(
        str(values.get("reservation_date", "")),
    )
    view_x, view_y, view_w, view_h = PLAN_VIEWBOX
    plan_url = room_plan_asset_url()

    nodes = []
    for number, cx, cy, width, height in PLAN_HOTSPOTS:
        location = location_for_plan_number(int(number))
        if not location:
            continue
        status = statuses.get(location, {"status": "free", "label": "Wolne", "color": ""})
        is_room = int(number) <= 6
        classes = ["plan-node", "room-node" if is_room else "table-node"]
        color = str(status.get("color") or "")
        style_attr = ""
        if status["status"] == "occupied":
            classes.append("is-busy")
            if color:
                style_attr = f' style="--node-color: {escape(color)}"'

        # Near-square tables follow the original circular markers; rooms stay rectangular.
        label_size = max(10.0, min(width, height) * (0.38 if is_room else 0.52))
        if not is_room and abs(width - height) <= 4:
            radius = min(width, height) / 2
            field = (
                f'<circle class="plan-field" cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}"></circle>'
            )
        else:
            x = cx - width / 2
            y = cy - height / 2
            rx = min(width, height) * (0.18 if is_room else 0.45)
            field = (
                f'<rect class="plan-field" x="{x:.1f}" y="{y:.1f}" '
                f'width="{width:.1f}" height="{height:.1f}" rx="{rx:.1f}" ry="{rx:.1f}"></rect>'
            )

        tip = str(status.get("tip") or "")
        tip_attr = f' data-tip="{escape(tip)}"' if tip else ' data-tip=""'
        nodes.append(
            f"""
    <g id="plan-object-{int(number)}" class="{' '.join(classes)}" data-location="{escape(location)}" data-plan-number="{int(number)}" role="button" tabindex="0" aria-label="{escape(location)}"{style_attr}{tip_attr}>
      <title>{escape(status["label"])}</title>
      {field}
      <text class="plan-label" x="{cx:.1f}" y="{cy:.1f}" font-size="{label_size:.1f}">{int(number)}</text>
    </g>
"""
        )

    svg_markup = f"""
        <svg class="room-plan" viewBox="{view_x:.1f} {view_y:.1f} {view_w:.1f} {view_h:.1f}" preserveAspectRatio="xMidYMid meet" aria-label="Plan sali iKids Park">
          <rect class="plan-canvas" x="{view_x:.1f}" y="{view_y:.1f}" width="{view_w:.1f}" height="{view_h:.1f}"></rect>
          <image class="plan-base" href="{escape(plan_url)}" x="0" y="0" width="1440" height="810" preserveAspectRatio="xMidYMid meet"></image>
          <g class="plan-objects" aria-label="Stoliki i salki">
            {''.join(nodes)}
          </g>
        </svg>
"""

    if compact:
        return f"""
<div class="plan-block" id="room-plan">
  <button type="button" class="plan-block-title" data-open-plan-fs aria-label="Otwórz plan sali na pełnym ekranie">PLAN SALI</button>
  {svg_markup}
</div>
"""

    return f"""
<div class="location-accordion plan-accordion is-open full" data-accordion="room-plan" id="room-plan-accordion">
  <button type="button" class="location-accordion-head plan-accordion-head" aria-expanded="true">
    <span class="location-accordion-head-main">
      <span class="location-accordion-label">Plan sali i dostępność na żywo</span>
      <span class="plan-legend-compact">
        <span><span class="legend-key key-selected"></span>wybrane</span>
      </span>
    </span>
    <span class="location-accordion-chevron" aria-hidden="true"></span>
  </button>
  <div class="location-accordion-panel">
    <div class="location-accordion-body">
      <div class="plan-wrap">
{svg_markup}
      </div>
    </div>
  </div>
</div>
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
    if ". " in cleaned:
        number, name = cleaned.split(". ", 1)
        if number.isdigit():
            return [f"{number}.", name]
    if " - " in cleaned:
        return cleaned.split(" - ", 1)
    parts = cleaned.split()
    if len(parts) <= 2:
        return [cleaned]
    midpoint = (len(parts) + 1) // 2
    return [" ".join(parts[:midpoint]), " ".join(parts[midpoint:])]


def render_inventory_category_options(current: object = "") -> str:
    options = ['<option value="">Kategoria</option>']
    for key in INVENTORY_CATEGORIES:
        options.append(
            f'<option value="{escape(key)}"{selected(current, key)}>{escape(INVENTORY_CATEGORY_LABELS[key])}</option>'
        )
    return "\n".join(options)


def render_inventory_catalog_options(category: str = "", current_item_id: object = "") -> str:
    items = inventory.list_inventory_items()
    options = ['<option value="">Nowa pozycja / wybierz z katalogu</option>']
    for item in items:
        if category and str(item.get("category")) != category:
            continue
        item_id = str(item.get("id"))
        label = f'{INVENTORY_CATEGORY_LABELS.get(str(item.get("category")), "")}: {item.get("name")} (wolne: {item.get("qty_available", 0)})'
        options.append(
            f'<option value="{escape(item_id)}" data-category="{escape(item.get("category"))}" '
            f'data-name="{escape(item.get("name"))}" data-description="{escape(item.get("description") or "")}"'
            f"{selected(str(current_item_id), item_id)}>{escape(label)}</option>"
        )
    return "\n".join(options)


def render_inventory_line_row(line: dict[str, object] | None = None) -> str:
    line = line or {}
    category = str(line.get("category") or "")
    item_id = str(line.get("item_id") or "")
    name = str(line.get("name") or "")
    qty = str(line.get("qty") or "")
    description = str(line.get("description") or "")
    return f"""
<div class="inventory-line-row">
  <label>
    Kategoria
    <select name="inventory_category" class="inventory-category">{render_inventory_category_options(category)}</select>
  </label>
  <label>
    Katalog
    <select name="inventory_item_id" class="inventory-item-id">{render_inventory_catalog_options(category, item_id)}</select>
  </label>
  <label>
    Nazwa
    <input type="text" name="inventory_name" class="inventory-name" maxlength="120" value="{escape(name)}" placeholder="np. Piniata Jednorożec" enterkeyhint="next">
  </label>
  <label>
    Ilość
    <input type="number" name="inventory_qty" class="inventory-qty" min="1" max="500" value="{escape(qty)}" placeholder="1" inputmode="numeric" enterkeyhint="next">
  </label>
  <label class="full">
    Opis
    <input type="text" name="inventory_description" class="inventory-description" maxlength="300" value="{escape(description)}" placeholder="Jak ma wyglądać zestaw / motyw" enterkeyhint="done">
  </label>
  <button type="button" class="button secondary remove-inventory-line" aria-label="Usuń pozycję">Usuń</button>
</div>
"""


def render_inventory_lines_fields(values: dict[str, object], errors: dict[str, str]) -> str:
    lines = values.get("inventory_lines") or []
    if not isinstance(lines, list):
        lines = []
    rows = "".join(render_inventory_line_row(line if isinstance(line, dict) else {}) for line in lines)
    return f"""
<div class="inventory-lines-block">
  <div id="inventory-lines-list">{rows}</div>
  <button type="button" class="button secondary" id="add-inventory-line">+ Dodaj pozycję inwentury</button>
  {error_for(errors, "inventory_lines")}
</div>
"""


def render_form(
    values: dict[str, object],
    errors: dict[str, str],
    role: str,
    day: str,
    include_plan: bool = False,
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
    plan_markup = render_room_plan(values, errors) if include_plan else ""
    current_type = reservation_type(values)
    form_type_class = "is-table-reservation" if current_type == "table" else ""

    return f"""
<section class="role-board organizer-form-board">
  <div class="section-head">
    <div>
      <h2>{title}</h2>
      <p class="subtitle">Rezerwacja blokuje wybrane lokalizacje na cały dzień. Godziny przy usługach są godzinami startu.</p>
    </div>
  </div>
  <form method="post" action="/reservations?role={escape(role)}&day={escape(day)}" id="reservation-form" class="{form_type_class}">
    <input type="hidden" name="id" id="reservation_id" value="{escape(reservation_id)}">
    <input type="hidden" name="stage_block_acknowledged" id="stage_block_acknowledged" value="">
    <div class="form-board">
      <div class="form-category">
        <h3 class="category-title">Termin</h3>
        <div class="category-fields single termin">
          <fieldset class="reservation-type-switch full">
            <legend>Typ wpisu</legend>
            <label>
              <input type="radio" name="reservation_type" value="banquet"{checked(1 if current_type == "banquet" else 0)}>
              Bankiet
            </label>
            <label>
              <input type="radio" name="reservation_type" value="table"{checked(1 if current_type == "table" else 0)}>
              Rezerwacja stolika
            </label>
          </fieldset>
          <label class="cooperation-toggle full">
            <input type="checkbox" name="cooperation_enabled" value="1"{checked(values.get("cooperation_enabled"))}>
            Etykieta: Współpraca
          </label>
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
          <div class="guest-count-block">
            <span class="guest-count-title">Liczba</span>
            <div class="guest-count-grid">
              <label class="guest-count-field banquet-only">
                dzieci
                <input class="guest-count-input" type="number" name="children_count" min="1" max="120" value="{escape(values.get("children_count", ""))}" required>
                {error_for(errors, "children_count")}
              </label>
              <label class="guest-count-field banquet-only">
                dorosłych
                <input class="guest-count-input" type="number" name="adults_count" min="0" max="120" value="{escape(values.get("adults_count", ""))}" required>
                {error_for(errors, "adults_count")}
              </label>
              <label class="guest-count-field table-only">
                gości
                <input class="guest-count-input" type="number" name="guest_total" min="1" max="240" value="{escape((values.get("guest_total") or values.get("children_count")) if current_type == "table" else values.get("guest_total", ""))}">
                {error_for(errors, "guest_total")}
              </label>
            </div>
          </div>
          <label>
            Rodzic / osoba rezerwująca
            <input name="parent_name" autocomplete="name" value="{escape(values.get("parent_name", ""))}" required minlength="2" maxlength="80" pattern="[A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+([ '\\-][A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+)*" title="Tylko litery, spacje, myślnik lub apostrof">
            {error_for(errors, "parent_name")}
          </label>
          <label>
            Telefon
            <input name="parent_phone" id="parent_phone" autocomplete="tel" inputmode="tel" value="{escape(values.get("parent_phone", ""))}" placeholder="np. 500 000 000" required maxlength="20" data-phone-input title="9 cyfr, np. 500 000 000 lub +48 500 000 000">
            {error_for(errors, "parent_phone")}
          </label>
          <label>
            Kto dodał rezerwację
            <select name="created_by" required>
              <option value="">Wybierz osobę</option>
              {render_options(list(RESERVATION_AUTHORS), values.get("created_by", ""))}
            </select>
            {error_for(errors, "created_by")}
          </label>
          <div class="banquet-only full">
            {render_birthday_children_fields(values, errors)}
          </div>
        </div>
      </div>

      <div class="form-category">
        <h3 class="category-title">Lokalizacje</h3>
        <div class="category-fields single">
          <div class="location-picker full" id="location-picker">
            <p class="subtitle location-hint">Wybierz lożę po lewej i stoliki po prawej (pojedynczo lub zakresem od–do), albo kliknij element na planie.</p>
            <div class="location-forms">
              <div class="banquet-only">{render_child_location_picker(values.get("child_location"))}</div>
              {render_adult_location_picker(values.get("adult_location"))}
            </div>
            <div class="location-confirm-bar">
              <div class="location-summary">
                <span class="location-summary-label">Podgląd wyboru</span>
                <div class="location-summary-values">
                  <span class="location-summary-item" id="location-summary-child">{escape(display_location(values.get("child_location")))}</span>
                  <span class="location-summary-item" id="location-summary-adult">{escape(display_locations(values.get("adult_location")))}</span>
                </div>
              </div>
              <button type="button" class="button location-confirm-btn" id="location-confirm-btn">Zatwierdź lokalizacje</button>
            </div>
            {error_for(errors, "child_location")}
            {error_for(errors, "adult_location")}
            {plan_markup}
          </div>
        </div>
      </div>

      <div class="form-category wide banquet-only">
        <h3 class="category-title">Atrakcje i dodatki</h3>
        <p class="subtitle location-hint">Przy każdej opcji kliknij zielony plus, wybierz godzinę i rodzaj. Czerwony minus usuwa dodatek.</p>
        <div id="service-overlap-notice" class="overlap-notice is-hidden">Godziny się pokrywają</div>
        <div
          id="stage-block-notice"
          class="overlap-notice stage-block-notice is-hidden"
          data-start-minutes="{STAGE_BLOCK_START.hour * 60 + STAGE_BLOCK_START.minute}"
          data-end-minutes="{STAGE_BLOCK_END.hour * 60 + STAGE_BLOCK_END.minute}"
          data-message="{escape(STAGE_BLOCK_MESSAGE)}"
          data-confirm="{escape(STAGE_BLOCK_CONFIRM_PROMPT)}"
        >{escape(STAGE_BLOCK_MESSAGE)} Możesz zatwierdzić przy zapisie.</div>
        <div class="category-fields services service-catalog">
          {render_service_option(values, errors, "animation_enabled", "animation_at", "Animacja", SERVICE_DURATIONS["animation_at"], render_animation_type_select(values, errors), show_time=False)}
          {render_service_option(values, errors, "cake_enabled", "cake_at", "Tort", SERVICE_DURATIONS["cake_at"], render_cake_theme_input(values, errors))}
          {render_service_option(values, errors, "fruit_enabled", "fruit_at", "Owoce", None, render_fruit_plates_input(values, errors), show_time=False)}
          {render_service_option(values, errors, "culinary_workshops_enabled", "culinary_workshops_at", "Warsztaty kulinarne", SERVICE_DURATIONS["culinary_workshops_at"], render_workshop_type_select(values, errors))}
          {render_service_option(values, errors, "pinata_enabled", "pinata_at", "Piniata", SERVICE_DURATIONS["pinata_at"], render_pinata_theme_input(values, errors))}
          {render_service_option(values, errors, "mascot_enabled", "mascot_at", "Maskotka", SERVICE_DURATIONS["mascot_at"], render_mascot_type_select(values, errors))}
          {render_service_option(values, errors, "balloons_enabled", "balloons_at", "Balony", None, render_balloons_description_input(values, errors), show_time=False)}
        </div>
      </div>

      <div class="form-category wide banquet-only">
        <h3 class="category-title">Pozycje inwentury</h3>
        <p class="subtitle location-hint">Piniata, balony lub zestaw tematyczny — wybierz z katalogu albo dodaj nową pozycję do zamówienia.</p>
        {render_inventory_lines_fields(values, errors)}
      </div>

      <div class="form-category wide">
        <h3 class="category-title">Uwagi</h3>
        <div class="category-fields">
          <label class="full">
            Notatki
            <textarea name="notes" maxlength="1000" placeholder="Alergie, ustalenia z rodzicem, szczegóły organizacyjne...">{escape(values.get("notes", ""))}</textarea>
            {error_for(errors, "notes")}
          </label>
          <label class="{cancellation_class}" id="cancellation_reason_field">
            Powód anulowania
            <textarea name="cancellation_reason" id="cancellation_reason" maxlength="300" placeholder="Wymagane tylko przy zmianie statusu na Anulowana.">{escape(values.get("cancellation_reason", ""))}</textarea>
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


def service_pills(row: DbRow) -> str:
    items = []
    for item in animations_from_row(row):
        animation_label = f"Animacja: {item.get('type')}" if item.get("type") else "Animacja"
        items.append((animation_label, item.get("at"), SERVICE_DURATIONS["animation_at"]))
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
            workshop_label = f"Warsztaty: {row['culinary_workshops_type']} ({workshop_children_label(row)})"
        items.append((workshop_label, row["culinary_workshops_at"], SERVICE_DURATIONS["culinary_workshops_at"]))
    if is_enabled(row, "pinata_enabled"):
        pinata_label = f"Piniata: {row['pinata_theme'] or '(brak)'}"
        items.append((pinata_label, row["pinata_at"], SERVICE_DURATIONS["pinata_at"]))
    if is_enabled(row, "mascot_enabled"):
        mascot_label = "Maskotka"
        if row["mascot_type"]:
            mascot_label = f"Maskotka: {row['mascot_type']}"
        items.append((mascot_label, row["mascot_at"], SERVICE_DURATIONS["mascot_at"]))
    if is_enabled(row, "balloons_enabled"):
        balloons_label = f"Balony: {row['balloons_description'] or '(brak)'}"
        items.append((balloons_label, row["balloons_at"], None))

    if not items:
        return '<span class="pill">Bez dodatków</span>'
    return "".join(
        f'<span class="pill">{escape(label)}{": " + escape(format_service_window(value, duration)) if value else ""}</span>'
        for label, value, duration in items
    )


def render_metrics(rows: list[DbRow], day: str) -> str:
    active = [row for row in rows if row["status"] == "active"]
    animation_count = sum(len(animations_from_row(row)) for row in active)
    workshops = sum(1 for row in active if is_enabled(row, "culinary_workshops_enabled"))
    cakes = sum(1 for row in active if is_enabled(row, "cake_enabled"))
    pinatas = sum(1 for row in active if is_enabled(row, "pinata_enabled"))
    guests = sum(int(row["children_count"]) + int(row["adults_count"]) for row in active)
    current_day_query = day_query(selected_day(day))
    animators_link = link_for("animators", current_day_query)
    kitchen_link = link_for("kitchen", current_day_query)
    return f"""
<div class="metrics">
  {render_metric(len(active), "bankiety", "bankiety")}
  {render_metric(guests, "liczba gości", "guests")}
  {render_metric(animation_count, "animacje", "animacje", animators_link)}
  {render_metric(pinatas, "piniaty", "piniaty", animators_link)}
  {render_metric(cakes, "torty", "torty", kitchen_link)}
  {render_metric(workshops, "warsztaty", "warsztaty", kitchen_link)}
</div>
"""


def render_manager_view(rows: list[DbRow], role: str, day: str) -> str:
    target_day = selected_day(day)
    weekday = WEEKDAY_LABELS[target_day.weekday()]
    day_label = f"{weekday} · {target_day.strftime('%d.%m')}"
    colors = reservation_color_map(rows)
    if not rows:
        body = '<div class="empty">Brak rezerwacji w wybranym dniu.</div>'
    else:
        cards = [
            render_reservation_details(
                row,
                include_notes=True,
                show_status=True,
                role=role,
                day=day,
                assign_waiter=True,
                color=colors.get(int(row["id"]), ""),
            )
            for row in rows
        ]
        body = "".join(cards)

    return f"""
  <div class="day-heading">{escape(day_label)}</div>
  {body}
{staff_assignment_script()}
"""


def render_animator_view(rows: list[DbRow], role: str, day: str) -> str:
    banquets = []
    for row in rows:
        if row["status"] != "active":
            continue

        tasks = []
        for index, item in enumerate(animations_from_row(row)):
            tasks.append((item.get("at"), item.get("type") or "Animacja", f"anim:{index}"))
        if is_enabled(row, "pinata_enabled"):
            tasks.append((row["pinata_at"], f"Piniata: {row['pinata_theme'] or '(brak)'}", "pinata"))
        if is_enabled(row, "mascot_enabled"):
            tasks.append((row["mascot_at"], f"Maskotka: {row['mascot_type'] or '(brak)'}", "mascot"))

        task_items = []
        for task_time, task_name, slot in tasks:
            time_label = format_time(task_time)
            if not time_label:
                continue
            try:
                hour = int(time_label[:2])
            except ValueError:
                hour = 0
            if hour < 10 or hour > 21:
                continue
            task_items.append((time_label, task_name, slot))

        if task_items:
            task_items.sort(key=lambda item: (item[0], item[1]))
            banquets.append((task_items[0][0], row, task_items, row["notes"]))

    banquets.sort(key=lambda item: (item[0], item[1]["child_location"]))
    if not banquets:
        return '<section><div class="empty">Brak animacji</div></section>'

    banquet_cards = []
    for _, row, task_items, notes in banquets:
        task_blocks = []
        for time_label, task_name, slot in task_items:
            assign_slot = render_animator_assignment(row, role, day, slot=slot)
            task_blocks.append(
                f"""
              <div class="banquet-task">
                <div class="task-time">{escape(time_label)}</div>
                <div class="task-detail">
                  <div class="task-label-row">
                    <div class="task-label">{escape(task_name)}</div>
                    {assign_slot}
                  </div>
                </div>
              </div>
            """
            )
        banquet_cards.append(
            f"""
            <div class="banquet-card role-card animator-card">
              {render_role_card_identity(row, show_guest_summary=False, show_children_tag=True)}
              <div class="banquet-tasks">{''.join(task_blocks)}</div>
              {render_role_extra_info(row, notes)}
            </div>
            """
        )

    task_count = sum(len(task_items) for _, _, task_items, _ in banquets)
    return f"""
<section class="role-board animator-board">
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
{staff_assignment_script()}
"""


def render_kitchen_view(rows: list[DbRow]) -> str:
    active = [row for row in rows if row["status"] == "active"]
    banquet_entries: list[tuple[str, str]] = []

    for row in active:
        has_fruit = is_enabled(row, "fruit_enabled")
        has_cake = is_enabled(row, "cake_enabled")
        has_workshops = is_enabled(row, "culinary_workshops_enabled")
        if not (has_fruit or has_cake or has_workshops):
            continue

        task_items = []
        if has_fruit:
            plates = f"{row['fruit_plates']} tal." if row["fruit_plates"] else "brak liczby talerzy"
            task_items.append((format_time(row["start_at"]), "Owoce", plates))

        if has_cake:
            task_items.append(
                (
                    format_time(row["cake_at"]),
                    "Tort",
                    "",
                    "",
                    cake_kitchen_panel(row),
                    "cake",
                )
            )

        if has_workshops:
            workshop_name = row["culinary_workshops_type"] or "Warsztaty"
            workshop_name = f"{workshop_name} ({workshop_children_label(row)})"
            workshop_window = format_service_window(
                row["culinary_workshops_at"],
                SERVICE_DURATIONS["culinary_workshops_at"],
            )
            task_items.append((format_time(row["culinary_workshops_at"]), "Warsztaty", workshop_name, workshop_window))

        task_items = [item for item in task_items if item[0]]
        if not task_items:
            continue
        task_items.sort(key=lambda item: (item[0], item[1]))
        task_blocks = []
        for item in task_items:
            kind = item[5] if len(item) > 5 else ""
            if kind == "cake":
                task_blocks.append(
                    f"""
              <div class="banquet-task kitchen-task kitchen-task--cake">
                <div class="task-time">{escape(item[0])}</div>
                <div class="task-detail">
                  <div class="task-label">{escape(item[1])}</div>
                  {item[4]}
                </div>
              </div>
            """
                )
                continue
            meta = escape(item[2])
            if len(item) > 3 and item[3]:
                meta = f"{meta} · {escape(item[3])}"
            image_markup = item[4] if len(item) > 4 else ""
            task_blocks.append(
                f"""
              <div class="banquet-task kitchen-task">
                <div class="task-time">{escape(item[0])}</div>
                <div class="task-detail">
                  <div class="task-label">{escape(item[1])}</div>
                  <div class="task-meta">{meta}</div>
                  {image_markup}
                </div>
              </div>
            """
            )
        task_markup = "".join(task_blocks)
        sort_time = task_items[0][0]
        banquet_entries.append(
            (
                sort_time,
            f"""
            <div class="banquet-card role-card kitchen-card">
              {render_role_card_identity(row, show_guest_summary=False, show_total_guests_tag=True)}
              <div class="banquet-tasks kitchen-orders">{task_markup}</div>
              {render_role_extra_info(row, row["notes"])}
            </div>
            """,
            )
        )

    if not banquet_entries:
        return '<section><div class="empty">Brak zamówień kuchennych</div></section>'

    banquet_entries.sort(key=lambda item: item[0])
    banquet_cards = [markup for _, markup in banquet_entries]

    return f"""
<section class="role-board kitchen-board-section">
  <div class="section-head">
    <div>
      <h2>Kuchnia</h2>
      <p class="subtitle">Owoce, torty i warsztaty.</p>
    </div>
    <span class="count">{len(banquet_cards)} bankietów</span>
  </div>
  <div class="banquet-grid">
    {''.join(banquet_cards)}
  </div>
</section>
"""


def render_organizer_view(rows: list[DbRow], role: str, day: str) -> str:
    target_day = selected_day(day)
    weekday = WEEKDAY_LABELS[target_day.weekday()]
    day_label = f"{weekday} · {target_day.strftime('%d.%m')}"
    colors = reservation_color_map(rows)
    if not rows:
        body = '<div class="empty">Brak rezerwacji w wybranym dniu.</div>'
    else:
        cards = []
        for row in rows:
            actions = f"""
              <div class="inline-actions">
                <a class="button secondary" href="{link_for(role, day, edit=row["id"])}">Edytuj</a>
                <a class="button secondary" href="/history?id={escape(row["id"])}&role={escape(role)}&day={escape(day)}">Historia</a>
                <form class="inline-form" method="post" action="/delete?role={escape(role)}&day={escape(day)}" onsubmit="return confirm('Usunąć tę rezerwację? Tej operacji nie można cofnąć.');">
                  <input type="hidden" name="id" value="{escape(row["id"])}">
                  <button class="button danger" type="submit">Usuń</button>
                </form>
              </div>
            """
            cards.append(
                render_reservation_details(
                    row,
                    include_notes=True,
                    show_status=True,
                    footer_markup=actions,
                    color=colors.get(int(row["id"]), ""),
                )
            )
        body = "".join(cards)

    return f"""
<section class="role-board organizer-board">
  <div class="section-head">
    <div>
      <h2>Rezerwacje dnia</h2>
      <p class="subtitle">{escape(day_label)} · zarządzanie rezerwacjami, statusami i dodatkami.</p>
    </div>
    <span class="count">{len(rows)} pozycji</span>
  </div>
  <div class="organizer-day-list">
    {body}
  </div>
</section>
"""


def render_role_view(role: str, rows: list[DbRow], day: str) -> str:
    if role == "animators":
        return render_animator_view(rows, role, day)
    if role == "kitchen":
        return render_kitchen_view(rows)
    if role == "organizer":
        return render_organizer_view(rows, role, day)
    return render_manager_view(rows, role, day)


def schedule_url(
    *,
    role: str,
    day: str,
    department: str,
    month: str,
    week: str,
    view: str,
) -> str:
    return "/grafiki?" + urlencode(
        {
            "role": role,
            "day": day,
            "department": department,
            "month": month,
            "week": week,
            "view": view,
        }
    )


def schedule_entry_has_month(entry: dict[str, object], month: str) -> bool:
    week_months = entry.get("week_months")
    if isinstance(week_months, list) and week_months:
        return month in {str(value) for value in week_months if value}
    sheet_month = str(entry.get("sheet_month") or "").strip()
    if sheet_month == month:
        return True
    shifts = entry.get("shifts", [])
    return any(isinstance(shift, dict) and shift.get("month") == month for shift in shifts)


def schedule_entry_has_week(entry: dict[str, object], week: str) -> bool:
    return str(entry.get("week_start") or "") == week


def schedule_available_months(entries: list[dict[str, object]]) -> list[str]:
    months: set[str] = set()
    for entry in entries:
        week_months = entry.get("week_months")
        if isinstance(week_months, list):
            for value in week_months:
                if value:
                    months.add(str(value))
        sheet_month = str(entry.get("sheet_month") or "").strip()
        if sheet_month:
            months.add(sheet_month)
        shifts = entry.get("shifts", [])
        if not isinstance(shifts, list):
            continue
        for shift in shifts:
            if isinstance(shift, dict) and shift.get("month"):
                months.add(str(shift.get("month")))
    return sorted(months)


def schedule_available_weeks(entries: list[dict[str, object]], month: str, department: str) -> list[str]:
    weeks = {
        str(entry.get("week_start") or "")
        for entry in entries
        if entry.get("week_start")
        and (department == "all" or entry.get("department") == department)
        and schedule_entry_has_month(entry, month)
    }
    return sorted(weeks)


def schedule_default_week(weeks: list[str], selected_month: str) -> str:
    today = current_app_date()
    today_key = schedule_month_key(today)
    if today_key == selected_month:
        for week in weeks:
            try:
                week_start = date.fromisoformat(week)
            except ValueError:
                continue
            if week_start <= today <= week_start + timedelta(days=6):
                return week
    return weeks[0] if weeks else ""


def schedule_filtered_entries(
    entries: list[dict[str, object]],
    *,
    department: str,
    month: str,
    week: str = "",
) -> list[dict[str, object]]:
    filtered = []
    for entry in entries:
        if department != "all" and entry.get("department") != department:
            continue
        if not schedule_entry_has_month(entry, month):
            continue
        if week and not schedule_entry_has_week(entry, week):
            continue
        filtered.append(entry)
    return filtered


def schedule_shift_is_work(value: object) -> bool:
    normalized = normalize_search_text(value)
    return bool(normalized) and normalized not in {
        "-",
        ".",
        "x",
        "w",
        "wolne",
        "u",
        "urlop",
        "?",
    }


def normalize_schedule_shift(value: object) -> str:
    """Normalizuje wpis z arkusza: '10- 20' → '10-20', '9-15.30' → '9-15:30', '11-21B' → '11-21'."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"\.(\d{2})(?=$|-)", r":\1", raw)
    raw = re.sub(r"([0-9:])[Bb]$", r"\1", raw)
    return raw


def schedule_month_days(month: str) -> list[date]:
    try:
        year_value, month_value = (int(part) for part in month.split("-", 1))
        first_day = date(year_value, month_value, 1)
    except ValueError:
        return []
    days = []
    current = first_day
    while current.month == first_day.month:
        days.append(current)
        current += timedelta(days=1)
    return days


def schedule_week_days(week_start: str) -> list[date]:
    try:
        start = date.fromisoformat(week_start)
    except ValueError:
        return []
    return [start + timedelta(days=offset) for offset in range(7)]


def schedule_adjacent_week(week: str, weeks: list[str], delta: int) -> str:
    if not weeks or week not in weeks:
        return week
    index = weeks.index(week) + delta
    if index < 0 or index >= len(weeks):
        return week
    return weeks[index]


def schedule_shift_cell_score(shift: dict[str, object]) -> int:
    score = 0
    if schedule_shift_is_work(shift.get("shift")):
        score += 2
    if str(shift.get("hours") or "").strip():
        score += 1
    return score


def schedule_entry_row_score(entry: dict[str, object]) -> int:
    score = 0
    shifts = entry.get("shifts", [])
    if isinstance(shifts, list):
        for shift in shifts:
            if not isinstance(shift, dict):
                continue
            score += schedule_shift_cell_score(shift)
    if str(entry.get("total_hours") or "").strip():
        score += 2
    if str(entry.get("position") or "").strip():
        score += 1
    return score


def schedule_entry_work_signatures(entry: dict[str, object]) -> dict[str, str]:
    signatures: dict[str, str] = {}
    shifts = entry.get("shifts", [])
    if not isinstance(shifts, list):
        return signatures
    for shift in shifts:
        if not isinstance(shift, dict):
            continue
        iso = str(shift.get("date") or "").strip()
        if not iso or not schedule_shift_is_work(shift.get("shift")):
            continue
        signatures[iso] = normalize_schedule_shift(shift.get("shift"))
    return signatures


def schedule_entry_is_redundant_duplicate(
    existing_days: dict[str, object],
    entry: dict[str, object],
) -> bool:
    """Pomija zdublowany wiersz z arkusza, który nie dodaje nowych zmian."""
    incoming = schedule_entry_work_signatures(entry)
    if not incoming:
        return True
    if not isinstance(existing_days, dict) or not existing_days:
        return False
    for iso, shift in incoming.items():
        existing = existing_days.get(iso)
        if not isinstance(existing, dict) or not schedule_shift_is_work(existing.get("shift")):
            return False
        if normalize_schedule_shift(existing.get("shift")) != shift:
            return False
    return True


def schedule_prefer_position(current: str, candidate: str) -> str:
    current_value = str(current or "").strip()
    candidate_value = str(candidate or "").strip()
    if not current_value:
        return candidate_value
    if not candidate_value:
        return current_value
    if len(candidate_value) > len(current_value):
        return candidate_value
    return current_value


def schedule_prefer_display_name(current: str, candidate: str) -> str:
    current_value = str(current or "").strip()
    candidate_value = str(candidate or "").strip()
    if not current_value:
        return candidate_value
    if not candidate_value:
        return current_value
    if len(candidate_value) > len(current_value):
        return candidate_value
    return current_value


def recalculate_compact_schedule_total(person: dict[str, object]) -> None:
    days = person.get("days", {})
    total = 0.0
    if isinstance(days, dict):
        for cell in days.values():
            if not isinstance(cell, dict):
                continue
            if schedule_shift_is_work(cell.get("shift")):
                total += cell_hours_value(cell.get("shift"), cell.get("hours"))
    person["total"] = total


def compact_schedule_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for entry in entries:
        name = schedule_clean_person_name(entry.get("name"))
        if not name:
            continue
        person_key = schedule_person_key(name)
        current = merged.get(person_key)
        current_days = current.get("days", {}) if isinstance(current, dict) else {}
        if not isinstance(current_days, dict):
            current_days = {}
        work_signature = schedule_entry_work_signatures(entry)
        if not current and not work_signature:
            continue
        if current and schedule_entry_is_redundant_duplicate(current_days, entry):
            continue
        if not current:
            current = {
                "name": name,
                "position": str(entry.get("position") or "").strip(),
                "total": 0.0,
                "days": {},
            }
            merged[person_key] = current
        else:
            current["name"] = schedule_prefer_display_name(str(current.get("name") or ""), name)
            current["position"] = schedule_prefer_position(
                str(current.get("position") or ""),
                str(entry.get("position") or ""),
            )
        current_days = current["days"]
        if not isinstance(current_days, dict):
            current_days = {}
            current["days"] = current_days
        for shift in entry.get("shifts", []) if isinstance(entry.get("shifts", []), list) else []:
            if not isinstance(shift, dict) or not shift.get("date"):
                continue
            iso = str(shift["date"])
            next_cell = {
                "shift": str(shift.get("shift") or "").strip(),
                "hours": str(shift.get("hours") or "").strip(),
            }
            existing = current_days.get(iso)
            if isinstance(existing, dict):
                if schedule_shift_cell_score(existing) > schedule_shift_cell_score(next_cell):
                    continue
            current_days[iso] = next_cell
    for person in merged.values():
        recalculate_compact_schedule_total(person)
    return list(merged.values())


def format_schedule_total(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number.is_integer():
        return str(int(number))
    return str(number).replace(".", ",")


def parse_shift_start_hour(shift: object) -> float | None:
    """Wyciąga godzinę startu z wartości typu '9-21', '15:30-21', '10-16:30'."""
    raw = normalize_schedule_shift(shift)
    if not raw or not schedule_shift_is_work(raw):
        return None
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?", raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23:
        return None
    return hour + (minute / 60.0)


def parse_shift_end_hour(shift: object) -> float | None:
    """Wyciąga godzinę końca z zakresu typu '9-21', '15:30-21', '10:30-20:00'."""
    raw = normalize_schedule_shift(shift)
    if not raw or not schedule_shift_is_work(raw) or "-" not in raw:
        return None
    end_raw = raw.split("-", 1)[1]
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?", end_raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23:
        return None
    return hour + (minute / 60.0)


def estimate_hours_from_shift(shift: object) -> float:
    """Szacuje długość zmiany z zakresu godzin, gdy brak kolumny 'ilość godzin'."""
    start = parse_shift_start_hour(shift)
    end = parse_shift_end_hour(shift)
    if start is None or end is None:
        return 0.0
    if end < start:
        end += 24.0
    return round(end - start, 2)


def cell_hours_value(shift: object, hours: object) -> float:
    """Preferuje wpisaną liczbę godzin; inaczej liczy z zakresu '9-21'."""
    parsed = parse_hours_number(hours)
    if parsed > 0:
        return parsed
    return estimate_hours_from_shift(shift)


SHIFT_REPORT_HALF_HOURS_MAX = 6.0  # do 6 h włącznie = pół zmiany; powyżej = cała

# Pan Valery — codziennie na zmianie, nie występuje w arkuszu grafiku.
SHIFT_REPORT_STANDING_KONSERWATOR = 1

SHIFT_REPORT_ROLE_ORDER = [
    "Administrator",
    "Bar",
    "Animatorzy",
    "Organizator urodzin",
    "Kelnerzy",
    "Pracownia kreatywna",
    "Sprzątaczki + zmywak",
    "Recepcja",
    "Kuchnia",
    "Dyrekcja",
    "Księgowość",
    "Konserwator",
    "Kierownik HR",
    "Kierownik animatorów",
    "Inne",
]

SHIFT_REPORT_ROLE_EMOJI = {
    "Administrator": "👔",
    "Bar": "🍸",
    "Animatorzy": "🎈",
    "Organizator urodzin": "🎉",
    "Kelnerzy": "🍽️",
    "Pracownia kreatywna": "🎨",
    "Sprzątaczki + zmywak": "🧹",
    "Recepcja": "🛎️",
    "Kuchnia": "👨‍🍳",
    "Dyrekcja": "💼",
    "Księgowość": "📊",
    "Konserwator": "🔧",
    "Kierownik HR": "📋",
    "Kierownik animatorów": "⭐",
    "Inne": "➕",
}

# Gdy w arkuszu brak stanowiska (Managerowie) — znane przypisania po imieniu i nazwisku.
SHIFT_REPORT_NAME_OVERRIDES: dict[str, str] = {
    normalize_search_text("Agata Krzyżanowska"): "Kierownik HR",
    normalize_search_text("Adam Tur"): "Kierownik animatorów",
    normalize_search_text("Ania Hernacka"): "Księgowość",
    normalize_search_text("Łukasz Aleksandrowicz"): "Dyrekcja",
    normalize_search_text("Olga Dietkovska"): "Dyrekcja",
    normalize_search_text("Maciej Pacholak"): "Kuchnia",
    normalize_search_text("Weronika Walkowiak"): "Administrator",
    normalize_search_text("Marek Dutkiewicz"): "Administrator",
}

# Etykieta stanowiska w tabeli grafiku (może różnić się od linii raportu zmiany).
SCHEDULE_POSITION_DISPLAY_OVERRIDES: dict[str, str] = {
    schedule_person_key("Maciej Pacholak"): "Szef Kuchni",
}

# Skróty stanowisk w widoku grafiku (tabela, roster, podsumowanie dnia).
SCHEDULE_ROLE_ABBREVIATIONS: dict[str, str] = {
    "Animatorzy": "A",
    "Organizator urodzin": "OU",
    "Kelnerzy": "K",
    "Bar": "B",
    "Administrator": "ADM",
    "Kierownik animatorów": "KA",
    "Kierownik HR": "HR",
    "Pracownia kreatywna": "PK",
    "Sprzątaczki + zmywak": "SZ",
    "Recepcja": "R",
    "Kuchnia": "KU",
    "Dyrekcja": "D",
    "Księgowość": "KŚ",
    "Konserwator": "KO",
    "Inne": "?",
}

SCHEDULE_POSITION_SPECIAL_ABBREVIATIONS: dict[str, str] = {
    "szef kuchni": "SK",
    "soue szef": "SS",
    "sou chef": "SS",
    "sous chef": "SS",
    "soue": "SS",
    "cukiernik": "CU",
    "pizza": "PZ",
    "kucharz": "KR",
    "pomoc kuchenna": "PKU",
    "kierownik zmiany": "ADM",
    "organizator urodzin": "OU",
    "pracownia tworcza": "PK",
    "pracownia kreatywna": "PK",
}

SCHEDULE_POSITION_MODIFIER_BY_ROLE: dict[str, str] = {
    "Animatorzy": "emp-position--animator",
    "Organizator urodzin": "emp-position--organizer",
    "Kelnerzy": "emp-position--kelner",
    "Bar": "emp-position--bar",
    "Administrator": "emp-position--admin",
    "Kierownik animatorów": "emp-position--kierownik-animatorow",
    "Kierownik HR": "emp-position--hr",
    "Pracownia kreatywna": "emp-position--pracownia",
    "Sprzątaczki + zmywak": "emp-position--sprzatanie",
    "Recepcja": "emp-position--recepcja",
    "Kuchnia": "emp-position--kuchnia",
    "Dyrekcja": "emp-position--dyrekcja",
    "Księgowość": "emp-position--ksiegowosc",
    "Konserwator": "emp-position--konserwator",
    "Inne": "emp-position--other",
}

SCHEDULE_POSITION_ABBREV_TO_ROLE: dict[str, str] = {
    abbrev.upper(): role for role, abbrev in SCHEDULE_ROLE_ABBREVIATIONS.items()
}
SCHEDULE_POSITION_ABBREV_TO_ROLE["SK"] = "Kuchnia"
SCHEDULE_POSITION_ABBREV_TO_ROLE["SS"] = "Kuchnia"
SCHEDULE_POSITION_ABBREV_TO_ROLE["CU"] = "Kuchnia"
SCHEDULE_POSITION_ABBREV_TO_ROLE["PZ"] = "Kuchnia"
SCHEDULE_POSITION_ABBREV_TO_ROLE["KR"] = "Kuchnia"
SCHEDULE_POSITION_ABBREV_TO_ROLE["PKU"] = "Kuchnia"


def shift_has_bar_marker(value: object) -> bool:
    """Dopisek B w komórce (np. 11-21B) = kelner obsługuje bar tego dnia."""
    raw = re.sub(r"\s+", "", str(value or "").strip())
    return bool(re.search(r"[Bb]$", raw))


def report_role_name_override(name: object) -> str | None:
    return SHIFT_REPORT_NAME_OVERRIDES.get(normalize_search_text(name))


def schedule_employee_full_position(name: str, position: str) -> str:
    """Pełna etykieta stanowiska (np. do tooltipa)."""
    display_override = SCHEDULE_POSITION_DISPLAY_OVERRIDES.get(schedule_person_key(name))
    if display_override:
        return display_override
    position = str(position or "").strip()
    if position:
        return position
    override = report_role_name_override(name)
    return override or ""


def schedule_position_abbreviation(label: str) -> str:
    """Skraca stanowisko do kodu widocznego w grafiku."""
    label = str(label or "").strip()
    if not label:
        return ""
    if label in SCHEDULE_ROLE_ABBREVIATIONS:
        return SCHEDULE_ROLE_ABBREVIATIONS[label]
    compact = re.sub(r"\s+", "", label)
    if 1 < len(compact) <= 4 and compact.upper() == compact:
        return compact
    hay = normalize_search_text(label)
    for token, abbrev in SCHEDULE_POSITION_SPECIAL_ABBREVIATIONS.items():
        if token in hay:
            return abbrev
    role = report_role_for(label)
    return SCHEDULE_ROLE_ABBREVIATIONS.get(role, label)


def schedule_employee_display_position(name: str, position: str) -> str:
    """Skrót stanowiska w tabeli — dla Managerów często z override po nazwisku."""
    return schedule_position_abbreviation(schedule_employee_full_position(name, position))


def schedule_position_modifier_for_label(label: str, *, name: str = "") -> str:
    full = schedule_employee_full_position(name, label) if name else str(label or "").strip()
    role = report_role_for(full or label)
    return SCHEDULE_POSITION_MODIFIER_BY_ROLE.get(role, "emp-position--other")


def schedule_position_watermark_label(label: str, *, name: str = "") -> str:
    full = schedule_employee_full_position(name, label) if name else str(label or "").strip()
    if full:
        return full
    role = report_role_for(label)
    return role or ""


def schedule_position_modifier_class(abbrev: str) -> str:
    token = str(abbrev or "").strip().upper()
    role = SCHEDULE_POSITION_ABBREV_TO_ROLE.get(token)
    if role:
        return SCHEDULE_POSITION_MODIFIER_BY_ROLE.get(role, "emp-position--other")
    if token == "A":
        return "emp-position--animator"
    if token == "OU":
        return "emp-position--organizer"
    return "emp-position--other"


def schedule_position_watermark_html(
    text: str,
    *,
    position_full: str = "",
    name: str = "",
    class_name: str = "emp-position",
) -> str:
    text_value = str(text or "").strip()
    if not text_value:
        return ""
    label = position_full or text_value
    modifier = schedule_position_modifier_for_label(label, name=name)
    full_value = schedule_position_watermark_label(label, name=name)
    title_attr = (
        f' title="{escape(full_value)}"'
        if full_value and full_value != text_value
        else ""
    )
    return (
        f'<span class="{class_name} {modifier}" aria-hidden="true"{title_attr}>'
        f"{escape(text_value)}"
        f"</span>"
    )


def schedule_position_span(
    abbrev: str,
    full: str = "",
    *,
    class_name: str = "emp-position",
    name: str = "",
) -> str:
    abbrev_value = str(abbrev or "").strip()
    if not abbrev_value:
        return ""
    return schedule_position_watermark_html(
        abbrev_value,
        position_full=full or abbrev_value,
        name=name,
        class_name=class_name,
    )


def schedule_roster_identity_html(
    name: str,
    *,
    position: str = "",
    position_full: str = "",
) -> str:
    watermark = schedule_position_watermark_label(position_full or position, name=name)
    position_html = schedule_position_watermark_html(
        watermark,
        position_full=position_full or position,
        name=name,
    )
    return (
        f'<div class="roster-identity emp-name-wrap emp-name-wrap--roster" title="{escape(name)}">'
        f'<span class="emp-name roster-name">{escape(name)}</span>'
        f"{position_html}"
        f"</div>"
    )


def schedule_grafik_name_cell_html(
    short_name: str,
    *,
    name_title: str = "",
    position: str = "",
    position_full: str = "",
    employee_name: str = "",
    wrap_class: str = "emp-name-wrap",
    watermark_mode: str = "abbrev",
) -> str:
    title = name_title or short_name
    label = position_full or position
    if watermark_mode == "full":
        watermark = schedule_position_watermark_label(label, name=employee_name)
    else:
        watermark = position
    position_html = schedule_position_watermark_html(
        watermark,
        position_full=label,
        name=employee_name,
    )
    wrap_class = str(wrap_class or "emp-name-wrap").strip() or "emp-name-wrap"
    return (
        f'<div class="{wrap_class}" title="{escape(title)}">'
        f'<span class="emp-name">{escape(short_name)}</span>'
        f"{position_html}"
        f"</div>"
    )


def report_role_for_shift(name: object, position: object, shift_raw: object) -> str:
    if shift_has_bar_marker(shift_raw):
        return "Bar"
    override = report_role_name_override(name)
    if override:
        return override
    return report_role_for(position)


def report_role_for(position: object) -> str:
    """Mapuje stanowisko z arkusza na linię raportu kierownika zmiany."""
    hay = normalize_search_text(position)
    if not hay:
        return "Inne"
    if "kierownik animator" in hay:
        return "Kierownik animatorów"
    if "organizator" in hay:
        return "Organizator urodzin"
    if "kierownik zmiany" in hay or "administrator" in hay or hay == "admin":
        return "Administrator"
    if "barman" in hay or hay == "bar":
        return "Bar"
    if "animator" in hay or "aniamator" in hay:
        return "Animatorzy"
    if "kelner" in hay:
        return "Kelnerzy"
    if "pracownia" in hay:
        return "Pracownia kreatywna"
    if "sprzataj" in hay or "zmywak" in hay:
        return "Sprzątaczki + zmywak"
    if "recepcja" in hay:
        return "Recepcja"
    if any(token in hay for token in ("kucharz", "cukiernik", "pizza", "soue", "kuchnia", "szef kuchni")):
        return "Kuchnia"
    if any(token in hay for token in ("dyrekcja", "dyrektor", "zastepca")):
        return "Dyrekcja"
    if "ksiegow" in hay:
        return "Księgowość"
    if "konserwator" in hay:
        return "Konserwator"
    if hay == "hr" or hay.startswith("hr ") or hay.endswith(" hr"):
        return "Kierownik HR"
    return "Inne"


def is_half_shift(hours: float) -> bool:
    """Pół zmiana: do 6 godzin włącznie; powyżej 6 — cała zmiana."""
    return hours <= SHIFT_REPORT_HALF_HOURS_MAX


def format_staff_count(total: int, full: int, half: int) -> str:
    if total <= 0:
        return "0"
    if half <= 0:
        return str(total)
    if full <= 0:
        return f"{total} ({half} 1/2)"
    return f"{total} ({half} 1/2, {full} cały)"


def reservation_metrics_for_day(
    target_day: date,
    rows: list[DbRow] | None = None,
) -> dict[str, int]:
    if rows is None:
        rows = get_reservations_for_day(target_day)
    active = [row for row in rows if row["status"] == "active"]
    return {
        "banquets": sum(1 for row in active if not is_table_reservation(row)),
        "tables": sum(1 for row in active if is_table_reservation(row)),
        "animations": sum(len(animations_from_row(row)) for row in active),
        "pinatas": sum(1 for row in active if is_enabled(row, "pinata_enabled")),
        "workshops": sum(1 for row in active if is_enabled(row, "culinary_workshops_enabled")),
    }


def organizer_metrics_from_rows(rows: list[DbRow]) -> dict[str, int]:
    active = [row for row in rows if str(row.get("status") or "") == "active"]
    return {
        "banquets": sum(1 for row in active if not is_table_reservation(row)),
        "tables": sum(1 for row in active if is_table_reservation(row)),
        "cakes": sum(1 for row in active if is_enabled(row, "cake_enabled")),
        "animations": sum(len(animations_from_row(row)) for row in active),
        "pinatas": sum(1 for row in active if is_enabled(row, "pinata_enabled")),
        "cooperation": sum(1 for row in active if is_enabled(row, "cooperation_enabled")),
    }


def next_calendar_month(target_day: date) -> date:
    if target_day.month == 12:
        return date(target_day.year + 1, 1, 1)
    return date(target_day.year, target_day.month + 1, 1)


def format_organizer_report_text(report_day: date) -> str:
    """Raport z pracy organizatora: liczy wpisy po created_at w danym dniu/miesiącu."""
    tomorrow = report_day + timedelta(days=1)
    created_today = get_reservations_created_on_day(report_day)
    today_metrics = organizer_metrics_from_rows(created_today)
    # Z dzisiejszej pracy: wpisy z datą imprezy na jutro.
    created_for_tomorrow = [
        row for row in created_today if reservation_party_date(row) == tomorrow
    ]
    tomorrow_metrics = organizer_metrics_from_rows(created_for_tomorrow)
    month_a = date(report_day.year, report_day.month, 1)
    month_b = next_calendar_month(report_day)
    month_a_metrics = organizer_metrics_from_rows(
        get_reservations_for_month(month_a.year, month_a.month)
    )
    month_b_metrics = organizer_metrics_from_rows(
        get_reservations_for_month(month_b.year, month_b.month)
    )

    def month_block(month_day: date, metrics: dict[str, int]) -> list[str]:
        label = f"{MONTH_STANDALONE_LABELS[month_day.month - 1]} {month_day.year}"
        return [
            label.capitalize() + ":",
            f"Bankiety: {metrics['banquets']}",
            f"Rezerwacje: {metrics['tables']}",
            f"Współpraca: {metrics['cooperation']}",
            f"Torty: {metrics['cakes']}",
            f"Animacje: {metrics['animations']}",
            f"Piniata: {metrics['pinatas']}",
        ]

    lines = [
        f"Raport: {report_day.strftime('%d.%m.%Y')}",
        "Liczba odpowiedzi i wysłanych ofert (mail): ",
        f"Rezerwacje: {today_metrics['tables']}",
        f"Wstępne zamówienia bankietów: {today_metrics['banquets']}",
        "Potwierdzone bankiety: ",
        f"Torty: {today_metrics['cakes']}",
        f"Animacje: {today_metrics['animations']}",
        f"Piniata: {today_metrics['pinatas']}",
        "",
        "Jutro:",
        f"Bankiety: {tomorrow_metrics['banquets']}",
        f"Rezerwacje: {tomorrow_metrics['tables']}",
        f"Współpraca: {tomorrow_metrics['cooperation']}",
        "",
        *month_block(month_a, month_a_metrics),
        "",
        *month_block(month_b, month_b_metrics),
    ]
    return "\n".join(lines)


def build_shift_report(
    iso_date: str,
    entries: list[dict[str, object]],
    *,
    reservation_rows: list[DbRow] | None = None,
    compact_entries: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Buduje raport obsady + metryki rezerwacji dla jednego dnia (wszystkie działy)."""
    target_day = date.fromisoformat(iso_date)
    roles: dict[str, dict[str, int]] = {
        label: {"full": 0, "half": 0} for label in SHIFT_REPORT_ROLE_ORDER
    }
    people = compact_entries if compact_entries is not None else compact_schedule_entries(entries)
    for person in people:
        name = str(person.get("name") or "").strip()
        if not name:
            continue
        days_map = person.get("days", {})
        cell = days_map.get(iso_date, {}) if isinstance(days_map, dict) else {}
        if not isinstance(cell, dict):
            continue
        shift_raw = cell.get("shift") or ""
        shift = normalize_schedule_shift(shift_raw)
        if not schedule_shift_is_work(shift):
            continue
        hours_value = cell_hours_value(shift, cell.get("hours"))
        role = report_role_for_shift(name, person.get("position"), shift_raw)
        bucket = roles.setdefault(role, {"full": 0, "half": 0})
        if is_half_shift(hours_value):
            bucket["half"] += 1
        else:
            bucket["full"] += 1
    konserwator = roles.setdefault("Konserwator", {"full": 0, "half": 0})
    konserwator["full"] += SHIFT_REPORT_STANDING_KONSERWATOR
    total_people = sum(bucket["full"] + bucket["half"] for bucket in roles.values())
    return {
        "date": iso_date,
        "metrics": reservation_metrics_for_day(target_day, reservation_rows),
        "roles": roles,
        "total_people": total_people,
    }


def format_shift_report_text(report: dict[str, object]) -> str:
    metrics = report.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    roles = report.get("roles", {})
    if not isinstance(roles, dict):
        roles = {}

    def role_line(label: str, full: int, half: int) -> str:
        emoji = SHIFT_REPORT_ROLE_EMOJI.get(label, "➕")
        return f"{emoji} {label} - {format_staff_count(full + half, full, half)}"

    lines = [
        "Dzień dobry 👋",
        "",
        f"🎂 Bankiety – {int(metrics.get('banquets', 0))}",
        f"🪑 Rezerwacje – {int(metrics.get('tables', 0))}",
        f"🎭 Animacje – {int(metrics.get('animations', 0))}",
        f"🪅 Piniata – {int(metrics.get('pinatas', 0))}",
        f"🧁 Warsztaty (MK) – {int(metrics.get('workshops', 0))}",
        "",
    ]
    for label in SHIFT_REPORT_ROLE_ORDER:
        if label == "Inne":
            continue
        bucket = roles.get(label, {"full": 0, "half": 0})
        if not isinstance(bucket, dict):
            bucket = {"full": 0, "half": 0}
        full = int(bucket.get("full", 0))
        half = int(bucket.get("half", 0))
        lines.append(role_line(label, full, half))
    other = roles.get("Inne", {"full": 0, "half": 0})
    if isinstance(other, dict):
        other_full = int(other.get("full", 0))
        other_half = int(other.get("half", 0))
        if other_full + other_half > 0:
            lines.append(role_line("Inne", other_full, other_half))
    lines.append("")
    lines.append(f"👥 Razem: {int(report.get('total_people', 0))} osób na zmianie")
    return "\n".join(lines)


def build_shift_reports_for_dates(
    iso_dates: list[str],
    entries: list[dict[str, object]],
) -> dict[str, str]:
    reports: dict[str, str] = {}
    if not iso_dates:
        return reports
    schedule_loaded_at = float(_SCHEDULE_CACHE.get("loaded_at", 0.0))
    cached_reports = _SHIFT_REPORT_CACHE.get("reports")
    if float(_SHIFT_REPORT_CACHE.get("schedule_loaded_at", 0.0)) != schedule_loaded_at:
        cached_reports = {}
        _SHIFT_REPORT_CACHE["schedule_loaded_at"] = schedule_loaded_at
        _SHIFT_REPORT_CACHE["reports"] = cached_reports
    if not isinstance(cached_reports, dict):
        cached_reports = {}
        _SHIFT_REPORT_CACHE["reports"] = cached_reports

    missing_dates = [iso for iso in iso_dates if iso not in cached_reports]
    if missing_dates:
        reservation_cache = get_reservations_for_days(missing_dates)
        compact = compact_schedule_entries(entries)
        for iso in missing_dates:
            report = build_shift_report(
                iso,
                entries,
                reservation_rows=reservation_cache.get(iso, []),
                compact_entries=compact,
            )
            cached_reports[iso] = format_shift_report_text(report)

    for iso in iso_dates:
        text = cached_reports.get(iso)
        if isinstance(text, str):
            reports[iso] = text
    return reports


def shift_period_bucket(shift: object) -> str:
    """System zmianowy iKids (z Google Sheets — zakresy godzin, nie litery D/N).

    Na podstawie realnych wpisów (animatorzy: 9-15, 15-21, 9-21, 13-21…):
    - DNIÓWKA (morning): start < 12  → 9-15, 9-16, 10-12, 9-21…
    - POPOŁUDNIÓWKA (afternoon): 12 ≤ start < 16 → 12-20, 13-21, 15-21…
    - NOCKA (evening): start ≥ 16 → 16-21…
    """
    start = parse_shift_start_hour(shift)
    if start is None:
        return "afternoon"
    if start < 12:
        return "morning"
    if start < 16:
        return "afternoon"
    return "evening"


def parse_hours_number(value: object) -> float:
    try:
        return float(str(value or "").replace(",", ".").strip() or 0)
    except ValueError:
        return 0.0


def build_grafik_day_model(
    entries: list[dict[str, object]],
    days: list[date],
    *,
    month: str = "",
    week: str = "",
) -> dict[str, object]:
    """Buduje model dni × osoby (miesiąc albo tydzień) w układzie grafik4600."""
    compact = compact_schedule_entries(entries)
    compact.sort(key=lambda item: normalize_search_text(item.get("name", "")))
    today = current_app_date()

    employees = [
        {
            "name": str(item.get("name") or "").strip(),
            "position": schedule_employee_display_position(
                str(item.get("name") or "").strip(),
                str(item.get("position") or "").strip(),
            ),
            "position_full": schedule_employee_full_position(
                str(item.get("name") or "").strip(),
                str(item.get("position") or "").strip(),
            ),
            "display_name": str(item.get("name") or "").strip(),
            "total": item.get("total", 0.0),
        }
        for item in compact
        if str(item.get("name") or "").strip()
    ]

    shifts_by_date: dict[str, dict[str, dict[str, str]]] = {}
    day_totals: dict[str, dict[str, object]] = {}
    schedule_rows: list[dict[str, object]] = []

    for day in days:
        iso = day.isoformat()
        day_shifts: dict[str, dict[str, str]] = {}
        people_count = 0
        hours_sum = 0.0
        for person in compact:
            name = str(person.get("name") or "").strip()
            if not name:
                continue
            days_map = person.get("days", {})
            cell = days_map.get(iso, {}) if isinstance(days_map, dict) else {}
            if not isinstance(cell, dict):
                continue
            shift = normalize_schedule_shift(cell.get("shift") or "")
            hours = str(cell.get("hours") or "").strip()
            if not schedule_shift_is_work(shift):
                continue
            day_shifts[name] = {
                "shift": shift,
                "hours": hours,
                "position": schedule_employee_display_position(
                    name,
                    str(person.get("position") or "").strip(),
                ),
                "position_full": schedule_employee_full_position(
                    name,
                    str(person.get("position") or "").strip(),
                ),
                "position_modifier": schedule_position_modifier_for_label(
                    str(person.get("position") or "").strip(),
                    name=name,
                ),
                "position_watermark": schedule_position_watermark_label(
                    str(person.get("position") or "").strip(),
                    name=name,
                ),
            }
            people_count += 1
            hours_sum += parse_hours_number(hours)
        shifts_by_date[iso] = day_shifts
        day_totals[iso] = {
            "people": people_count,
            "hours": hours_sum,
            "hours_label": format_schedule_total(hours_sum),
        }
        schedule_rows.append(
            {
                "date": iso,
                "dd": f"{day.day:02d}",
                "abbr": WEEKDAY_LABELS[day.weekday()][:3],
                "is_off": day.weekday() >= 5,
                "is_today": day == today,
            }
        )

    return {
        "month": month,
        "week": week,
        "employees": employees,
        "schedule_rows": schedule_rows,
        "shifts_by_date": shifts_by_date,
        "day_totals": day_totals,
    }


def build_grafik_month_model(entries: list[dict[str, object]], month: str) -> dict[str, object]:
    """Buduje model miesiąca w układzie grafik4600: dni × osoby."""
    return build_grafik_day_model(entries, schedule_month_days(month), month=month)


def build_grafik_week_model(
    entries: list[dict[str, object]],
    week_start: str,
    *,
    month: str = "",
) -> dict[str, object]:
    """Buduje model tygodnia w tym samym układzie co miesiąc (7 dni × osoby)."""
    return build_grafik_day_model(
        entries,
        schedule_week_days(week_start),
        month=month,
        week=week_start,
    )


def schedule_adjacent_month(month: str, delta: int) -> str:
    try:
        year_value, month_value = (int(part) for part in month.split("-", 1))
        first_day = date(year_value, month_value, 1)
    except ValueError:
        return month
    if delta < 0:
        target = first_day - timedelta(days=1)
    else:
        if month_value == 12:
            target = date(year_value + 1, 1, 1)
        else:
            target = date(year_value, month_value + 1, 1)
    return target.strftime("%Y-%m")


SCHEDULE_CHEVRON_LEFT = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M14.5 6.5 9 12l5.5 5.5" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)
SCHEDULE_CHEVRON_RIGHT = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M9.5 6.5 15 12l-5.5 5.5" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)


def schedule_day_nav_html(initial_display: str) -> str:
    return (
        '<div class="schedule-day-nav" aria-label="Nawigacja dnia">'
        f'<button type="button" id="shift-prev-day" class="schedule-nav-arrow" aria-label="Poprzedni dzień">{SCHEDULE_CHEVRON_LEFT}</button>'
        '<div class="shift-date-info">'
        '<strong id="shift-date-label">DZISIAJ</strong>'
        f'<span id="shift-date-display">{escape(initial_display)}</span>'
        "</div>"
        f'<button type="button" id="shift-next-day" class="schedule-nav-arrow" aria-label="Następny dzień">{SCHEDULE_CHEVRON_RIGHT}</button>'
        "</div>"
    )


def schedule_shift_report_button_html() -> str:
    return (
        '<div class="shift-report-panel">'
        '<button type="button" id="shift-report-copy" class="shift-report-copy" aria-label="Kopiuj raport zmiany do WhatsApp">'
        '<span class="shift-report-copy__label">Raport zmiany</span>'
        '<span class="shift-report-copy__icon" aria-hidden="true">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="9" y="9" width="13" height="13" rx="2"/>'
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
        "</svg>"
        "</span>"
        "</button>"
        "</div>"
    )


def schedule_day_nav_cluster_html(initial_display: str) -> str:
    """Zachowane dla kompatybilności — używaj assemble_schedule_toolbar."""
    return schedule_day_nav_html(initial_display)


def assemble_schedule_toolbar(
    controls_html: str,
    initial_display: str,
) -> str:
    """DZISIAJ na środku paska dnia, raport po prawej (okres jest wyżej)."""
    day_nav = schedule_day_nav_html(initial_display)
    report_panel = schedule_shift_report_button_html()
    center_block = f'<div class="schedule-controls-center">{day_nav}</div>'
    right_block = f'<div class="schedule-controls-side schedule-controls-right">{report_panel}</div>'
    marker = "<!--schedule-controls-end-->"
    controls_open = '<div class="schedule-controls">'
    toolbar_tail = f"{center_block}{right_block}</div>"
    if not controls_html.strip():
        return f'<div class="schedule-controls">{center_block}{right_block}</div>'
    patched = controls_html
    if controls_open in patched:
        if marker in patched:
            return patched.replace(marker, f"{toolbar_tail}{marker}", 1)
        return patched + toolbar_tail
    return controls_html + day_nav + report_panel


def build_staff_summary_by_date(
    iso_dates: list[str],
    entries: list[dict[str, object]],
    *,
    department: str = "",
) -> dict[str, list[dict[str, object]]]:
    """Liczba osób na zmianie wg stanowiska w wybranym dziale."""
    department = department if department in dict(SCHEDULE_DEPARTMENTS) else ""
    compact = compact_schedule_entries(entries)
    dept_by_person: dict[tuple[str, str], str] = {}
    for entry in entries:
        person_key = (str(entry.get("name") or ""), str(entry.get("position") or ""))
        dept_by_person[person_key] = str(entry.get("department") or "serwis")

    result: dict[str, list[dict[str, object]]] = {}
    for iso in iso_dates:
        position_counts: dict[str, int] = {}
        for person in compact:
            name = str(person.get("name") or "").strip()
            position = str(person.get("position") or "").strip()
            if not name:
                continue
            days_map = person.get("days", {})
            if not isinstance(days_map, dict):
                continue
            cell = days_map.get(iso, {})
            if not isinstance(cell, dict):
                continue
            shift_raw = str(cell.get("shift") or "").strip()
            if not schedule_shift_is_work(shift_raw):
                continue
            dept = dept_by_person.get((name, position), "serwis")
            if department and dept != department:
                continue
            label = schedule_employee_full_position(name, position) or report_role_for_shift(
                name, position, shift_raw
            )
            position_counts[label] = position_counts.get(label, 0) + 1

        items = sorted(
            [{"name": pos_name, "count": count} for pos_name, count in position_counts.items()],
            key=lambda item: (-int(item["count"]), normalize_search_text(item["name"])),
        )
        result[iso] = items
    return result


def render_staff_summary_html(positions: list[dict[str, object]]) -> str:
    if not positions:
        return '<p class="staff-summary-empty muted">brak przypisań</p>'
    items: list[str] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        name = escape(str(item.get("name") or ""))
        count = int(item.get("count") or 0)
        if not name or count <= 0:
            continue
        items.append(
            '<span class="staff-summary-item">'
            f'<span class="staff-summary-position">{name}</span>'
            f'<span class="staff-summary-count">{count}</span>'
            "</span>"
        )
    if not items:
        return '<p class="staff-summary-empty muted">brak przypisań</p>'
    return f'<div class="staff-summary-grid">{"".join(items)}</div>'


def schedule_roster_actions_html(summary_html: str = "") -> str:
    return (
        '<div class="grafiki-roster-head">'
        f'<div id="shifts-summary" class="staff-summary">{summary_html}</div>'
        "</div>"
    )


def render_grafik_mobile_month_calendar(
    date_columns: list[dict[str, object]],
    people_by_date: dict[str, int],
) -> str:
    """Kompaktowa siatka kalendarza na telefon (7 kolumn, dni miesiąca)."""
    if not date_columns:
        return ""

    weekday_headers = "".join(
        f'<span class="grafiki-mobile-cal-weekday">{escape(label)}</span>'
        for label in WEEKDAY_LABELS
    )

    first_iso = str(date_columns[0].get("date") or "")
    try:
        pad_count = date.fromisoformat(first_iso).weekday()
    except ValueError:
        pad_count = 0
    padding = '<span class="grafiki-mobile-cal-pad" aria-hidden="true"></span>' * pad_count

    day_cells: list[str] = []
    for day in date_columns:
        iso = str(day.get("date") or "")
        dd = escape(str(day.get("dd") or ""))
        abbr = escape(str(day.get("abbr") or ""))
        people = int(people_by_date.get(iso, 0))
        classes = ["grafiki-mobile-cal-day"]
        if day.get("is_off"):
            classes.append("off-day")
        if day.get("is_today"):
            classes.append("today")
        if people > 0:
            classes.append("has-shifts")
        count_html = (
            f'<span class="cal-day-count" data-overview-count="{people}">{people}</span>'
            if people > 0
            else '<span class="cal-day-count" data-overview-count="0" hidden></span>'
        )
        day_cells.append(
            f'<button type="button" class="{" ".join(classes)}" data-date="{escape(iso)}" '
            f'data-people-count="{people}" aria-label="{abbr} {dd}">'
            f'<span class="cal-day-num">{dd}</span>'
            f"{count_html}"
            f"</button>"
        )

    return (
        '<div class="grafiki-mobile-month-cal" aria-label="Kalendarz miesiąca">'
        f'<div class="grafiki-mobile-cal-weekdays">{weekday_headers}</div>'
        f'<div class="grafiki-mobile-cal-grid">{padding}{"".join(day_cells)}</div>'
        '<p class="grafiki-mobile-cal-legend muted" data-cal-legend>'
        "Cyfra pod dniem — liczba osób na zmianie"
        "</p>"
        "</div>"
    )


def render_grafik_mobile_month_employee_picker(
    employee_list: list[dict[str, object]],
) -> str:
    """Rozwijana lista pracowników w stylu przypisania kelnera/animatora."""
    sorted_employees = sorted(
        employee_list,
        key=lambda emp: normalize_search_text(str(emp.get("display_name") or emp.get("name") or "")),
    )
    options: list[str] = []
    for emp in sorted_employees:
        name = str(emp.get("name") or "")
        display_name = str(emp.get("display_name") or name)
        if not name:
            continue
        options.append(
            f"""
            <button type="button" class="animator-assign__option" data-staff-option
              data-employee="{escape(name)}"
              data-display-name="{escape(display_name)}"
              data-search="{escape(normalize_search_text(display_name))}">
              <span class="animator-assign__avatar" aria-hidden="true">{escape(staff_initials(display_name))}</span>
              <span class="animator-assign__option-name">{escape(display_name)}</span>
            </button>
            """
        )
    if not options:
        return ""
    return f"""
      <div class="animator-assign grafiki-month-emp-assign" data-month-emp-picker>
        <div class="grafiki-month-emp-combobox">
          <label class="animator-assign__search grafiki-month-emp-search">
            <span class="visually-hidden">Szukaj pracownika</span>
            <input type="search" placeholder="Szukaj pracownika…" autocomplete="off"
              role="combobox" aria-expanded="false" aria-controls="grafik-month-emp-list"
              aria-autocomplete="list" data-month-emp-filter>
          </label>
          <div class="animator-assign__sheet grafiki-month-emp-sheet" id="grafik-month-emp-list" data-month-emp-sheet hidden>
            <div class="animator-assign__list" data-month-emp-list>
              {"".join(options)}
            </div>
          </div>
        </div>
      </div>
    """


def render_grafik_mobile_month(
    *,
    employee_list: list[dict[str, object]],
    date_columns: list[dict[str, object]],
    shifts_by_date: dict[str, dict[str, dict[str, str]]],
    hours_by_emp: dict[str, float],
    people_by_date: dict[str, int],
) -> str:
    del shifts_by_date, hours_by_emp  # używane po stronie JS z window.grafikShiftsData
    calendar_html = render_grafik_mobile_month_calendar(date_columns, people_by_date)
    picker_html = render_grafik_mobile_month_employee_picker(employee_list)
    if not calendar_html and not picker_html:
        return ""
    return (
        '<section class="grafiki-mobile-month" aria-label="Grafik miesięczny — widok mobilny">'
        f"{picker_html}"
        f"{calendar_html}"
        "</section>"
    )


def render_schedule_grafik_grid(
    model: dict[str, object],
    *,
    title: str,
    subtitle: str = "",
    role: str = "manager",
    day: str = "today",
    department: str = "animatorzy",
    months: list[str] | None = None,
    weeks: list[str] | None = None,
    week: str = "",
    view: str = "month",
    controls_html: str = "",
    period_bar_html: str = "",
    home_href: str = "/",
    refresh_href: str = "",
    shift_reports: dict[str, str] | None = None,
    staff_summary_by_date: dict[str, list[dict[str, object]]] | None = None,
) -> str:
    """Widok miesiąc/tydzień grafików w shellu głównej aplikacji."""
    employees = model.get("employees", [])
    schedule_rows = model.get("schedule_rows", [])
    shifts_by_date = model.get("shifts_by_date", {})
    month = str(model.get("month") or "")
    week = str(week or model.get("week") or "")
    view = view if view in {"week", "month"} else "month"
    months = months or []
    weeks = weeks or []

    empty = f"""
<div class="grafiki-filters">{controls_html}</div>
<div class="grafiki-layout">{period_bar_html}</div>
<div class="grafiki-empty">
  <h2>{escape(title)}</h2>
  <p class="muted">{escape(subtitle or "Brak danych dla wybranego zakresu.")}</p>
  <a class="button" href="{escape(home_href)}">Ekran główny</a>
</div>
"""
    if not isinstance(employees, list) or not isinstance(schedule_rows, list) or not schedule_rows:
        return empty

    employee_list = [emp for emp in employees if isinstance(emp, dict) and emp.get("name")]
    if not employee_list:
        return empty

    today_iso = current_app_date().isoformat()
    initial_date = today_iso
    if not any(isinstance(row, dict) and row.get("date") == today_iso for row in schedule_rows):
        first_row = schedule_rows[0] if isinstance(schedule_rows[0], dict) else {}
        initial_date = str(first_row.get("date") or "")

    def people_for_date(iso: str) -> list[dict[str, str]]:
        people: list[dict[str, str]] = []
        row_shifts = shifts_by_date.get(iso, {}) if isinstance(shifts_by_date, dict) else {}
        if not isinstance(row_shifts, dict):
            return people
        for name, cell in row_shifts.items():
            if not isinstance(cell, dict):
                continue
            shift = str(cell.get("shift") or "").strip()
            hours = str(cell.get("hours") or "").strip()
            position = str(cell.get("position") or "").strip()
            if not schedule_shift_is_work(shift):
                continue
            people.append(
                {
                    "name": str(name),
                    "position": position,
                    "position_full": str(cell.get("position_full") or "").strip(),
                    "position_modifier": str(cell.get("position_modifier") or "").strip(),
                    "position_watermark": str(cell.get("position_watermark") or "").strip(),
                    "shift": shift,
                    "hours": hours,
                }
            )

        def sort_key(person: dict[str, str]) -> tuple[float, str]:
            start = parse_shift_start_hour(person.get("shift"))
            return (
                start if start is not None else 99.0,
                normalize_search_text(person.get("name")),
            )

        people.sort(key=sort_key)
        return people

    initial_people = people_for_date(initial_date)
    shift_reports = shift_reports or {}
    staff_summary_by_date = staff_summary_by_date or {}
    initial_summary = staff_summary_by_date.get(initial_date, [])
    if not isinstance(initial_summary, list):
        initial_summary = []

    def roster_html(people: list[dict[str, str]]) -> str:
        if not people:
            return '<p class="muted shifts-roster-empty">brak przypisań</p>'
        items = []
        for person in people:
            label = escape(person.get("name") or "")
            shift = str(person.get("shift") or "").strip()
            hours = str(person.get("hours") or "").strip()
            meta = escape(shift)
            if hours:
                meta += f" · {escape(hours)}h"
            identity_html = schedule_roster_identity_html(
                str(person.get("name") or ""),
                position=str(person.get("position") or ""),
                position_full=str(person.get("position_full") or ""),
            )
            items.append(
                "<li>"
                f"{identity_html}"
                f'<span class="roster-meta">{meta}</span>'
                "</li>"
            )
        return "<ul>" + "".join(items) + "</ul>"

    date_columns = [row for row in schedule_rows if isinstance(row, dict) and row.get("date")]

    date_headers = "".join(
        (
            f'<th class="col-date{" today" if day.get("is_today") else ""}{" off-day" if day.get("is_off") else ""}" '
            f'data-date="{escape(str(day.get("date") or ""))}" title="{escape(str(day.get("date") or ""))}">'
            f'<span class="date-dd">{escape(str(day.get("dd") or ""))}</span>'
            f'<span class="date-abbr">{escape(str(day.get("abbr") or ""))}</span></th>'
        )
        for day in date_columns
    )

    body_rows = []
    hours_by_emp = {str(emp.get("name") or ""): 0.0 for emp in employee_list}
    hours_grand_total = 0.0
    people_by_date = {str(day.get("date") or ""): 0 for day in date_columns}

    for emp in employee_list:
        name = str(emp.get("name") or "")
        display_name = str(emp.get("display_name") or name)
        position = str(emp.get("position") or "")
        position_full = str(emp.get("position_full") or "")
        cells = []
        for day in date_columns:
            iso = str(day.get("date") or "")
            row_shifts = shifts_by_date.get(iso, {}) if isinstance(shifts_by_date, dict) else {}
            if not isinstance(row_shifts, dict):
                row_shifts = {}
            cell = row_shifts.get(name, {})
            shift = ""
            hours = ""
            if isinstance(cell, dict):
                shift = str(cell.get("shift") or "").strip()
                hours = str(cell.get("hours") or "").strip()
            is_work = schedule_shift_is_work(shift)
            display = escape(shift) if is_work else ""
            hours_value = cell_hours_value(shift, hours) if is_work else 0.0
            if is_work and hours_value > 0:
                hours_by_emp[name] = hours_by_emp.get(name, 0.0) + hours_value
                hours_grand_total += hours_value
            if is_work:
                people_by_date[iso] = people_by_date.get(iso, 0) + 1
            title_attr = f"{shift} ({format_schedule_total(hours_value)}h)" if is_work and hours_value else shift
            today_cell = " today" if day.get("is_today") else ""
            off_cell = " off-day" if day.get("is_off") else ""
            cell_class = f"col-date slot{today_cell}{off_cell}"
            if is_work:
                cell_class += " custom-shift"
            cells.append(
                f'<td class="{cell_class}" data-date="{escape(iso)}" data-employee="{escape(name)}" '
                f'data-value="{escape(shift)}" data-hours="{escape(hours)}" '
                f'data-hours-value="{hours_value}" title="{escape(title_attr)}">'
                f'{"<span class=\"shift-chip\">" + display + "</span>" if display else ""}'
                f"</td>"
            )
        short_name = schedule_grafik_short_name(display_name)
        name_title = display_name
        if position_full or position:
            name_title = f"{display_name} · {position_full or position}"
        name_cell_html = schedule_grafik_name_cell_html(
            short_name,
            name_title=name_title,
            position=position,
            position_full=position_full,
            employee_name=name,
        )
        body_rows.append(
            f'<tr data-employee="{escape(name)}">'
            f'<th class="col-name" scope="row">'
            f"{name_cell_html}"
            f"</th>"
            f'{"".join(cells)}'
            f'<td class="col-summary hours-total-cell">{escape(format_schedule_total(hours_by_emp.get(name, 0.0)))}</td>'
            "</tr>"
        )

    people_total_cells = "".join(
        f'<td class="col-date{" today" if day.get("is_today") else ""}" data-date="{escape(str(day.get("date") or ""))}">'
        f'{people_by_date.get(str(day.get("date") or ""), 0)}</td>'
        for day in date_columns
    )
    hours_total_row = (
        '<tr class="hours-total-row people-total-row">'
        '<th class="col-name" scope="row">Razem</th>'
        f"{people_total_cells}"
        f'<td class="col-summary">{escape(format_schedule_total(hours_grand_total))}</td>'
        "</tr>"
    )

    shifts_json = json.dumps(shifts_by_date, ensure_ascii=False)
    shift_reports_json = json.dumps(shift_reports, ensure_ascii=False)
    staff_summary_json = json.dumps(staff_summary_by_date, ensure_ascii=False)
    dates_json = json.dumps(
        [str(day.get("date")) for day in date_columns if day.get("date")],
        ensure_ascii=False,
    )
    try:
        initial_display = datetime.fromisoformat(initial_date).strftime("%d.%m.%Y")
    except ValueError:
        initial_display = format_date(initial_date)

    toolbar = assemble_schedule_toolbar(controls_html, initial_display)
    tabs_html = ""
    controls_only = toolbar
    controls_marker = '<div class="schedule-controls">'
    if controls_marker in toolbar:
        split_at = toolbar.index(controls_marker)
        tabs_html = toolbar[:split_at].strip()
        controls_only = toolbar[split_at:]

    mobile_month_html = ""
    if view == "month":
        mobile_month_html = render_grafik_mobile_month(
            employee_list=employee_list,
            date_columns=date_columns,
            shifts_by_date=shifts_by_date if isinstance(shifts_by_date, dict) else {},
            hours_by_emp=hours_by_emp,
            people_by_date=people_by_date,
        )
    mobile_month_attr = ' data-mobile-month="1"' if view == "month" else ""

    return f"""
<div class="grafiki-filters">
  {tabs_html}
  {controls_only}
  <section class="grafiki-roster">
    {schedule_roster_actions_html(render_staff_summary_html(initial_summary))}
    <div id="shifts-roster" class="shifts-roster">{roster_html(initial_people)}</div>
  </section>
</div>

<div class="grafiki-layout">
  {period_bar_html}
  <div class="grafiki-stack">
    <section class="grafiki-table-panel"{mobile_month_attr}>
      {mobile_month_html}
      <div class="table-wrap">
        <table class="grafiki-table" id="grafik"
               data-view="{escape(view)}"
               data-month="{escape(month)}"
               data-week="{escape(week)}"
               data-initial-date="{escape(initial_date)}"
               data-emp-count="{len(employee_list)}"
               data-days-count="{len(date_columns)}">
          <thead>
            <tr>
              <th class="col-name">Imię i nazwisko</th>
              {date_headers}
              <th class="col-summary">Σ h</th>
            </tr>
          </thead>
          <tbody>
            {''.join(body_rows)}
          </tbody>
          <tfoot>
            {hours_total_row}
          </tfoot>
        </table>
      </div>
    </section>
  </div>
</div>
<script>
  window.grafikShiftsData = {shifts_json};
  window.grafikMonthDates = {dates_json};
  window.grafikShiftReports = {shift_reports_json};
  window.grafikStaffSummary = {staff_summary_json};
</script>
"""


def render_schedule_period_bar(
    *,
    role: str,
    day: str,
    selected_department: str,
    selected_month: str,
    selected_week: str,
    selected_view: str,
    months: list[str],
    weeks: list[str],
    entries: list[dict[str, object]] | None = None,
) -> str:
    chevron_left = SCHEDULE_CHEVRON_LEFT
    chevron_right = SCHEDULE_CHEVRON_RIGHT

    def nav_in_list(current: str, items: list[str], delta: int) -> str:
        if not current or not items or current not in items:
            return ""
        index = items.index(current) + delta
        if index < 0 or index >= len(items):
            return ""
        return items[index]

    def nav_arrow(*, direction: str, href: str, aria_label: str) -> str:
        icon = chevron_left if direction == "prev" else chevron_right
        if href:
            return (
                f'<a class="schedule-period-arrow schedule-control" href="{escape(href)}" '
                f'aria-label="{escape(aria_label)}">{icon}</a>'
            )
        return (
            f'<span class="schedule-period-arrow is-disabled" aria-disabled="true" '
            f'aria-label="{escape(aria_label)}">{icon}</span>'
        )

    if selected_view == "week":
        period_label = schedule_week_label(selected_week) if selected_week else "Tydzień"
        prev_week = nav_in_list(selected_week, weeks, -1)
        next_week = nav_in_list(selected_week, weeks, 1)
        prev_month = selected_month
        next_month = selected_month
        if not prev_week and entries is not None:
            adj_month = nav_in_list(selected_month, months, -1)
            if adj_month:
                adj_weeks = schedule_available_weeks(entries, adj_month, selected_department)
                if adj_weeks:
                    prev_week = adj_weeks[-1]
                    prev_month = adj_month
        if not next_week and entries is not None:
            adj_month = nav_in_list(selected_month, months, 1)
            if adj_month:
                adj_weeks = schedule_available_weeks(entries, adj_month, selected_department)
                if adj_weeks:
                    next_week = adj_weeks[0]
                    next_month = adj_month
        prev_href = (
            schedule_url(
                role=role,
                day=day,
                department=selected_department,
                month=prev_month,
                week=prev_week,
                view="week",
            )
            if prev_week
            else ""
        )
        next_href = (
            schedule_url(
                role=role,
                day=day,
                department=selected_department,
                month=next_month,
                week=next_week,
                view="week",
            )
            if next_week
            else ""
        )
        prev_aria = "Poprzedni tydzień"
        next_aria = "Następny tydzień"
    else:
        period_label = schedule_month_label(selected_month) if selected_month else "Miesiąc"
        prev_month = nav_in_list(selected_month, months, -1)
        next_month = nav_in_list(selected_month, months, 1)
        prev_href = (
            schedule_url(
                role=role,
                day=day,
                department=selected_department,
                month=prev_month,
                week="",
                view="month",
            )
            if prev_month
            else ""
        )
        next_href = (
            schedule_url(
                role=role,
                day=day,
                department=selected_department,
                month=next_month,
                week="",
                view="month",
            )
            if next_month
            else ""
        )
        prev_aria = "Poprzedni miesiąc"
        next_aria = "Następny miesiąc"

    view_links = "".join(
        f'<a class="schedule-control{" is-active" if view == selected_view else ""}" '
        f'href="{escape(schedule_url(role=role, day=day, department=selected_department, month=selected_month, week=selected_week, view=view))}">{label}</a>'
        for view, label in (("week", "Tydzień"), ("month", "Miesiąc"))
    )

    return f"""
    <div class="schedule-period-bar">
      <div class="schedule-period-nav-card" aria-label="Nawigacja okresu">
        {nav_arrow(direction="prev", href=prev_href, aria_label=prev_aria)}
        <span class="schedule-period-label">{escape(period_label)}</span>
        {nav_arrow(direction="next", href=next_href, aria_label=next_aria)}
      </div>
      <div class="schedule-view-toggle" role="group" aria-label="Widok">
        {view_links}
      </div>
    </div>
    """


def render_schedule_controls(
    *,
    role: str,
    day: str,
    selected_department: str,
    selected_month: str,
    selected_week: str,
    selected_view: str,
    months: list[str],
    weeks: list[str],
    home_href: str = "/",
    entries: list[dict[str, object]] | None = None,
) -> str:
    day = normalize_day(day)
    home_url = home_href or hub_home_href(day)
    department_tabs = render_grafiki_department_tabs(
        role=role,
        day=day,
        selected_department=selected_department,
        selected_month=selected_month,
        selected_week=selected_week,
        selected_view=selected_view,
        home_href=home_url,
    )

    return f"""
    {department_tabs}
    <div class="schedule-controls">
      <!--schedule-controls-end-->
    </div>
    """


def grafik4600_assets() -> str:
    """Style ekranu grafików w języku wizualnym głównej aplikacji."""
    return r"""
<style>
  .grafiki-filters {
    margin-bottom: 0;
    display: grid;
    gap: 12px;
    min-width: 0;
    max-width: 100%;
  }

  .grafiki-layout {
    display: grid;
    gap: 12px;
    width: 100%;
    min-width: 0;
    max-width: 100%;
    margin-top: 14px;
  }

  .grafiki-stack {
    display: grid;
    gap: 12px;
    width: 100%;
    min-width: 0;
    max-width: 100%;
  }

  .grafiki-layout .schedule-period-bar {
    display: grid;
    gap: 10px;
    width: 100%;
    min-width: 0;
    max-width: 100%;
  }

  .grafiki-layout .schedule-period-nav-card {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 6px;
    width: 100%;
    padding: 4px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: #ffffff;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  }

  .grafiki-filters .schedule-controls {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 10px;
    width: 100%;
    align-items: stretch;
  }

  .grafiki-filters .schedule-controls-side {
    display: flex;
    align-items: stretch;
    min-width: 0;
  }

  .grafiki-filters .schedule-controls-center,
  .grafiki-filters .schedule-controls-right {
    display: flex;
    align-items: stretch;
    min-width: 0;
  }

  .grafiki-filters .schedule-day-nav {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    width: 100%;
    min-height: 44px;
    padding: 6px 8px;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
  }

  .grafiki-layout .schedule-period-label {
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 0;
    padding: 10px 8px;
    text-align: center;
    font-size: 0.96rem;
    font-weight: 900;
    color: var(--ink);
    letter-spacing: -0.01em;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .grafiki-layout .schedule-period-arrow {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    min-width: 44px;
    min-height: 44px;
    padding: 0;
    margin: 0;
    border: 0;
    border-radius: 10px;
    background: #f5f5f5;
    color: var(--ink);
    text-decoration: none;
    flex: 0 0 auto;
    line-height: 0;
    font-size: 0;
    font-weight: 800;
    cursor: pointer;
    appearance: none;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
  }

  .grafiki-layout .schedule-period-arrow svg {
    width: 16px;
    height: 16px;
    display: block;
  }

  .grafiki-layout a.schedule-period-arrow:hover {
    background: #ececec;
  }

  .grafiki-layout .schedule-period-arrow.is-disabled {
    opacity: 0.32;
    pointer-events: none;
    cursor: default;
  }

  .grafiki-layout .schedule-view-toggle {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    width: 100%;
  }

  .grafiki-layout .schedule-view-toggle .schedule-control {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 42px;
    padding: 0 14px;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #ffffff;
    color: var(--ink);
    text-decoration: none;
    font-size: 0.88rem;
    font-weight: 800;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  }

  .grafiki-layout .schedule-view-toggle .schedule-control:first-child {
    border-left: 1px solid var(--line);
  }

  .grafiki-layout .schedule-view-toggle .schedule-control:hover {
    background: #f5f5f5;
  }

  .grafiki-layout .schedule-view-toggle .schedule-control.is-active {
    background: #000000;
    border-color: #000000;
    color: #ffffff;
    box-shadow: none;
  }

  .grafiki-filters .schedule-nav-arrow,
  .grafiki-filters button.schedule-nav-arrow {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    width: 34px;
    height: 34px;
    min-width: 34px;
    min-height: 34px;
    max-width: 34px;
    max-height: 34px;
    border: 0;
    border-radius: 50%;
    background: #000;
    color: #fff;
    text-decoration: none;
    flex: 0 0 34px;
    padding: 0;
    margin: 0;
    line-height: 0;
    font-size: 0;
    font-weight: 400;
    cursor: pointer;
    appearance: none;
    overflow: hidden;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
    transition: transform 0.15s ease, opacity 0.15s ease;
  }

  .grafiki-filters .schedule-nav-arrow svg {
    width: 17px;
    height: 17px;
    display: block;
    flex: 0 0 auto;
  }

  .grafiki-filters a.schedule-nav-arrow:hover,
  .grafiki-filters button.schedule-nav-arrow:hover:not(:disabled):not(.is-disabled) {
    background: #000;
    color: #fff;
    transform: scale(1.06);
  }

  .grafiki-filters .schedule-nav-arrow.is-disabled,
  .grafiki-filters .schedule-nav-arrow:disabled,
  .grafiki-filters button.schedule-nav-arrow:disabled {
    opacity: 0.28;
    pointer-events: none;
    cursor: default;
    background: #000;
    color: #fff;
    transform: none;
  }

  .page-schedules .grafiki-roster {
    border: 0;
    background: transparent;
    padding: 0;
    overflow: visible;
    width: 100%;
    display: grid;
    gap: 10px;
    margin-bottom: 2px;
  }

  .page-schedules .grafiki-table-panel {
    border: 0;
    border-radius: 0;
    background: transparent;
    padding: 0;
    overflow: visible;
    width: 100%;
    max-width: 100%;
    min-width: 0;
    margin-left: 0;
    margin-right: 0;
  }

  .roster-head {
    display: none;
  }

  .grafiki-roster-head {
    display: block;
    width: 100%;
    margin-top: 10px;
  }

  .shift-date-info {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    line-height: 1.15;
    width: 6.4rem;
    min-width: 6.4rem;
    text-align: center;
  }

  .shift-date-info strong {
    display: block;
    width: 100%;
    font-size: 0.74rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    text-align: center;
    font-weight: 900;
    color: var(--ink);
  }

  .shift-date-info span {
    display: block;
    width: 100%;
    font-size: 0.92rem;
    font-weight: 700;
    color: var(--ink);
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .roster-title {
    margin: 0;
    font-size: 0.84rem;
    font-weight: 900;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: #64748b;
  }

  .staff-summary-wrap {
    flex: 1 1 240px;
    min-width: 0;
    display: grid;
    gap: 6px;
  }

  .staff-summary {
    display: grid;
    gap: 8px;
  }

  .staff-summary-empty {
    margin: 0;
    font-size: 0.84rem;
  }

  .staff-summary-dept {
    display: grid;
    gap: 5px;
  }

  .staff-summary-dept-label {
    font-size: 0.72rem;
    font-weight: 800;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: color-mix(in srgb, var(--ink) 58%, white);
  }

  .staff-summary-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .staff-summary-item {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35em;
    padding: 5px 10px;
    border-radius: 10px;
    background: #ffffff;
    border: 1px solid color-mix(in srgb, var(--line) 90%, transparent);
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    font-size: 0.84rem;
    line-height: 1.2;
  }

  .staff-summary-position {
    font-weight: 700;
    color: var(--ink);
  }

  .staff-summary-count {
    font-weight: 800;
    color: var(--ink);
    font-size: inherit;
    font-variant-numeric: tabular-nums;
  }

  .shifts-roster {
    margin: 0;
  }

  .shifts-roster-empty {
    margin: 0;
    padding: 8px 2px;
    font-size: 0.88rem;
  }

  .shifts-roster ul {
    display: grid;
    gap: 6px;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .shifts-roster li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px 14px;
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #ffffff;
    font-size: 0.84rem;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  }

  .shifts-roster .roster-identity {
    display: block;
    min-width: 0;
    position: relative;
    overflow: hidden;
  }

  .shifts-roster .roster-name {
    position: relative;
    z-index: 2;
    display: block;
    font-weight: 800;
    line-height: 1.2;
    overflow-wrap: anywhere;
  }

  .emp-name-wrap--roster .emp-position {
    font-size: clamp(0.95em, 4.5vw, 1.45em);
    letter-spacing: -0.03em;
  }

  .shifts-roster .roster-meta {
    color: var(--brand-dark);
    white-space: nowrap;
    flex: 0 0 auto;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
    justify-self: end;
  }

  .shift-report-panel {
    margin: 0;
    display: flex;
    align-items: stretch;
    width: 100%;
    min-width: 0;
  }

  .shift-report-copy {
    appearance: none;
    border: 1px solid #000000;
    border-radius: 12px;
    background: #ffffff;
    color: var(--ink);
    font: inherit;
    font-weight: 800;
    font-size: 0.84rem;
    min-height: 44px;
    width: 100%;
    padding: 0 10px 0 12px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    cursor: pointer;
  }

  .shift-report-copy:hover {
    background: #f3f3f3;
  }

  .shift-report-copy:focus-visible {
    outline: 2px solid var(--brand);
    outline-offset: 2px;
  }

  .shift-report-copy.is-copied {
    background: color-mix(in srgb, var(--ok) 14%, white);
    border-color: var(--ok);
    color: #3f6212;
  }

  .shift-report-copy.is-copied .shift-report-copy__icon {
    border-left-color: var(--ok);
    color: #3f6212;
  }

  .shift-report-copy.is-loading {
    opacity: 0.72;
    cursor: wait;
  }

  .shift-report-copy:disabled {
    opacity: 0.55;
    cursor: wait;
  }

  .shift-report-copy__icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-left: 1px solid var(--line);
    padding-left: 10px;
    margin-left: 2px;
    color: var(--brand);
  }

  .shift-report-copy__icon svg {
    width: 16px;
    height: 16px;
    display: block;
  }

  .grafiki-table-panel {
    padding: 0;
    min-width: 0;
    max-width: 100%;
  }

  .grafiki-table-panel .table-wrap {
    width: 100%;
    max-width: 100%;
    min-width: 0;
    overflow-x: auto;
    overflow-y: visible;
    overscroll-behavior-x: contain;
    -webkit-overflow-scrolling: touch;
    border: 1px solid #000000;
    border-radius: 0;
    background: #ffffff;
    box-shadow: none;
  }

  .grafiki-table {
    width: max-content;
    min-width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border: 0;
    font-size: 0.78rem;
    background: #fff;
  }

  .grafiki-table th,
  .grafiki-table td {
    border: 0;
    border-right: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
    padding: 6px 4px;
    text-align: center;
    vertical-align: middle;
    white-space: nowrap;
  }

  /* Bez podwójnej krawędzi: zewnętrzną ramkę daje tylko .table-wrap */
  .grafiki-table tr > *:last-child {
    border-right: 0;
  }

  .grafiki-table tr:last-child > * {
    border-bottom: 0;
  }

  .grafiki-table thead th {
    background: var(--soft);
    font-weight: 900;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    position: sticky;
    top: 0;
    z-index: 2;
  }

  .grafiki-table .col-name {
    text-align: left;
    min-width: 148px;
    max-width: 220px;
    padding: 8px 10px;
    font-weight: 800;
    background: #fafafa;
    position: sticky;
    left: 0;
    z-index: 3;
    overflow: hidden;
  }

  .grafiki-table thead .col-name {
    z-index: 4;
  }

  .emp-name-wrap {
    position: relative;
    display: block;
    width: 100%;
    isolation: isolate;
  }

  .emp-name-wrap .emp-name {
    position: relative;
    z-index: 2;
    display: block;
    white-space: normal;
    line-height: 1.2;
  }

  .emp-name-wrap .emp-position {
    position: absolute;
    top: 50%;
    right: 0;
    z-index: 0;
    transform: translateY(-50%);
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
    font-size: 1.75em;
    font-weight: 900;
    line-height: 1;
    letter-spacing: -0.04em;
    pointer-events: none;
    user-select: none;
  }

  .emp-name-wrap .emp-position--animator {
    color: #22c55e;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--organizer {
    color: #3b82f6;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--kelner {
    color: #14b8a6;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--bar {
    color: #f59e0b;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--admin {
    color: #6366f1;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--kierownik-animatorow {
    color: #eab308;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--hr {
    color: #a855f7;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--pracownia {
    color: #ec4899;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--sprzatanie {
    color: #06b6d4;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--recepcja {
    color: #8b5cf6;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--kuchnia {
    color: #ef4444;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--dyrekcja {
    color: #1d4ed8;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--ksiegowosc {
    color: #d946ef;
    opacity: 0.34;
  }

  .emp-name-wrap .emp-position--konserwator {
    color: #78716c;
    opacity: 0.3;
  }

  .emp-name-wrap .emp-position--other {
    color: color-mix(in srgb, var(--muted) 80%, var(--ink));
    opacity: 0.22;
    font-size: 1.35em;
  }

  .grafiki-table .col-name .emp-name {
    display: block;
    white-space: normal;
    line-height: 1.2;
  }

  .grafiki-table .col-name .emp-position {
    margin-top: 0;
    white-space: nowrap;
  }

  .grafiki-table thead .col-date {
    min-width: 44px;
    cursor: pointer;
  }

  .grafiki-table thead .col-date .date-dd {
    display: block;
    font-size: 0.86rem;
    font-weight: 900;
    line-height: 1.1;
  }

  .grafiki-table thead .col-date .date-abbr {
    display: block;
    font-size: 0.62rem;
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
  }

  .grafiki-table .col-summary {
    min-width: 48px;
    font-weight: 800;
    background: #fafafa;
    position: sticky;
    right: 0;
    z-index: 2;
    box-shadow: -4px 0 8px rgba(15, 23, 42, 0.06);
  }

  .grafiki-table thead .col-summary {
    z-index: 5;
  }

  .grafiki-table tbody .col-summary {
    z-index: 1;
  }

  .grafiki-table td.slot {
    min-width: 52px;
    font-weight: 700;
    color: var(--ink);
    cursor: pointer;
  }

  #grafik[data-view="month"] {
    table-layout: fixed;
    width: 100%;
    min-width: 0;
    max-width: 100%;
    font-size: 0.62rem;
  }

  #grafik[data-view="month"] th,
  #grafik[data-view="month"] td {
    padding: 3px 1px;
  }

  #grafik[data-view="month"] thead .col-date {
    min-width: 0;
    width: auto;
    padding: 4px 0;
  }

  #grafik[data-view="month"] thead .col-date .date-dd {
    font-size: 0.68rem;
  }

  #grafik[data-view="month"] thead .col-date .date-abbr {
    font-size: 0.5rem;
  }

  #grafik[data-view="month"] td.slot {
    min-width: 0;
    max-width: none;
    width: auto;
    white-space: nowrap;
    word-break: normal;
    overflow-wrap: normal;
    hyphens: none;
    font-size: 0.58rem;
    letter-spacing: -0.03em;
    line-height: 1.05;
    padding: 2px 0;
  }

  #grafik[data-view="month"] .col-name {
    min-width: 0;
    width: 7.2rem;
    max-width: 7.2rem;
    padding: 4px 6px;
    font-size: 0.66rem;
  }

  #grafik[data-view="month"] .col-name .emp-name {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  #grafik[data-view="month"] .col-summary {
    min-width: 0;
    width: 2.2rem;
    max-width: 2.2rem;
    padding: 3px 1px;
    font-size: 0.6rem;
  }

  #grafik[data-view="month"] td.slot.custom-shift {
    padding: 1px;
  }

  #grafik[data-view="month"] td.slot.custom-shift .shift-chip {
    padding: 2px 1px;
    border-radius: 4px;
    white-space: nowrap;
    font-size: inherit;
    letter-spacing: inherit;
    line-height: inherit;
  }

  .page-schedules .grafiki-table-panel .table-wrap:has(#grafik[data-view="month"]) {
    overflow-x: hidden;
  }

  body.page-schedules main {
    max-width: none;
    padding-left: 10px;
    padding-right: 10px;
  }

  .grafiki-table td.slot.custom-shift {
    position: relative;
    background: transparent;
    color: #000000;
    font-weight: 800;
    box-shadow: none;
    padding: 3px 2px;
    border-radius: 0 !important;
  }

  .grafiki-table td.slot.custom-shift .shift-chip {
    display: flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    width: 100%;
    min-height: 100%;
    padding: 5px 6px 5px 10px;
    border-radius: 8px;
    background: #e8f0fe;
    color: #000000;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
    box-shadow: inset 2px 0 0 0 #1a73e8;
    line-height: 1.15;
  }

  .grafiki-table td.slot.custom-shift::before {
    display: none;
  }

  .grafiki-table .off-day {
    background: #f6f6f6;
  }

  .grafiki-table td.slot.off-day.custom-shift {
    background: transparent;
    color: #000000;
  }

  .grafiki-table td.slot.off-day.custom-shift .shift-chip {
    background: #e8f0fe;
  }

  .grafiki-table td.slot.custom-shift.today .shift-chip {
    background: #ceead6;
    box-shadow: inset 2px 0 0 0 #34a853;
  }

  .grafiki-table td.slot.custom-shift.is-selected-day {
    background: transparent;
    color: #000000;
  }

  .grafiki-table td.slot.custom-shift.is-selected-day .shift-chip {
    background: #feefc3;
    box-shadow: inset 2px 0 0 0 #f9ab00;
  }

  .grafiki-table td.slot.custom-shift.is-selected-day.today {
    background: transparent;
    color: #000000;
  }

  .grafiki-table td.slot.custom-shift.is-selected-day.today .shift-chip {
    background: #feefc3;
    box-shadow: inset 2px 0 0 0 #f9ab00;
  }

  /* Lekkie podświetlenie kolumny — bez ramki */
  .grafiki-table th.today,
  .grafiki-table td.today {
    background: #e6f4ea;
    box-shadow: none;
    outline: none;
  }

  .grafiki-table th.is-selected-day,
  .grafiki-table td.is-selected-day {
    background: #fef7e0;
    box-shadow: none;
    outline: none;
  }

  .grafiki-table th.today::after,
  .grafiki-table td.today::after,
  .grafiki-table th.is-selected-day::after,
  .grafiki-table td.is-selected-day::after {
    content: none;
    display: none;
  }

  .grafiki-table td.slot.custom-shift.today,
  .grafiki-table td.slot.custom-shift.is-selected-day {
    background: transparent;
  }

  /* Tydzień: siatka Excel + pełna szerokość
     border-collapse:separate — żeby komórki miały stabilny układ */
  #grafik[data-view="week"] {
    border-collapse: separate;
    border-spacing: 0;
    table-layout: fixed;
    width: 100%;
    font-size: 0.8rem;
    border: 0;
  }

  #grafik[data-view="week"] th,
  #grafik[data-view="week"] td {
    border: 0;
    border-right: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
    border-radius: 0 !important;
    padding: 7px 4px;
  }

  #grafik[data-view="week"] tr > *:last-child {
    border-right: 0;
  }

  /* col-summary jest ukryty — ostatnia widoczna kolumna to przedostatnia komórka */
  #grafik[data-view="week"] tr > *:nth-last-child(2) {
    border-right: 0;
  }

  #grafik[data-view="week"] tr:last-child > * {
    border-bottom: 0;
  }

  #grafik[data-view="week"] thead th,
  #grafik[data-view="week"] tfoot th,
  #grafik[data-view="week"] tfoot td {
    border-radius: 0 !important;
  }

  #grafik[data-view="week"] thead th {
    background: var(--soft);
    padding: 8px 3px;
    text-transform: none;
    letter-spacing: 0;
    font-weight: 800;
  }

  #grafik[data-view="week"] thead .col-name {
    color: var(--muted);
    font-size: 0.68rem;
    font-weight: 800;
    background: var(--soft);
    text-align: left;
    padding-left: 10px;
  }

  #grafik[data-view="week"] thead .col-date {
    border-radius: 0;
    background: var(--soft);
    padding: 8px 3px;
    cursor: pointer;
  }

  #grafik[data-view="week"] thead .col-date .date-dd {
    font-size: 0.92rem;
    font-weight: 900;
    line-height: 1.1;
    color: var(--ink);
  }

  #grafik[data-view="week"] thead .col-date .date-abbr {
    font-size: 0.64rem;
    font-weight: 700;
    color: var(--muted);
    margin-top: 2px;
  }

  #grafik[data-view="week"] tbody .col-name {
    background: #fafafa;
    text-align: left;
    padding: 8px 8px 8px 10px;
    font-weight: 800;
  }

  #grafik[data-view="week"] tbody td.slot {
    padding: 5px 4px;
    vertical-align: middle;
    background: #ffffff;
    min-height: 40px;
    height: auto;
    white-space: normal;
    line-height: 1.2;
    word-break: break-word;
    border-radius: 0;
  }

  #grafik[data-view="week"] tbody td.slot:not(.custom-shift) {
    color: transparent;
  }

  #grafik[data-view="week"] tbody td.slot.custom-shift {
    background: transparent;
    color: #000000;
    border-radius: 0 !important;
    font-weight: 800;
    font-size: 0.78rem;
    letter-spacing: -0.01em;
    font-variant-numeric: tabular-nums;
    box-shadow: none;
    padding: 3px 2px;
  }

  #grafik[data-view="week"] tbody td.slot.custom-shift .shift-chip {
    min-height: 28px;
    font-size: inherit;
  }

  #grafik[data-view="week"] tbody td.slot.off-day {
    background: #fafafa;
  }

  #grafik[data-view="week"] tbody td.slot.off-day.custom-shift {
    background: transparent;
    color: #000000;
  }

  #grafik[data-view="week"] tbody tr:hover td.slot.custom-shift:not(.today):not(.is-selected-day) .shift-chip {
    background: #d2e3fc;
  }

  #grafik[data-view="week"] th.today,
  #grafik[data-view="week"] td.today:not(.custom-shift) {
    background: #e6f4ea;
  }

  #grafik[data-view="week"] thead th.today {
    background: #ceead6;
    color: #000000;
  }

  #grafik[data-view="week"] tfoot th.today,
  #grafik[data-view="week"] tfoot td.today {
    background: #e6f4ea;
  }

  #grafik[data-view="week"] tbody td.slot.today.custom-shift,
  #grafik[data-view="week"] tbody td.slot.today.off-day.custom-shift {
    background: transparent;
    color: #000000;
  }

  #grafik[data-view="week"] th.is-selected-day,
  #grafik[data-view="week"] td.is-selected-day:not(.custom-shift) {
    background: #fef7e0;
  }

  #grafik[data-view="week"] thead th.is-selected-day {
    background: #feefc3;
    color: #000000;
  }

  #grafik[data-view="week"] tfoot th.is-selected-day,
  #grafik[data-view="week"] tfoot td.is-selected-day {
    background: #fef7e0;
  }

  #grafik[data-view="week"] tbody td.slot.is-selected-day.custom-shift,
  #grafik[data-view="week"] tbody td.slot.is-selected-day.off-day.custom-shift {
    background: transparent;
    color: #000000;
  }

  #grafik[data-view="week"] tfoot tr.hours-total-row th,
  #grafik[data-view="week"] tfoot tr.hours-total-row td {
    background: var(--soft);
    border-top: 1px solid #000000;
    padding: 10px 5px;
    font-weight: 800;
    color: var(--ink);
  }

  #grafik[data-view="week"] tfoot .col-name {
    color: var(--muted);
    font-size: 0.68rem;
    font-weight: 700;
    line-height: 1.2;
    white-space: normal;
    text-align: left;
    padding-left: 10px;
  }

  #grafik[data-view="week"] tfoot .col-date {
    font-size: 0.78rem;
    font-weight: 900;
    color: var(--brand-dark);
    font-variant-numeric: tabular-nums;
  }

  #grafik[data-view="week"] .col-name {
    min-width: 0;
    max-width: none;
  }

  #grafik[data-view="week"] .col-summary {
    display: none;
  }

  .grafiki-table-panel:has(#grafik[data-view="week"]) .table-wrap {
    border-radius: 0 !important;
    overflow: visible;
    background: #ffffff;
    box-shadow: none;
    border: 1px solid #000000;
  }

  .grafiki-table th,
  .grafiki-table td {
    border-radius: 0;
  }

  .grafiki-table tfoot tr.hours-total-row th,
  .grafiki-table tfoot tr.hours-total-row td {
    background: var(--soft);
    font-weight: 900;
    border-top: 1px solid #000000;
  }

  .grafiki-table tfoot .people-total-row .col-name {
    white-space: normal;
    line-height: 1.15;
    font-size: 0.72rem;
    max-width: 220px;
  }

  .grafiki-table tfoot .hours-total-cell,
  .grafiki-table tfoot .col-summary {
    color: var(--brand-dark);
  }

  .grafiki-empty {
    display: grid;
    place-content: center;
    gap: 12px;
    min-height: 40vh;
    text-align: center;
    padding: 40px 16px;
  }

  .grafiki-empty h2 {
    margin: 0;
    font-size: 1.35rem;
  }

  main.is-grafiki-loading .grafiki-filters,
  main.is-grafiki-loading .grafiki-layout,
  main.is-grafiki-loading .grafiki-empty {
    opacity: 0.55;
    pointer-events: none;
    transition: opacity 0.15s ease;
  }

  .grafiki-mobile-month {
    display: none;
  }

  .grafiki-mobile-month-cal {
    display: grid;
    gap: 8px;
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: #ffffff;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  }

  .grafiki-mobile-cal-weekdays {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 4px;
  }

  .grafiki-mobile-cal-weekday {
    text-align: center;
    font-size: 0.62rem;
    font-weight: 800;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: var(--muted);
    padding: 2px 0;
  }

  .grafiki-mobile-cal-grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 4px;
  }

  .grafiki-mobile-cal-pad {
    display: block;
    aspect-ratio: 1;
  }

  .grafiki-mobile-cal-day {
    appearance: none;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 1px;
    aspect-ratio: 1;
    min-height: 0;
    padding: 2px;
    margin: 0;
    border: 1px solid transparent;
    border-radius: 10px;
    background: #fafafa;
    color: var(--ink);
    font: inherit;
    cursor: pointer;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
    transition: background 0.12s ease, border-color 0.12s ease, box-shadow 0.12s ease;
  }

  .grafiki-mobile-cal-day.off-day {
    background: #f3f3f3;
    color: color-mix(in srgb, var(--ink) 72%, white);
  }

  .grafiki-mobile-cal-day.has-shifts {
    background: color-mix(in srgb, var(--brand) 12%, white);
    border-color: color-mix(in srgb, var(--brand) 28%, transparent);
  }

  .grafiki-mobile-cal-day.off-day.has-shifts {
    background: color-mix(in srgb, var(--brand) 10%, #f3f3f3);
  }

  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day,
  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.off-day,
  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.has-shifts,
  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.off-day.has-shifts {
    background: #fafafa;
    border-color: transparent;
    color: var(--ink);
    box-shadow: none;
  }

  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.is-emp-work {
    background: color-mix(in srgb, #3b82f6 22%, white);
    border-color: color-mix(in srgb, #3b82f6 42%, transparent);
    color: #1e3a8a;
  }

  .grafiki-mobile-cal-day.today {
    background: #e6f4ea;
    box-shadow: none;
  }

  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.today:not(.is-emp-work):not(.is-selected-day) {
    background: #e6f4ea;
  }

  .grafiki-mobile-cal-day.is-selected-day {
    outline: none;
    background: #fef7e0;
  }

  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.is-selected-day:not(.is-emp-work) {
    background: #fef7e0;
  }

  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.is-emp-work.is-selected-day {
    background: color-mix(in srgb, #3b82f6 18%, #fef7e0);
    border-color: color-mix(in srgb, #3b82f6 50%, transparent);
  }

  .grafiki-mobile-cal-day .cal-day-num {
    font-size: 0.82rem;
    font-weight: 900;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }

  .grafiki-mobile-cal-day .cal-day-count {
    font-size: 0.58rem;
    font-weight: 800;
    line-height: 1;
    color: var(--brand-dark);
    font-variant-numeric: tabular-nums;
  }

  .grafiki-mobile-month.is-emp-filtered .grafiki-mobile-cal-day.is-emp-work .cal-day-count {
    color: #1e3a8a;
    font-size: 0.52rem;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .grafiki-mobile-cal-legend {
    margin: 2px 0 0;
    font-size: 0.72rem;
    text-align: center;
    line-height: 1.3;
  }

  .grafiki-month-emp-assign {
    display: grid;
    gap: 8px;
    justify-items: center;
    position: relative;
    z-index: 20;
    margin-top: 6px;
  }

  .grafiki-month-emp-combobox {
    position: relative;
    width: 100%;
    max-width: 22rem;
  }

  .grafiki-month-emp-search {
    display: block;
    width: 100%;
  }

  .grafiki-month-emp-search input {
    width: 100%;
    min-height: 44px;
    box-sizing: border-box;
    text-align: center;
  }

  .grafiki-month-emp-search input::placeholder {
    text-align: center;
  }

  .grafiki-month-emp-assign .grafiki-month-emp-sheet {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    right: 0;
    width: 100%;
    z-index: 40;
  }

  .grafiki-month-emp-sheet.is-ported {
    position: fixed;
    margin: 0;
    z-index: 16000;
    box-sizing: border-box;
    display: grid;
    overflow: hidden;
  }

  .grafiki-month-emp-assign .grafiki-month-emp-sheet[hidden],
  .grafiki-month-emp-sheet.is-ported[hidden] {
    display: none !important;
  }

  .grafiki-month-emp-sheet .animator-assign__list,
  .grafiki-month-emp-assign .animator-assign__list {
    max-height: none;
    height: 100%;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .grafiki-month-emp-assign .animator-assign__option[hidden],
  .grafiki-month-emp-sheet .animator-assign__option[hidden] {
    display: none !important;
  }

  .grafiki-month-emp-assign .animator-assign__option.is-selected,
  .grafiki-month-emp-sheet .animator-assign__option.is-selected {
    background: color-mix(in srgb, var(--brand) 14%, white);
  }

  @media (max-width: 900px) {
    .grafiki-filters .schedule-day-nav {
      gap: 8px;
      padding: 6px;
    }
  }

  @media (max-width: 860px) {
    .grafiki-layout {
      margin-top: 12px;
    }

    body.page-schedules main {
      padding-left: 0;
      padding-right: 0;
      overflow-x: clip;
    }

    body.page-schedules .grafiki-filters,
    body.page-schedules .grafiki-layout > .schedule-period-bar {
      padding-left: max(24px, env(safe-area-inset-left, 0px));
      padding-right: max(24px, env(safe-area-inset-right, 0px));
      box-sizing: border-box;
    }

    body.page-schedules .grafiki-table-panel {
      width: 100%;
      max-width: 100%;
      margin: 0;
      padding: 0;
      overflow-x: clip;
    }

    body.page-schedules .grafiki-table-panel:has(#grafik[data-view="week"]) {
      padding-left: 0;
      padding-right: 0;
      overflow-x: clip;
    }

    body.page-schedules .grafiki-table-panel .table-wrap {
      width: 100%;
      max-width: 100%;
      margin: 0;
      border-left: 0;
      border-right: 0;
      border-radius: 0;
      box-shadow: none;
    }

    body.page-schedules .grafiki-table-panel:has(#grafik[data-view="week"]) .table-wrap {
      width: 100%;
      margin: 0;
      border: 1px solid #000000;
      border-radius: 0;
      box-shadow: none;
      overflow: hidden;
    }

    body.page-schedules .grafiki-table .col-name {
      position: static;
      left: auto;
    }

    .grafiki-table-panel[data-mobile-month="1"] .grafiki-mobile-month {
      display: grid;
      gap: 12px;
      width: 100%;
      padding-top: 10px;
      padding-left: max(14px, env(safe-area-inset-left, 0px));
      padding-right: max(14px, env(safe-area-inset-right, 0px));
      box-sizing: border-box;
    }

    .page-schedules .grafiki-roster {
      margin-bottom: 0;
    }

    .grafiki-table-panel[data-mobile-month="1"] .table-wrap {
      display: none;
    }

    .grafiki-filters .schedule-controls {
      gap: 8px;
    }

    .grafiki-layout .schedule-period-nav-card {
      padding: 3px;
    }

    .grafiki-layout .schedule-period-label {
      font-size: 0.9rem;
      padding: 8px 6px;
    }

    .grafiki-layout .schedule-period-arrow {
      min-width: 40px;
      min-height: 40px;
    }

    .grafiki-layout .schedule-view-toggle .schedule-control {
      min-height: 40px;
      font-size: 0.84rem;
    }

    .shift-date-info {
      width: 6rem;
      min-width: 6rem;
    }

    .shift-date-info strong {
      font-size: 0.7rem;
    }

    .shift-date-info span {
      font-size: 0.88rem;
    }

    .grafiki-roster-head {
      gap: 10px;
      align-items: stretch;
    }

    .staff-summary-wrap {
      flex-basis: 100%;
    }

    .staff-summary-item {
      font-size: 0.76rem;
      padding: 4px 7px 4px 9px;
    }

    .shift-report-copy {
      min-height: 36px;
      font-size: 0.8rem;
      padding: 0 8px 0 10px;
    }

    .shifts-roster ul {
      gap: 6px;
    }

    .shifts-roster li {
      padding: 9px 10px;
      font-size: 0.8rem;
      border-radius: 10px;
    }

    .emp-name-wrap--roster .emp-position {
      font-size: clamp(0.85em, 4vw, 1.2em);
    }

    .grafiki-table-panel .table-wrap {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      overscroll-behavior-x: contain;
      margin: 0;
      padding-bottom: 4px;
    }

    .grafiki-table-panel .table-wrap:has(#grafik[data-view="week"]) {
      overflow-x: clip;
      max-width: 100%;
    }

    .grafiki-table {
      font-size: 0.68rem;
      min-width: 100%;
    }

    #grafik[data-view="week"].grafiki-table {
      min-width: 0;
    }

    .grafiki-table th,
    .grafiki-table td {
      padding: 4px 2px;
    }

    .grafiki-table .col-name {
      min-width: 78px;
      max-width: 92px;
      padding: 5px 6px;
      font-size: 0.66rem;
    }

    .grafiki-table .col-name .emp-name {
      display: block;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      line-height: 1.15;
    }

    .grafiki-table .col-name .emp-position {
      font-size: 1.45em;
    }

    .grafiki-table thead .col-date {
      min-width: 34px;
    }

    .grafiki-table thead .col-date .date-dd {
      font-size: 0.76rem;
    }

    .grafiki-table thead .col-date .date-abbr {
      font-size: 0.56rem;
    }

    .grafiki-table td.slot {
      min-width: 34px;
      font-size: 0.66rem;
    }

    .grafiki-table .col-summary {
      min-width: 36px;
      font-size: 0.66rem;
    }

    #grafik[data-view="week"] {
      width: 100%;
      min-width: 0;
      max-width: 100%;
      font-size: 0.72rem;
    }

    #grafik[data-view="week"] .col-name {
      width: 24%;
      min-width: 0;
      max-width: 24%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      box-sizing: border-box;
    }

    #grafik[data-view="week"] thead .col-name {
      font-size: 0.52rem;
      padding-left: max(8px, env(safe-area-inset-left, 0px));
      text-transform: none;
      letter-spacing: 0;
    }

    #grafik[data-view="week"] tbody .col-name {
      padding: 6px 4px 6px max(6px, env(safe-area-inset-left, 0px));
      font-size: 0.54rem;
      letter-spacing: -0.02em;
    }

    #grafik[data-view="week"] .col-name .emp-name {
      display: block;
      font-size: inherit;
      font-weight: 800;
      line-height: 1.1;
      letter-spacing: -0.03em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    #grafik[data-view="week"] .col-name .emp-position {
      font-size: 1.35em;
      opacity: 0.28;
    }

    #grafik[data-view="week"] thead .col-date {
      padding: 6px 2px;
      border-radius: 10px;
    }

    #grafik[data-view="week"] thead .col-date .date-dd {
      font-size: 0.78rem;
    }

    #grafik[data-view="week"] thead .col-date .date-abbr {
      font-size: 0.54rem;
    }

    #grafik[data-view="week"] tbody td.slot {
      padding: 4px 2px;
      min-height: 38px;
    }

    #grafik[data-view="week"] tbody td.slot.custom-shift {
      font-size: 0.66rem;
      border-radius: 0 !important;
      padding: 2px;
    }

    #grafik[data-view="week"] tbody td.slot.custom-shift .shift-chip {
      min-height: 24px;
      padding: 4px 4px 4px 8px;
      border-radius: 6px;
    }

    #grafik[data-view="week"] tfoot .col-name {
      font-size: 0.56rem;
      padding-left: max(8px, env(safe-area-inset-left, 0px));
    }

    #grafik[data-view="week"] tfoot .col-date {
      font-size: 0.7rem;
    }

    #grafik[data-view="week"] .col-name .emp-position {
      font-size: 1.2em;
    }
  }

  @media (max-width: 640px) {
    body.page-schedules .grafiki-filters,
    body.page-schedules .grafiki-layout > .schedule-period-bar {
      padding-left: max(14px, env(safe-area-inset-left, 0px));
      padding-right: max(14px, env(safe-area-inset-right, 0px));
    }

    .grafiki-layout .schedule-period-label {
      font-size: 0.78rem;
      padding: 0 8px;
    }

    .grafiki-layout .schedule-period-arrow {
      min-width: 32px;
      min-height: 32px;
      padding: 0 8px;
    }

    .grafiki-roster-head {
      gap: 8px;
    }

    .shift-report-copy {
      min-height: 36px;
      font-size: 0.8rem;
      padding: 0 8px 0 10px;
    }

    .grafiki-table .col-name {
      min-width: 72px;
      max-width: 84px;
      font-size: 0.62rem;
      padding: 4px 5px;
    }

    #grafik[data-view="week"] .col-name {
      width: 18%;
      max-width: 18%;
      font-size: 0.62rem;
    }

    #grafik[data-view="week"] thead .col-name {
      font-size: 0.52rem;
    }

    #grafik[data-view="week"] tfoot .col-name {
      font-size: 0.52rem;
    }

    #grafik[data-view="week"] tbody td.slot.custom-shift {
      font-size: 0.62rem;
    }

    .shifts-roster .roster-meta {
      font-size: 0.72rem;
    }
  }
}
</style>
"""


def grafik4600_script() -> str:
    """Skrypt panelu dnia + miękka nawigacja filtrów grafiku (bez pełnego reloadu)."""
    return r"""
<script>
(() => {
  const emptyHtml = '<p class="muted shifts-roster-empty">brak przypisań</p>';

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatDayLabel(iso) {
    const today = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const todayIso = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    const tomorrowIso = `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}`;
    const yesterdayIso = `${yesterday.getFullYear()}-${pad(yesterday.getMonth() + 1)}-${pad(yesterday.getDate())}`;
    if (iso === todayIso) return "DZISIAJ";
    if (iso === tomorrowIso) return "JUTRO";
    if (iso === yesterdayIso) return "WCZORAJ";
    const d = new Date(iso + "T12:00:00");
    const names = ["Niedziela", "Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota"];
    return names[d.getDay()] || iso;
  }

  function formatDayDisplay(iso) {
    const d = new Date(iso + "T12:00:00");
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
  }

  function parseShiftStartHour(shift) {
    const match = String(shift || "").trim().match(/^(\d{1,2})(?::(\d{2}))?/);
    if (!match) return 99;
    const hour = Number(match[1]);
    const minute = Number(match[2] || 0);
    if (!Number.isFinite(hour) || hour > 23) return 99;
    return hour + (minute / 60);
  }

  function rosterModifierClass(value) {
    const modifier = String(value || "").trim();
    return /^emp-position--[\w-]+$/.test(modifier) ? modifier : "emp-position--other";
  }

  function rosterIdentityHtml(person) {
    const watermark = String(
      person.position_watermark || person.position_full || person.position || "",
    ).trim();
    const modifier = rosterModifierClass(person.position_modifier);
    const positionHtml = watermark
      ? `<span class="emp-position ${modifier}" aria-hidden="true">${escapeHtml(watermark)}</span>`
      : "";
    return `<div class="roster-identity emp-name-wrap emp-name-wrap--roster" title="${escapeHtml(person.name)}"><span class="emp-name roster-name">${escapeHtml(person.name)}</span>${positionHtml}</div>`;
  }

  function rosterHtml(people) {
    if (!people.length) return emptyHtml;
    return `<ul>${people.map((person) => {
      const meta = person.hours ? `${escapeHtml(person.shift)} · ${escapeHtml(person.hours)}h` : escapeHtml(person.shift);
      return `<li>${rosterIdentityHtml(person)}<span class="roster-meta">${meta}</span></li>`;
    }).join("")}</ul>`;
  }

  function staffSummaryHtml(positions) {
    if (!Array.isArray(positions) || !positions.length) {
      return '<p class="staff-summary-empty muted">brak przypisań</p>';
    }
    const items = positions
      .filter((item) => item && item.name && Number(item.count) > 0)
      .map((item) => (
        `<span class="staff-summary-item">`
        + `<span class="staff-summary-position">${escapeHtml(String(item.name))}</span>`
        + `<span class="staff-summary-count">${Number(item.count)}</span>`
        + `</span>`
      ))
      .join("");
    if (!items) {
      return '<p class="staff-summary-empty muted">brak przypisań</p>';
    }
    return `<div class="staff-summary-grid">${items}</div>`;
  }

  function applyGrafikDataScripts(root) {
    if (!root) return;
    root.querySelectorAll("script").forEach((script) => {
      const code = script.textContent || "";
      if (!code.includes("grafikShiftsData") && !code.includes("grafikMonthDates") && !code.includes("grafikShiftReports") && !code.includes("grafikStaffSummary")) {
        return;
      }
      try {
        new Function(code)();
      } catch (_err) {
        // ignore malformed payload
      }
    });
  }

  function replaceNode(current, next) {
    if (!next) {
      current?.remove();
      return null;
    }
    const imported = document.importNode(next, true);
    if (current) {
      current.replaceWith(imported);
    } else {
      const main = document.querySelector("main");
      const filters = document.querySelector(".grafiki-filters");
      if (filters) filters.after(imported);
      else main?.appendChild(imported);
    }
    return imported;
  }

  function initMobileMonthEmployeePicker() {
    const section = document.querySelector(".grafiki-mobile-month");
    const picker = section?.querySelector("[data-month-emp-picker]");
    if (!section || !picker) return;

    const combobox = picker.querySelector(".grafiki-month-emp-combobox") || picker;
    const filterInput = picker.querySelector("[data-month-emp-filter]");
    let sheet = picker.querySelector("[data-month-emp-sheet]");
    const legend = section.querySelector("[data-cal-legend]");
    const dayButtons = Array.from(section.querySelectorAll(".grafiki-mobile-cal-day[data-date]"));
    const shiftsByDate = window.grafikShiftsData || {};
    let selectedEmployee = "";
    let selectedDisplayName = "";
    let placeRaf = 0;

    function options() {
      return Array.from((sheet || picker).querySelectorAll("[data-staff-option][data-employee]"));
    }

    function normalizeSearch(value) {
      return String(value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/ł/g, "l")
        .replace(/Ł/g, "L")
        .toLowerCase()
        .trim();
    }

    function isWorkShift(shift) {
      const value = String(shift || "").trim();
      return Boolean(value) && !/^(?:-|\.|x|w|wolne|u|urlop|\?)$/i.test(value);
    }

    function isOpen() {
      return Boolean(sheet) && !sheet.hidden;
    }

    function bottomMenuOffset() {
      const tabBar = document.querySelector(".tabs");
      if (tabBar) {
        const rect = tabBar.getBoundingClientRect();
        if (rect.height > 0 && rect.top < window.innerHeight) {
          return Math.ceil(window.innerHeight - rect.top) + 8;
        }
      }
      return 96;
    }

    function placeSheet() {
      if (!sheet || !filterInput) return;
      const trigger = filterInput.getBoundingClientRect();
      const width = Math.min(Math.max(trigger.width, 240), window.innerWidth - 16);
      let left = trigger.left + (trigger.width - width) / 2;
      if (left < 8) left = 8;
      if (left + width > window.innerWidth - 8) {
        left = Math.max(8, window.innerWidth - width - 8);
      }
      const top = trigger.bottom + 6;
      const available = Math.max(160, window.innerHeight - top - bottomMenuOffset());
      if (sheet.parentElement !== document.body) {
        document.body.appendChild(sheet);
      }
      sheet.classList.add("is-ported");
      sheet.style.top = `${top}px`;
      sheet.style.left = `${left}px`;
      sheet.style.right = "auto";
      sheet.style.width = `${width}px`;
      sheet.style.maxHeight = `${available}px`;
      sheet.style.height = `${available}px`;
    }

    function restoreSheet() {
      if (!sheet) return;
      sheet.classList.remove("is-ported");
      sheet.style.top = "";
      sheet.style.left = "";
      sheet.style.right = "";
      sheet.style.width = "";
      sheet.style.maxHeight = "";
      sheet.style.height = "";
      if (sheet.parentElement !== combobox) {
        combobox.appendChild(sheet);
      }
    }

    function openPicker() {
      if (!sheet) return;
      sheet.hidden = false;
      filterInput?.setAttribute("aria-expanded", "true");
      placeSheet();
      filterOptions();
    }

    function closePicker({ clearQuery = true } = {}) {
      if (placeRaf) {
        cancelAnimationFrame(placeRaf);
        placeRaf = 0;
      }
      if (sheet) {
        sheet.hidden = true;
        restoreSheet();
      }
      filterInput?.setAttribute("aria-expanded", "false");
      if (clearQuery && filterInput) {
        filterInput.value = selectedDisplayName;
      }
      options().forEach((opt) => {
        opt.hidden = false;
      });
    }

    function filterOptions() {
      const query = normalizeSearch(filterInput?.value || "");
      const selectedNorm = normalizeSearch(selectedDisplayName);
      const effectiveQuery = query && query !== selectedNorm ? query : "";
      options().forEach((opt) => {
        const hay = opt.getAttribute("data-search") || normalizeSearch(opt.textContent);
        opt.hidden = Boolean(effectiveQuery) && !hay.includes(effectiveQuery);
      });
    }

    function paintCalendar(employeeId) {
      const filtered = Boolean(employeeId);
      section.classList.toggle("is-emp-filtered", filtered);
      picker.classList.toggle("is-assigned", filtered);

      dayButtons.forEach((day) => {
        const iso = day.getAttribute("data-date") || "";
        const countEl = day.querySelector(".cal-day-count");
        const peopleCount = Number(day.getAttribute("data-people-count") || "0");
        day.classList.remove("is-emp-work");

        if (!filtered) {
          day.classList.toggle("has-shifts", peopleCount > 0);
          if (countEl) {
            if (peopleCount > 0) {
              countEl.hidden = false;
              countEl.textContent = String(peopleCount);
            } else {
              countEl.hidden = true;
              countEl.textContent = "";
            }
          }
          return;
        }

        day.classList.remove("has-shifts");
        const cell = (shiftsByDate[iso] || {})[employeeId] || {};
        const shift = String(cell.shift || "").trim();
        const works = isWorkShift(shift);
        day.classList.toggle("is-emp-work", works);
        if (countEl) {
          if (works) {
            countEl.hidden = false;
            countEl.textContent = shift;
          } else {
            countEl.hidden = true;
            countEl.textContent = "";
          }
        }
      });

      if (legend) {
        legend.textContent = filtered
          ? "Niebieskie dni — przedział godzin pracy wybranej osoby"
          : "Cyfra pod dniem — liczba osób na zmianie";
      }
    }

    function clearSelection() {
      selectedEmployee = "";
      selectedDisplayName = "";
      options().forEach((opt) => {
        opt.classList.remove("is-selected");
        opt.setAttribute("aria-selected", "false");
      });
      if (filterInput) filterInput.value = "";
      paintCalendar("");
    }

    function selectEmployee(employeeId, option) {
      if (!employeeId || !option) {
        clearSelection();
        closePicker({ clearQuery: false });
        return;
      }

      selectedEmployee = employeeId;
      selectedDisplayName = option.getAttribute("data-display-name") || option.textContent.trim();
      options().forEach((opt) => {
        const match = opt.getAttribute("data-employee") === selectedEmployee;
        opt.classList.toggle("is-selected", match);
        opt.setAttribute("aria-selected", match ? "true" : "false");
      });
      if (filterInput) filterInput.value = selectedDisplayName;
      paintCalendar(selectedEmployee);
      closePicker({ clearQuery: false });
    }

    filterInput?.addEventListener("focus", () => {
      openPicker();
      if (selectedDisplayName && filterInput.value === selectedDisplayName) {
        filterInput.select();
      }
    });
    filterInput?.addEventListener("click", () => {
      openPicker();
    });
    filterInput?.addEventListener("input", () => {
      if (!normalizeSearch(filterInput.value) && selectedEmployee) {
        clearSelection();
      }
      openPicker();
      filterOptions();
    });
    filterInput?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closePicker();
        filterInput.blur();
      }
    });

    document.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const opt = target.closest("[data-month-emp-sheet] [data-staff-option][data-employee]");
      if (opt && sheet && sheet.contains(opt) && !opt.hidden) {
        event.preventDefault();
        selectEmployee(opt.getAttribute("data-employee") || "", opt);
        return;
      }
      if (!isOpen()) return;
      if (picker.contains(target) || (sheet && sheet.contains(target))) return;
      closePicker();
    });

    window.addEventListener("resize", () => {
      if (!isOpen()) return;
      placeSheet();
    }, { passive: true });
    window.addEventListener("scroll", () => {
      if (!isOpen()) return;
      if (placeRaf) cancelAnimationFrame(placeRaf);
      placeRaf = requestAnimationFrame(() => {
        placeRaf = 0;
        placeSheet();
      });
    }, { passive: true, capture: true });
  }

  function initPanel() {
    const shiftsByDate = window.grafikShiftsData || {};
    const shiftReports = window.grafikShiftReports || {};
    const staffSummary = window.grafikStaffSummary || {};
    const dates = Array.isArray(window.grafikMonthDates) ? window.grafikMonthDates.slice() : [];
    const table = document.getElementById("grafik");
    if (!table || !dates.length) return;

    let current = table.getAttribute("data-initial-date") || dates[0];
    if (!dates.includes(current)) current = dates[0];
    let currentReport = shiftReports[current] || "";
    let copyFeedbackTimer = 0;
    let reportRequestToken = 0;

    const copyButton = document.getElementById("shift-report-copy");

    function setCopyButtonLoading(isLoading) {
      if (!copyButton) return;
      copyButton.classList.toggle("is-loading", isLoading);
      copyButton.toggleAttribute("disabled", isLoading);
      copyButton.setAttribute("aria-busy", isLoading ? "true" : "false");
    }

    async function copyTextToClipboard(text) {
      if (!text) return false;
      try {
        if (navigator.clipboard && window.isSecureContext && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          return true;
        }
      } catch (_err) {
        // fall through to legacy copy for HTTP / iOS
      }

      const helper = document.createElement("textarea");
      helper.value = text;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.top = "0";
      helper.style.left = "0";
      helper.style.width = "2em";
      helper.style.height = "2em";
      helper.style.padding = "0";
      helper.style.border = "none";
      helper.style.outline = "none";
      helper.style.boxShadow = "none";
      helper.style.background = "transparent";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.focus();
      helper.select();
      helper.setSelectionRange(0, helper.value.length);
      let copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (_err) {
        copied = false;
      } finally {
        helper.remove();
      }
      return copied;
    }

    async function ensureShiftReport(iso) {
      if (shiftReports[iso]) {
        currentReport = shiftReports[iso];
        return shiftReports[iso];
      }
      const token = ++reportRequestToken;
      setCopyButtonLoading(true);
      try {
        const response = await fetch(`/api/shift-report?date=${encodeURIComponent(iso)}`, {
          credentials: "same-origin",
          cache: "no-store",
        });
        if (!response.ok) return "";
        const data = await response.json();
        if (token !== reportRequestToken) return "";
        const text = data && typeof data.text === "string" ? data.text : "";
        shiftReports[iso] = text;
        if (current === iso) currentReport = text;
        return text;
      } catch (_err) {
        return "";
      } finally {
        if (token === reportRequestToken) setCopyButtonLoading(false);
      }
    }

    function showCopyFeedback() {
      if (!copyButton) return;
      copyButton.classList.add("is-copied");
      window.clearTimeout(copyFeedbackTimer);
      copyFeedbackTimer = window.setTimeout(() => {
        copyButton.classList.remove("is-copied");
      }, 1600);
    }

    async function copyCurrentReport() {
      let text = currentReport || shiftReports[current] || "";
      if (!text) {
        text = await ensureShiftReport(current);
      }
      if (!text) return;

      let copied = await copyTextToClipboard(text);
      if (!copied && navigator.share) {
        try {
          await navigator.share({ text });
          copied = true;
        } catch (shareErr) {
          if (shareErr && shareErr.name === "AbortError") return;
        }
      }
      if (copied) showCopyFeedback();
    }

    function syncDayNavButtons() {
      const index = dates.indexOf(current);
      const prev = document.getElementById("shift-prev-day");
      const next = document.getElementById("shift-next-day");
      const atStart = index <= 0;
      const atEnd = index < 0 || index >= dates.length - 1;
      if (prev) {
        prev.classList.toggle("is-disabled", atStart);
        prev.toggleAttribute("disabled", atStart);
        prev.setAttribute("aria-disabled", atStart ? "true" : "false");
      }
      if (next) {
        next.classList.toggle("is-disabled", atEnd);
        next.toggleAttribute("disabled", atEnd);
        next.setAttribute("aria-disabled", atEnd ? "true" : "false");
      }
    }

    function updateDayPanel(iso) {
      current = iso;
      currentReport = shiftReports[iso] || "";
      const rowShifts = shiftsByDate[iso] || {};
      const people = [];
      Object.keys(rowShifts)
        .forEach((name) => {
          const cell = rowShifts[name] || {};
          const shift = String(cell.shift || "").trim();
          if (!shift || /^(?:-|\.|x|w|wolne|u|urlop|\?)$/i.test(shift)) return;
          people.push({
            name,
            position: String(cell.position || "").trim(),
            position_full: String(cell.position_full || "").trim(),
            position_modifier: String(cell.position_modifier || "").trim(),
            position_watermark: String(cell.position_watermark || "").trim(),
            shift,
            hours: String(cell.hours || "").trim(),
          });
        });
      people.sort((a, b) => {
        const startDiff = parseShiftStartHour(a.shift) - parseShiftStartHour(b.shift);
        if (startDiff !== 0) return startDiff;
        return a.name.localeCompare(b.name, "pl");
      });

      const label = document.getElementById("shift-date-label");
      const display = document.getElementById("shift-date-display");
      const summary = document.getElementById("shifts-summary");
      const roster = document.getElementById("shifts-roster");
      if (label) label.textContent = formatDayLabel(iso);
      if (display) display.textContent = formatDayDisplay(iso);
      if (summary) summary.innerHTML = staffSummaryHtml(staffSummary[iso]);
      if (roster) roster.innerHTML = rosterHtml(people);

      table.querySelectorAll("[data-date]").forEach((node) => {
        node.classList.toggle("is-selected-day", node.getAttribute("data-date") === iso);
      });
      document.querySelectorAll(".grafiki-mobile-cal-day").forEach((node) => {
        node.classList.toggle("is-selected-day", node.getAttribute("data-date") === iso);
      });
      const selectedCalDay = document.querySelector(`.grafiki-mobile-cal-day[data-date="${iso}"]`);
      if (selectedCalDay && typeof selectedCalDay.scrollIntoView === "function") {
        selectedCalDay.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
      }
      syncDayNavButtons();
      ensureShiftReport(iso);
      const index = dates.indexOf(iso);
      if (index >= 0) {
        ensureShiftReport(dates[index - 1]);
        ensureShiftReport(dates[index + 1]);
      }
    }

    function move(delta) {
      const index = dates.indexOf(current);
      if (index < 0) return;
      const next = dates[index + delta];
      if (!next) return;
      updateDayPanel(next);
    }

    document.getElementById("shift-prev-day")?.addEventListener("click", () => move(-1));
    document.getElementById("shift-next-day")?.addEventListener("click", () => move(1));
    copyButton?.addEventListener("click", () => {
      void copyCurrentReport();
    });
    table.addEventListener("click", (event) => {
      const target = event.target.closest("[data-date]");
      if (!target || !table.contains(target)) return;
      const iso = target.getAttribute("data-date");
      if (iso) updateDayPanel(iso);
    });
    document.querySelector(".grafiki-mobile-month")?.addEventListener("click", (event) => {
      const target = event.target.closest("[data-date]");
      if (!target) return;
      const iso = target.getAttribute("data-date");
      if (iso) updateDayPanel(iso);
    });
    initMobileMonthEmployeePicker();
    updateDayPanel(current);
    if (!currentReport) void ensureShiftReport(current);
  }

  window.__ikidsGrafikiInitPanel = initPanel;

  if (!window.__ikidsGrafikiSoftNav) {
    window.__ikidsGrafikiSoftNav = true;
    let navigating = false;

    async function softNavigate(href, { historyMode = "push" } = {}) {
      if (navigating) return;
      const url = new URL(href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname !== "/grafiki") {
        window.location.assign(url.href);
        return;
      }
      if (url.href === window.location.href && historyMode === "push") return;

      navigating = true;
      const main = document.querySelector("main");
      main?.classList.add("is-grafiki-loading");
      try {
        const response = await fetch(url.href, {
          credentials: "same-origin",
          cache: "no-store",
          headers: { "X-IKids-Navigation": "1" },
        });
        if (!response.ok) throw new Error("Bad response");
        const html = await response.text();
        const doc = new DOMParser().parseFromString(html, "text/html");
        const nextMain = doc.querySelector("main");
        if (!nextMain) throw new Error("Missing main");

        const nextFilters = nextMain.querySelector(".grafiki-filters");
        const nextLayout = nextMain.querySelector(".grafiki-layout");
        const nextEmpty = nextMain.querySelector(".grafiki-empty");
        const currentFilters = document.querySelector(".grafiki-filters");
        const currentLayout = document.querySelector(".grafiki-layout");
        const currentEmpty = document.querySelector(".grafiki-empty");

        if (nextFilters) {
          if (currentFilters) currentFilters.replaceWith(document.importNode(nextFilters, true));
          else main?.prepend(document.importNode(nextFilters, true));
        }

        if (nextLayout) {
          replaceNode(currentLayout || currentEmpty, nextLayout);
          currentEmpty?.remove();
        } else if (nextEmpty) {
          replaceNode(currentEmpty || currentLayout, nextEmpty);
          currentLayout?.remove();
        }

        applyGrafikDataScripts(nextMain);
        if (historyMode === "push") {
          window.history.pushState({ ikidsGrafiki: true }, "", url.href);
        } else if (historyMode === "replace") {
          window.history.replaceState({ ikidsGrafiki: true }, "", url.href);
        }
        initPanel();
        const tablePanel = document.querySelector(".grafiki-table-panel");
        tablePanel?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } catch (_err) {
        window.location.assign(url.href);
      } finally {
        navigating = false;
        main?.classList.remove("is-grafiki-loading");
      }
    }

    window.__ikidsGrafikiSoftNavigate = softNavigate;

    document.addEventListener("click", (event) => {
      if (!document.body.classList.contains("page-schedules")) return;
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      const link = event.target.closest("a.schedule-control");
      if (!link || link.classList.contains("is-disabled") || link.getAttribute("aria-disabled") === "true") {
        return;
      }
      let url;
      try {
        url = new URL(link.href, window.location.href);
      } catch (_err) {
        return;
      }
      if (url.origin !== window.location.origin || url.pathname !== "/grafiki") return;
      event.preventDefault();
      softNavigate(url.href, { historyMode: "push" });
    });

    window.addEventListener("popstate", () => {
      if (!document.body.classList.contains("page-schedules")) return;
      if (window.location.pathname !== "/grafiki") return;
      softNavigate(window.location.href, { historyMode: "none" });
    });
  }

  initPanel();
})();
</script>
"""



def render_schedules_page(
    role: str = "manager",
    day: str = "today",
    query: dict[str, list[str]] | None = None,
    *,
    navigation: bool = False,
) -> bytes:
    query = query or {}
    schedule = load_live_schedule()
    entries = schedule.get("entries", [])
    entries_list = entries if isinstance(entries, list) else []
    months = schedule_available_months(entries_list)
    requested_month = query.get("month", [""])[0]
    current_month = schedule_month_key(current_app_date())
    selected_month = requested_month if requested_month in months else (current_month if current_month in months else (months[0] if months else ""))
    requested_department = query.get("department", ["animatorzy"])[0]
    department_keys = {key for key, _ in SCHEDULE_DEPARTMENTS}
    selected_department = requested_department if requested_department in department_keys else "animatorzy"
    selected_view = query.get("view", ["week"])[0]
    if selected_view not in {"week", "month"}:
        selected_view = "week"
    weeks = schedule_available_weeks(entries_list, selected_month, selected_department) if selected_month else []
    requested_week = query.get("week", [""])[0]
    selected_week = requested_week if requested_week in weeks else schedule_default_week(weeks, selected_month)

    error = str(schedule.get("error") or "")
    status_markup = ""
    if not schedule.get("ok"):
        status_markup = f'<div class="alert error">Nie udało się pobrać grafików: {escape(error)}</div>'
    day = normalize_day(day)
    home_href = hub_home_href(day)
    controls = render_schedule_controls(
        role=role,
        day=day,
        selected_department=selected_department,
        selected_month=selected_month,
        selected_week=selected_week,
        selected_view=selected_view,
        months=months,
        weeks=weeks,
        home_href=home_href,
        entries=entries_list,
    )
    period_bar = render_schedule_period_bar(
        role=role,
        day=day,
        selected_department=selected_department,
        selected_month=selected_month,
        selected_week=selected_week,
        selected_view=selected_view,
        months=months,
        weeks=weeks,
        entries=entries_list,
    )
    selected_department_label = dict(SCHEDULE_DEPARTMENTS).get(selected_department, selected_department)
    refresh_href = schedule_url(
        role=role,
        day=day,
        department=selected_department,
        month=selected_month,
        week=selected_week,
        view=selected_view,
    )

    if selected_view == "week":
        week_entries = schedule_filtered_entries(
            entries_list,
            department=selected_department,
            month=selected_month,
            week=selected_week,
        )
        model = build_grafik_week_model(week_entries, selected_week, month=selected_month)
        title = schedule_week_label(selected_week) if selected_week else "Tydzień"
    else:
        month_entries = schedule_filtered_entries(
            entries_list,
            department=selected_department,
            month=selected_month,
        )
        model = build_grafik_month_model(month_entries, selected_month)
        title = schedule_month_label(selected_month)

    shift_reports: dict[str, str] = {}
    initial_date = ""
    schedule_rows = model.get("schedule_rows", [])
    if isinstance(schedule_rows, list):
        today_iso = current_app_date().isoformat()
        if any(isinstance(row, dict) and row.get("date") == today_iso for row in schedule_rows):
            initial_date = today_iso
        elif schedule_rows:
            first_row = schedule_rows[0]
            if isinstance(first_row, dict):
                initial_date = str(first_row.get("date") or "")
    if initial_date:
        shift_reports = build_shift_reports_for_dates([initial_date], entries_list)

    report_dates = [
        str(row.get("date"))
        for row in model.get("schedule_rows", [])
        if isinstance(row, dict) and row.get("date")
    ]
    staff_summary_by_date = build_staff_summary_by_date(
        report_dates,
        entries_list,
        department=selected_department,
    )

    grid = render_schedule_grafik_grid(
        model,
        title=title,
        subtitle=selected_department_label,
        role=role,
        day=day,
        department=selected_department,
        months=months,
        weeks=weeks,
        week=selected_week,
        view=selected_view,
        controls_html=controls,
        period_bar_html=period_bar,
        home_href=home_href,
        refresh_href=refresh_href,
        shift_reports=shift_reports,
        staff_summary_by_date=staff_summary_by_date,
    )
    content = ("" if navigation else grafik4600_assets()) + status_markup + grid + grafik4600_script()
    if navigation:
        document = f"<!doctype html><html lang=\"pl\"><body><main>{content}</main></body></html>"
        return document.encode("utf-8")
    return page_template(
        content,
        role=role,
        day=day,
        page_class="page-schedules",
        logo_href=hub_home_href(day),
        hub="grafiki",
    )


def render_inventory_page(day: str = "today", message: str = "") -> bytes:
    day = normalize_day(day)
    today = current_app_date()
    inventory.auto_issue_due_lines(today=today, role="system")
    home_href = hub_home_href(day)
    day_q = day_query(selected_day(day))
    items = inventory.list_inventory_items()
    shopping = inventory.list_shopping_lines()
    issues = inventory.list_upcoming_lines(today=today, days=21)
    due = inventory.list_issue_lines(today=today)

    # Merge upcoming + due unique by line id, prefer due list for status display.
    seen: set[int] = set()
    issue_rows: list[DbRow] = []
    for row in due + issues + inventory.list_manual_issue_lines():
        line_id = int(row["id"])
        if line_id in seen:
            continue
        seen.add(line_id)
        issue_rows.append(row)

    category_chips = ['<button type="button" class="is-active" data-inventory-category="">Wszystkie</button>']
    for key in INVENTORY_CATEGORIES:
        label = INVENTORY_CATEGORY_LABELS.get(key, key)
        category_chips.append(
            f'<button type="button" data-inventory-category="{escape(key)}">{escape(label)}</button>'
        )
    filters_markup = f"""
<div class="inventory-filters" data-inventory-filters>
  <input type="search" class="inventory-filters__search" placeholder="Szukaj po nazwie, opisie, EAN…" autocomplete="off" data-inventory-search aria-label="Szukaj w inwenturze">
  <div class="inventory-filter-chips" role="group" aria-label="Kategorie inwentury">
    {"".join(category_chips)}
  </div>
</div>
"""

    item_cards = []
    for item in items:
        item_id = int(item["id"])
        category_key = str(item.get("category") or "")
        category = INVENTORY_CATEGORY_LABELS.get(category_key, category_key)
        description = str(item.get("description") or "").strip()
        ean = str(item.get("ean") or "").strip()
        name = str(item.get("name") or "")
        qty = int(item.get("qty_available") or 0)
        desc_html = f'<p class="inventory-card__meta">{escape(description)}</p>' if description else ""
        ean_html = f'<p class="inventory-card__ean">EAN {escape(ean)}</p>' if ean else ""
        search_blob = " ".join(part for part in [category, name, description, ean] if part).lower()
        item_cards.append(
            f"""
<article class="inventory-card" data-inventory-card data-category="{escape(category_key)}" data-search="{escape(search_blob)}">
  <div class="inventory-card__head">
    <span class="inventory-card__kicker">{escape(category)}</span>
    <span class="inventory-card__qty" title="Wolne sztuki">{escape(qty)}</span>
  </div>
  <h3 class="inventory-card__title">{escape(name)}</h3>
  {desc_html}
  {ean_html}
  <details class="inventory-edit">
    <summary>Edytuj pozycję</summary>
    <form class="inventory-edit-form" method="post" action="/inventory/item/update?day={escape(day_q)}">
      <input type="hidden" name="item_id" value="{item_id}">
      <label>
        Kategoria
        <select name="category" required>{render_inventory_category_options(category_key)}</select>
      </label>
      <label>
        Stan
        <input type="number" name="qty_available" min="0" max="5000" value="{escape(qty)}" required inputmode="numeric">
      </label>
      <label class="full">
        Nazwa
        <input type="text" name="name" maxlength="120" value="{escape(name)}" required autocomplete="off">
      </label>
      <label class="full">
        Kod EAN
        <input type="text" name="ean" maxlength="32" value="{escape(ean)}" inputmode="numeric" pattern="[0-9]*" autocomplete="off">
      </label>
      <label class="full">
        Opis
        <input type="text" name="description" maxlength="300" value="{escape(description)}" autocomplete="off">
      </label>
      <div class="inventory-edit-actions">
        <button type="submit">Zapisz zmiany</button>
      </div>
    </form>
  </details>
</article>
"""
        )
    items_markup = (
        f'<div class="inventory-list" data-inventory-list>{"".join(item_cards)}</div>'
        if item_cards
        else '<p class="inventory-empty" data-inventory-empty>Brak pozycji w katalogu. Dodaj stan poniżej.</p>'
    )

    shop_cards = []
    for line in shopping:
        start = format_date(line.get("reservation_start_at")) if line.get("reservation_start_at") else ""
        child = line.get("birthday_child_name") or ""
        reservation_id = line.get("reservation_id")
        is_manual = reservation_id is None
        line_id = int(line["id"])
        purchased = int(line.get("purchased") or 0)
        action_value = "0" if purchased else "1"
        action_label = "Cofnij zakup" if purchased else "Zakupiono"
        status_class = "is-bought" if purchased else "is-todo"
        status_label = "Zakupione" if purchased else "Do zamówienia"
        category_key = str(line.get("category") or "")
        category = INVENTORY_CATEGORY_LABELS.get(category_key, category_key)
        description = str(line.get("description") or "").strip()
        name = str(line.get("name") or "")
        qty_to_order = int(line.get("qty_to_order") or 0)
        kicker = "Ręczne" if is_manual else f"{start} · {child or '—'}"
        meta_bits = [str(category), f"× {qty_to_order}"]
        if description:
            meta_bits.append(description)
        banquet_link = ""
        if not is_manual and reservation_id is not None:
            banquet_link = (
                f'<a class="button secondary" href="{escape(link_for("organizer", day, edit=int(reservation_id)))}">Bankiet</a>'
            )
        remove_form = ""
        if is_manual:
            remove_form = f"""
    <form method="post" action="/inventory/shopping/delete?day={escape(day_q)}">
      <input type="hidden" name="line_id" value="{line_id}">
      <button type="submit" class="button secondary">Usuń</button>
    </form>
"""
        search_blob = " ".join(part for part in [category, name, description, kicker, "zakupy"] if part).lower()
        shop_cards.append(
            f"""
<article class="inventory-card" data-inventory-card data-category="{escape(category_key)}" data-search="{escape(search_blob)}">
  <div class="inventory-card__head">
    <span class="inventory-card__kicker">{escape(kicker)}</span>
    <span class="inventory-status {status_class}">{status_label}</span>
  </div>
  <h3 class="inventory-card__title">{escape(name)}</h3>
  <p class="inventory-card__meta">{escape(" · ".join(meta_bits))}</p>
  <div class="inventory-actions">
    <form method="post" action="/inventory/purchase?day={escape(day_q)}">
      <input type="hidden" name="line_id" value="{line_id}">
      <input type="hidden" name="purchased" value="{action_value}">
      <button type="submit" class="{'button secondary' if purchased else ''}">{action_label}</button>
    </form>
    {banquet_link}
    {remove_form}
  </div>
  <details class="inventory-edit">
    <summary>Edytuj pozycję</summary>
    <form class="inventory-edit-form" method="post" action="/inventory/line/update?day={escape(day_q)}">
      <input type="hidden" name="line_id" value="{line_id}">
      <input type="hidden" name="source" value="shopping">
      <label>
        Kategoria
        <select name="category" required>{render_inventory_category_options(category_key)}</select>
      </label>
      <label>
        Ilość
        <input type="number" name="qty_to_order" min="1" max="500" value="{escape(qty_to_order)}" required inputmode="numeric">
      </label>
      <label class="full">
        Nazwa
        <input type="text" name="name" maxlength="120" value="{escape(name)}" required autocomplete="off">
      </label>
      <label class="full">
        Opis
        <input type="text" name="description" maxlength="300" value="{escape(description)}" autocomplete="off">
      </label>
      <div class="inventory-edit-actions">
        <button type="submit">Zapisz zmiany</button>
      </div>
    </form>
  </details>
</article>
"""
        )
    shopping_markup = (
        f'<div class="inventory-list" data-inventory-list>{"".join(shop_cards)}</div>'
        if shop_cards
        else '<p class="inventory-empty" data-inventory-empty>Lista zakupów jest pusta.</p>'
    )

    issue_cards = []
    for line in issue_rows:
        start = format_date(line.get("reservation_start_at")) if line.get("reservation_start_at") else ""
        child = line.get("birthday_child_name") or ""
        reservation_id = line.get("reservation_id")
        is_manual = reservation_id is None
        line_id = int(line["id"])
        issued = int(line.get("issued") or 0)
        status_class = "is-issued" if issued else "is-open"
        status_label = "Wydano" if issued else "Do wydania"
        action_value = "0" if issued else "1"
        action_label = "Cofnij wydanie" if issued else "Wydaj"
        category_key = str(line.get("category") or "")
        category = INVENTORY_CATEGORY_LABELS.get(category_key, category_key)
        description = str(line.get("description") or "").strip()
        name = str(line.get("name") or "")
        qty = int(line.get("qty") or 0)
        kicker = "Ręczne" if is_manual else f"{start} · {child or '—'}"
        meta_bits = [str(category), f"× {qty}"]
        if description:
            meta_bits.append(description)
        if int(line.get("qty_to_order") or 0) > 0:
            meta_bits.append("zakupione" if int(line.get("purchased") or 0) else "czekamy na zakup")
        banquet_link = ""
        if not is_manual and reservation_id is not None:
            banquet_link = (
                f'<a class="button secondary" href="{escape(link_for("organizer", day, edit=int(reservation_id)))}">Bankiet</a>'
            )
        remove_form = ""
        if is_manual:
            remove_form = f"""
    <form method="post" action="/inventory/shopping/delete?day={escape(day_q)}">
      <input type="hidden" name="line_id" value="{line_id}">
      <button type="submit" class="button secondary">Usuń</button>
    </form>
"""
        search_blob = " ".join(part for part in [category, name, description, kicker, "wydania"] if part).lower()
        issue_cards.append(
            f"""
<article class="inventory-card" data-inventory-card data-category="{escape(category_key)}" data-search="{escape(search_blob)}">
  <div class="inventory-card__head">
    <span class="inventory-card__kicker">{escape(kicker)}</span>
    <span class="inventory-status {status_class}">{status_label}</span>
  </div>
  <h3 class="inventory-card__title">{escape(name)}</h3>
  <p class="inventory-card__meta">{escape(" · ".join(meta_bits))}</p>
  <div class="inventory-actions">
    <form method="post" action="/inventory/issue?day={escape(day_q)}">
      <input type="hidden" name="line_id" value="{line_id}">
      <input type="hidden" name="issued" value="{action_value}">
      <button type="submit" class="{'button secondary' if issued else ''}">{action_label}</button>
    </form>
    {banquet_link}
    {remove_form}
  </div>
  <details class="inventory-edit">
    <summary>Edytuj pozycję</summary>
    <form class="inventory-edit-form" method="post" action="/inventory/line/update?day={escape(day_q)}">
      <input type="hidden" name="line_id" value="{line_id}">
      <input type="hidden" name="source" value="issue">
      <label>
        Kategoria
        <select name="category" required>{render_inventory_category_options(category_key)}</select>
      </label>
      <label>
        Ilość
        <input type="number" name="qty" min="1" max="500" value="{escape(qty)}" required inputmode="numeric">
      </label>
      <label class="full">
        Nazwa
        <input type="text" name="name" maxlength="120" value="{escape(name)}" required autocomplete="off">
      </label>
      <label class="full">
        Opis
        <input type="text" name="description" maxlength="300" value="{escape(description)}" autocomplete="off">
      </label>
      <div class="inventory-edit-actions">
        <button type="submit">Zapisz zmiany</button>
      </div>
    </form>
  </details>
</article>
"""
        )
    issues_markup = (
        f'<div class="inventory-list" data-inventory-list>{"".join(issue_cards)}</div>'
        if issue_cards
        else '<p class="inventory-empty" data-inventory-empty>Brak wydań w najbliższych dniach.</p>'
    )

    items_count = len(items)
    shopping_count = len(shopping)
    issues_count = len(issue_rows)
    items_count_label = f"{items_count} pozycji" if items_count != 1 else "1 pozycja"
    shopping_count_label = f"{shopping_count} pozycji" if shopping_count != 1 else "1 pozycja"
    issues_count_label = f"{issues_count} pozycji" if issues_count != 1 else "1 pozycja"

    shopping_add_form = f"""
<details class="inventory-add-block" id="inventory-shopping-add" open>
  <summary>Dodaj ręcznie do listy zakupów</summary>
  <form class="inventory-add-form" method="post" action="/inventory/shopping/add?day={escape(day_q)}">
    <label>
      Kategoria
      <select name="category" required>{render_inventory_category_options()}</select>
    </label>
    <label>
      Nazwa
      <input type="text" name="name" maxlength="120" required placeholder="np. Balony pastelowe" autocomplete="off">
    </label>
    <label>
      Ilość
      <input type="number" name="qty" min="1" max="500" value="1" required inputmode="numeric">
    </label>
    <label class="full">
      Opis
      <input type="text" name="description" maxlength="300" placeholder="Opcjonalny opis" autocomplete="off">
    </label>
    <button type="submit" class="inventory-add-submit">Dodaj do zakupów</button>
  </form>
</details>
"""

    issues_add_form = f"""
<details class="inventory-add-block" id="inventory-issues-add" open>
  <summary>Dodaj ręcznie do wydań</summary>
  <form class="inventory-add-form" method="post" action="/inventory/issue/add?day={escape(day_q)}">
    <label>
      Kategoria
      <select name="category" required>{render_inventory_category_options()}</select>
    </label>
    <label>
      Nazwa
      <input type="text" name="name" maxlength="120" required placeholder="np. Piniata na dziś" autocomplete="off">
    </label>
    <label>
      Ilość
      <input type="number" name="qty" min="1" max="500" value="1" required inputmode="numeric">
    </label>
    <label class="full">
      Opis
      <input type="text" name="description" maxlength="300" placeholder="Opcjonalny opis" autocomplete="off">
    </label>
    <button type="submit" class="inventory-add-submit">Dodaj do wydań</button>
  </form>
</details>
"""

    add_form = f"""
<div class="inventory-add-block" id="inventory-add">
  <p class="inventory-add-block__title">Dodaj / dolicz stan</p>
  <div class="inventory-scan-row">
    <button type="button" class="button" id="inventory-scan-start">Skanuj kod kreskowy</button>
  </div>
  <p class="inventory-scan-status" id="inventory-scan-status" aria-live="polite"></p>
  <div class="inventory-new-ean" id="inventory-new-ean" hidden>
    <p class="inventory-new-ean__title">Nowy produkt — podaj nazwę</p>
    <form class="inventory-add-form" method="post" action="/inventory/item?day={escape(day_q)}" id="inventory-new-ean-form">
      <input type="hidden" name="ean" id="inventory-new-ean-code" value="">
      <label>
        Kategoria
        <select name="category" required>{render_inventory_category_options()}</select>
      </label>
      <label>
        Nazwa
        <input type="text" name="name" maxlength="120" required placeholder="Nazwa produktu" autocomplete="off">
      </label>
      <label>
        Ilość
        <input type="number" name="qty" min="1" max="500" value="1" required inputmode="numeric">
      </label>
      <label class="full">
        Opis
        <input type="text" name="description" maxlength="300" placeholder="Opcjonalny opis" autocomplete="off">
      </label>
      <p class="inventory-card__ean full" id="inventory-new-ean-label"></p>
      <div class="inventory-scan-row full">
        <button type="submit" class="inventory-add-submit">Utwórz i dodaj</button>
        <button type="button" class="button secondary" id="inventory-new-ean-cancel">Anuluj</button>
      </div>
    </form>
  </div>
  <form class="inventory-add-form" method="post" action="/inventory/item?day={escape(day_q)}" id="inventory-manual-form">
    <label>
      Kategoria
      <select name="category" required>{render_inventory_category_options()}</select>
    </label>
    <label>
      Nazwa
      <input type="text" name="name" maxlength="120" required placeholder="np. Balony podstawowe" autocomplete="off">
    </label>
    <label>
      Ilość
      <input type="number" name="qty" min="1" max="500" value="1" required inputmode="numeric">
    </label>
    <label class="full">
      Kod EAN / kreskowy
      <input type="text" name="ean" maxlength="32" inputmode="numeric" pattern="[0-9]*" placeholder="Opcjonalnie — skan lub wpis" autocomplete="off">
    </label>
    <label class="full">
      Opis
      <input type="text" name="description" maxlength="300" placeholder="Opcjonalny opis" autocomplete="off">
    </label>
    <button type="submit" class="inventory-add-submit">Dodaj / dolicz stan</button>
  </form>
</div>
<div class="inventory-scan-overlay" id="inventory-scan-overlay" hidden aria-hidden="true">
  <div class="inventory-scan-overlay__head">
    <h3>Skanuj produkt</h3>
    <button type="button" class="button secondary" id="inventory-scan-close">Zamknij</button>
  </div>
  <div class="inventory-scan-overlay__body">
    <div id="inventory-scan-reader"></div>
  </div>
  <p class="inventory-scan-overlay__hint">Skieruj kamerę na kod kreskowy. Znany kod doliczy stan; nowy poprosi o nazwę.</p>
</div>
"""

    content = f"""
<div class="toolbar">{render_date_toolbar("home", day, hub="inwentura")}</div>
<div class="inventory-page">
  <section class="role-board inventory-board inventory-board--intro">
    <div class="section-head">
      <div>
        <h2>Inwentura</h2>
        <p class="subtitle">Stan magazynu, lista zakupów i wydania na bankiety.</p>
      </div>
      <a class="button secondary" href="{escape(home_href)}">← Strona główna</a>
    </div>
    <nav class="inventory-jump" aria-label="Sekcje inwentury">
      <a href="#inventory-stock">Stan</a>
      <a href="#inventory-shopping">Zakupy{f' ({shopping_count})' if shopping_count else ''}</a>
      <a href="#inventory-issues">Wydania{f' ({issues_count})' if issues_count else ''}</a>
    </nav>
    {filters_markup}
  </section>

  <section class="role-board inventory-board" id="inventory-stock">
    <div class="section-head">
      <div>
        <h2>Stan magazynu</h2>
        <p class="subtitle">Wolne sztuki dostępne do rezerwacji na bankiety. Edytuj pozycje ręcznie lub skanuj EAN.</p>
      </div>
      <span class="count">{escape(items_count_label)}</span>
    </div>
    <div class="inventory-body">
      {items_markup}
      {add_form}
    </div>
  </section>

  <section class="role-board inventory-board" id="inventory-shopping">
    <div class="section-head">
      <div>
        <h2>Lista zakupów</h2>
        <p class="subtitle">Pozycje brakujące w stanie oraz ręczne zakupy — oznacz jako zakupione.</p>
      </div>
      <span class="count">{escape(shopping_count_label)}</span>
    </div>
    <div class="inventory-body">
      {shopping_markup}
      {shopping_add_form}
    </div>
  </section>

  <section class="role-board inventory-board" id="inventory-issues">
    <div class="section-head">
      <div>
        <h2>Wydania</h2>
        <p class="subtitle">W dniu bankietu pozycje oznaczają się automatycznie jako wydane. Możesz dodać i edytować ręcznie.</p>
      </div>
      <span class="count">{escape(issues_count_label)}</span>
    </div>
    <div class="inventory-body">
      {issues_markup}
      {issues_add_form}
    </div>
  </section>
</div>
{inventory_scan_script()}
{inventory_filters_script()}
"""
    return page_template(
        content,
        message=message,
        role="home",
        day=day,
        page_class="page-inventory",
        logo_href=home_href,
        hub="inwentura",
    )


def inventory_scan_script() -> str:
    return """
<script>
(() => {
  const startBtn = document.getElementById("inventory-scan-start");
  const closeBtn = document.getElementById("inventory-scan-close");
  const overlay = document.getElementById("inventory-scan-overlay");
  const reader = document.getElementById("inventory-scan-reader");
  const statusEl = document.getElementById("inventory-scan-status");
  const newBlock = document.getElementById("inventory-new-ean");
  const newCode = document.getElementById("inventory-new-ean-code");
  const newLabel = document.getElementById("inventory-new-ean-label");
  const newCancel = document.getElementById("inventory-new-ean-cancel");
  if (!startBtn || !overlay || !reader) return;

  let html5QrCode = null;
  let detector = null;
  let videoEl = null;
  let stream = null;
  let rafId = 0;
  let busy = false;
  let lastCode = "";
  let lastAt = 0;

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("is-ok", kind === "ok");
    statusEl.classList.toggle("is-error", kind === "error");
  }

  function showNewProduct(ean) {
    if (!newBlock || !newCode || !newLabel) return;
    newBlock.hidden = false;
    newBlock.classList.add("is-open");
    newCode.value = ean;
    newLabel.textContent = "EAN " + ean;
    const nameInput = newBlock.querySelector('input[name="name"]');
    if (nameInput) {
      nameInput.focus();
    }
  }

  function hideNewProduct() {
    if (!newBlock) return;
    newBlock.hidden = true;
    newBlock.classList.remove("is-open");
    if (newCode) newCode.value = "";
    if (newLabel) newLabel.textContent = "";
  }

  async function postScan(ean) {
    const body = new URLSearchParams();
    body.set("ean", ean);
    body.set("qty", "1");
    const response = await fetch("/inventory/scan", {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "ikids-assign",
      },
      body,
    });
    return response.json();
  }

  async function handleCode(raw) {
    const digits = String(raw || "").replace(/\\D/g, "");
    if (digits.length < 8 || digits.length > 14) return;
    const now = Date.now();
    if (digits === lastCode && now - lastAt < 1800) return;
    if (busy) return;
    busy = true;
    lastCode = digits;
    lastAt = now;
    try {
      const data = await postScan(digits);
      if (!data || !data.ok) {
        setStatus((data && data.message) || "Błąd skanu.", "error");
        return;
      }
      if (data.status === "increased") {
        const name = (data.item && data.item.name) || "produkt";
        const qty = data.item && data.item.qty_available;
        setStatus("Dodano +1: " + name + (qty != null ? " (stan: " + qty + ")" : ""), "ok");
        window.setTimeout(() => { window.location.reload(); }, 700);
        return;
      }
      if (data.status === "unknown") {
        await stopScanner();
        setStatus("Nowy kod — podaj nazwę produktu.", "error");
        showNewProduct(data.ean || digits);
        return;
      }
      setStatus((data && data.message) || "Nie udało się zeskanować.", "error");
    } catch (err) {
      setStatus("Brak połączenia ze skanerem.", "error");
    } finally {
      busy = false;
    }
  }

  async function stopNative() {
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    if (videoEl) {
      videoEl.remove();
      videoEl = null;
    }
    detector = null;
  }

  async function stopHtml5() {
    if (!html5QrCode) return;
    try {
      if (html5QrCode.isScanning) await html5QrCode.stop();
    } catch (_) {}
    try {
      await html5QrCode.clear();
    } catch (_) {}
    html5QrCode = null;
  }

  async function stopScanner() {
    await stopNative();
    await stopHtml5();
    overlay.classList.remove("is-open");
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }

  async function loadHtml5Qrcode() {
    if (window.Html5Qrcode) return true;
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js";
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("html5-qrcode"));
      document.head.appendChild(script);
    });
    return Boolean(window.Html5Qrcode);
  }

  async function startNative() {
    if (!("BarcodeDetector" in window) || !navigator.mediaDevices?.getUserMedia) {
      return false;
    }
    const formats = [
      "ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code",
    ];
    try {
      detector = new window.BarcodeDetector({ formats });
    } catch (_) {
      try {
        detector = new window.BarcodeDetector();
      } catch (_) {
        return false;
      }
    }
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: "environment" } },
    });
    videoEl = document.createElement("video");
    videoEl.setAttribute("playsinline", "true");
    videoEl.muted = true;
    videoEl.autoplay = true;
    videoEl.srcObject = stream;
    reader.replaceChildren(videoEl);
    await videoEl.play();

    const tick = async () => {
      if (!detector || !videoEl) return;
      try {
        if (videoEl.readyState >= 2) {
          const codes = await detector.detect(videoEl);
          if (codes && codes.length) {
            const value = codes[0].rawValue || "";
            if (value) await handleCode(value);
          }
        }
      } catch (_) {}
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return true;
  }

  async function startHtml5() {
    await loadHtml5Qrcode();
    reader.replaceChildren();
    html5QrCode = new window.Html5Qrcode("inventory-scan-reader");
    await html5QrCode.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: { width: 260, height: 140 }, aspectRatio: 1.777 },
      (decoded) => { handleCode(decoded); },
      () => {}
    );
    return true;
  }

  async function startScanner() {
    hideNewProduct();
    setStatus("Uruchamianie kamery…");
    overlay.hidden = false;
    overlay.classList.add("is-open");
    overlay.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    reader.replaceChildren();
    try {
      const okNative = await startNative();
      if (!okNative) await startHtml5();
      setStatus("Celuj w kod kreskowy.");
    } catch (err) {
      await stopScanner();
      setStatus("Brak dostępu do kamery (wymagane HTTPS / uprawnienia).", "error");
    }
  }

  startBtn.addEventListener("click", () => { startScanner(); });
  if (closeBtn) closeBtn.addEventListener("click", () => { stopScanner(); });
  if (newCancel) newCancel.addEventListener("click", () => { hideNewProduct(); setStatus(""); });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopScanner();
  });
})();
</script>
"""


def inventory_filters_script() -> str:
    return """
<script>
(() => {
  const root = document.querySelector("[data-inventory-filters]");
  if (!root) return;
  const searchInput = root.querySelector("[data-inventory-search]");
  const chips = Array.from(root.querySelectorAll("[data-inventory-category]"));
  const cards = Array.from(document.querySelectorAll("[data-inventory-card]"));
  let activeCategory = "";

  function applyFilters() {
    const query = String(searchInput?.value || "").trim().toLowerCase();
    cards.forEach((card) => {
      const category = card.getAttribute("data-category") || "";
      const haystack = card.getAttribute("data-search") || "";
      const categoryOk = !activeCategory || category === activeCategory;
      const searchOk = !query || haystack.includes(query);
      card.hidden = !(categoryOk && searchOk);
    });
    document.querySelectorAll("[data-inventory-list]").forEach((list) => {
      const visible = list.querySelectorAll("[data-inventory-card]:not([hidden])").length;
      let empty = list.parentElement?.querySelector("[data-inventory-filter-empty]");
      if (visible === 0) {
        if (!empty) {
          empty = document.createElement("p");
          empty.className = "inventory-empty";
          empty.setAttribute("data-inventory-filter-empty", "");
          empty.textContent = "Brak pozycji dla wybranego filtra.";
          list.insertAdjacentElement("afterend", empty);
        }
        empty.hidden = false;
      } else if (empty) {
        empty.hidden = true;
      }
    });
  }

  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      activeCategory = chip.getAttribute("data-inventory-category") || "";
      chips.forEach((other) => other.classList.toggle("is-active", other === chip));
      applyFilters();
    });
  });
  searchInput?.addEventListener("input", applyFilters);
})();
</script>
"""


def render_schema_summary() -> str:
    fields = [
        "reservations.id",
        "start_at jako godzina startu imprezy / end_at techniczne",
        "children_count / adults_count",
        "guest_total / reservation_type",
        "parent_name / parent_phone",
        "birthday_child_name / birthday_child_age",
        "child_location / adult_location",
        "cooperation_enabled",
        "animation_enabled / animation_type / animation_at / animations_json",
        "cake_enabled / cake_theme / cake_at",
        "cake_weight / cake_sponge / cake_filling / cake_cream / cake_image_data / cake_candle",
        "fruit_enabled / fruit_plates / fruit_at",
        "culinary_workshops_enabled / culinary_workshops_type / culinary_workshops_at",
        "pinata_enabled / pinata_theme / pinata_at",
        "mascot_enabled / mascot_type / mascot_at",
        "balloons_enabled / balloons_description / balloons_at",
        "notes",
        "status / cancellation_reason",
        "created_at / updated_at",
        "reservation_history z pełnym snapshotem JSON",
        "inventory_items / inventory_lines / inventory_movements",
    ]
    return f"""
<section>
  <div class="section-head">
    <div>
      <h2>Proponowana struktura bazy danych</h2>
      <p class="subtitle">Baza produkcyjna: PostgreSQL (Supabase).</p>
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
    hub: str = "",
) -> bytes:
    page_role = normalize_page_role(role)
    role = normalize_role(role)
    day = normalize_day(day)
    hub = normalize_hub(hub)
    errors = errors or {}
    target_day = selected_day(day)
    rows = get_reservations_for_day(target_day)

    if page_role == "home":
        content = (
            f'<div class="toolbar">{render_date_toolbar("home", day)}</div>'
            f'<div class="home-summary">{render_metrics(rows, day)}{render_hub_choice(day)}</div>'
        )
        return page_template(
            content,
            message=message,
            errors=errors,
            role=page_role,
            day=day,
        )

    if role != "organizer":
        edit_id = None

    if values is None and edit_id:
        row = get_reservation(edit_id)
        values = row_to_form_values(row) if row else default_form_values(target_day)
        if row is None:
            message = "Nie znaleziono rezerwacji do edycji."

    if values is None:
        values = default_form_values(target_day)

    content = render_nav(page_role, day, hub="urodziny")
    if role == "organizer":
        content += f"""
<div class="stack organizer-layout">
  {render_organizer_tools(role, day)}
  {render_form(values, errors, role, day, include_plan=True)}
  {render_role_view(role, rows, day)}
</div>
{room_plan_script()}
"""
    elif role == "manager":
        content += f"""
<div class="stack manager-layout">
  {render_role_view(role, rows, day)}
  {render_room_plan(values, errors, compact=True)}
</div>
"""
    else:
        content += f"""
<div class="stack">
  {render_role_view(role, rows, day)}
</div>
"""

    return page_template(
        content,
        message=message,
        errors=errors,
        role=page_role,
        day=day,
        hub="urodziny",
    )


def room_plan_script() -> str:
    catalog_options = render_inventory_catalog_options().replace("\\", "\\\\").replace("`", "\\`")
    script = """
<script>
(() => {
  const form = document.getElementById("reservation-form");
  if (!form) return;

  const dateInput = document.getElementById("reservation_date");
  const childSelect = document.getElementById("child_location");
  const adultSelect = document.getElementById("adult_location");
  const locationPicker = document.getElementById("location-picker");
  const locationConfirmBtn = document.getElementById("location-confirm-btn");
  const childLocationBadge = document.getElementById("child-location-badge");
  const adultLocationBadge = document.getElementById("adult-location-badge");
  const locationSummaryChild = document.getElementById("location-summary-child");
  const locationSummaryAdult = document.getElementById("location-summary-adult");
  const locationChips = Array.from(document.querySelectorAll(".location-chip[data-location]"));
  const locationAccordions = Array.from(document.querySelectorAll(".location-accordion"));
  const EMPTY_LOCATION = "Brak";
  const statusSelect = document.getElementById("status");
  const cancellationReason = document.getElementById("cancellation_reason");
  const cancellationReasonField = document.getElementById("cancellation_reason_field");
  const roomNodes = Array.from(document.querySelectorAll(".room-node"));
  const tableNodes = Array.from(document.querySelectorAll(".table-node"));
  const nodes = [...roomNodes, ...tableNodes];
  const catalogItems = Array.from(document.querySelectorAll(".service-catalog-item"));
  const overlapNotice = document.getElementById("service-overlap-notice");
  const birthdayList = document.getElementById("birthday-children-list");
  const addBirthdayBtn = document.getElementById("add-birthday-child");
  const animationList = document.getElementById("animation-list");
  const addAnimationBtn = document.getElementById("add-animation-row");
  const cakeImageInput = document.getElementById("cake_image_data");
  const cakeCameraInput = document.getElementById("cake_camera_input");
  const cakeGalleryInput = document.getElementById("cake_gallery_input");
  const cakePhotoTrigger = document.getElementById("cake_photo_trigger");
  const cakePhotoMenu = document.getElementById("cake_photo_menu");
  const cakeCameraBtn = document.getElementById("cake_camera_btn");
  const cakeGalleryBtn = document.getElementById("cake_gallery_btn");
  const cakePhotoPreview = document.getElementById("cake_photo_preview");
  const cakePhotoRemove = document.getElementById("cake_photo_remove");
  const reservationTypeInputs = Array.from(document.querySelectorAll('input[name="reservation_type"]'));
  const childrenCountInput = document.querySelector('input[name="children_count"]');
  const adultsCountInput = document.querySelector('input[name="adults_count"]');
  const guestTotalInput = document.querySelector('input[name="guest_total"]');
  let timer = null;

  function allTimeInputs() {
    return Array.from(document.querySelectorAll("[data-time-input]"));
  }

  function reservationType() {
    return reservationTypeInputs.find((input) => input.checked)?.value || "banquet";
  }

  function isTableReservation() {
    return reservationType() === "table";
  }

  function syncReservationType() {
    const tableMode = isTableReservation();
    form.classList.toggle("is-table-reservation", tableMode);
    if (childrenCountInput) childrenCountInput.required = !tableMode;
    if (adultsCountInput) adultsCountInput.required = !tableMode;
    if (guestTotalInput) guestTotalInput.required = tableMode;
    birthdayList?.querySelectorAll("input").forEach((input) => {
      input.required = !tableMode;
      input.disabled = tableMode;
    });
    if (childSelect) {
      childSelect.disabled = tableMode;
      if (tableMode) childSelect.value = EMPTY_LOCATION;
    }
    if (!tableMode && guestTotalInput && !guestTotalInput.value) {
      const children = Number(childrenCountInput?.value || 0);
      const adults = Number(adultsCountInput?.value || 0);
      if (children || adults) guestTotalInput.value = String(children + adults);
    }
    updateLocationSummary();
    validateServiceOverlaps();
  }

  function getAdultSelectValues() {
    return Array.from(adultSelect?.selectedOptions || []).map((option) => option.value);
  }

  function setAdultSelectValues(values) {
    if (!adultSelect) return;
    const wanted = new Set(values);
    Array.from(adultSelect.options).forEach((option) => {
      option.selected = wanted.has(option.value);
    });
  }

  function toggleAdultTable(location) {
    if (!location) return;
    const values = getAdultSelectValues();
    const index = values.indexOf(location);
    if (index >= 0) values.splice(index, 1);
    else values.push(location);
    setAdultSelectValues(values);
    paintSelectedLocations();
  }

  function setChildLocation(location) {
    if (!childSelect || !location) return;
    childSelect.value = location;
    paintSelectedLocations();
  }

  function toggleChildLocation(location) {
    if (!childSelect || !location) return;
    if (location === EMPTY_LOCATION || childSelect.value === location) {
      childSelect.value = EMPTY_LOCATION;
    } else {
      childSelect.value = location;
    }
    paintSelectedLocations();
  }

  function finalizeLocations() {
    if (isTableReservation()) {
      if (childSelect) childSelect.value = EMPTY_LOCATION;
      return;
    }
    if (childSelect && (!childSelect.value || childSelect.value.trim() === "")) {
      childSelect.value = EMPTY_LOCATION;
    }
  }

  function paintSelectedLocations() {
    const childValue = childSelect?.value;
    const activeAdults = new Set(getAdultSelectValues());
    roomNodes.forEach((node) => {
      node.classList.toggle(
        "is-selected",
        childValue && childValue !== EMPTY_LOCATION && node.dataset.location === childValue
      );
    });
    tableNodes.forEach((node) => {
      node.classList.toggle("is-selected", activeAdults.has(node.dataset.location));
    });
    locationChips.forEach((chip) => {
      const location = chip.dataset.location;
      if (chip.classList.contains("location-chip-adult-none")) {
        chip.classList.toggle("is-selected", activeAdults.size === 0);
        return;
      }
      const isAdultPanel = chip.closest(".location-panel-adult");
      if (isAdultPanel && chip.classList.contains("location-chip-table")) {
        chip.classList.toggle("is-selected", activeAdults.has(location));
        return;
      }
      if (!isAdultPanel) {
        chip.classList.toggle("is-selected", childValue === location);
      }
    });
    updateLocationSummary();
  }

  function shortChildLabel(value) {
    if (!value || value === EMPTY_LOCATION) return EMPTY_LOCATION;
    return value;
  }

  function updateAdultZoneMeta() {
    const activeAdults = new Set(getAdultSelectValues());
    locationAccordions.forEach((accordion) => {
      const meta = accordion.querySelector(".location-accordion-meta");
      const chips = Array.from(accordion.querySelectorAll(".location-chip-table"));
      if (!meta || !chips.length) return;
      const selectedCount = chips.filter((chip) => activeAdults.has(chip.dataset.location)).length;
      meta.textContent = selectedCount ? `${selectedCount}/${chips.length}` : String(chips.length);
    });
  }

  function updateLocationSummary() {
    const childValue = childSelect?.value || EMPTY_LOCATION;
    const adultValues = getAdultSelectValues();
    const childLabel = isTableReservation() ? "Rezerwacja stolika" : (childValue === EMPTY_LOCATION ? EMPTY_LOCATION : childValue);
    const adultLabel = adultValues.length ? adultValues.join(", ") : EMPTY_LOCATION;

    if (childLocationBadge) childLocationBadge.textContent = isTableReservation() ? "Stolik" : shortChildLabel(childValue);
    if (adultLocationBadge) {
      adultLocationBadge.textContent = adultValues.length ? `${adultValues.length} stol.` : EMPTY_LOCATION;
    }
    if (locationSummaryChild) {
      locationSummaryChild.textContent = childLabel;
      locationSummaryChild.classList.toggle("is-empty", !isTableReservation() && childValue === EMPTY_LOCATION);
    }
    if (locationSummaryAdult) {
      locationSummaryAdult.textContent = adultLabel;
      locationSummaryAdult.classList.toggle("is-empty", !adultValues.length);
    }
    updateAdultZoneMeta();
    if (locationPicker) locationPicker.classList.remove("is-confirmed");
    if (locationConfirmBtn) {
      locationConfirmBtn.classList.remove("is-confirmed");
      locationConfirmBtn.textContent = "Zatwierdź lokalizacje";
    }
  }

  function bindLocationAccordions() {
    locationAccordions.forEach((accordion) => {
      const head = accordion.querySelector(".location-accordion-head");
      if (!head) return;
      head.addEventListener("click", () => {
        const willOpen = !accordion.classList.contains("is-open");
        accordion.classList.toggle("is-open", willOpen);
        head.setAttribute("aria-expanded", willOpen ? "true" : "false");
      });
    });
  }

  function bindLocationChips() {
    locationChips.forEach((chip) => {
      chip.addEventListener("click", () => {
        if (chip.disabled || chip.classList.contains("is-busy")) {
          window.alert("Ta lokalizacja jest zajęta w wybranym dniu.");
          return;
        }
        const location = chip.dataset.location;
        if (chip.classList.contains("location-chip-adult-none")) {
          setAdultSelectValues([]);
          paintSelectedLocations();
          return;
        }
        if (chip.closest(".location-panel-adult")) {
          toggleAdultTable(location);
          return;
        }
        toggleChildLocation(location);
      });
    });
  }

  function bindLocationRanges() {
    document.querySelectorAll(".location-range-row").forEach((row) => {
      const applyBtn = row.querySelector(".location-range-apply");
      const fromInput = row.querySelector(".location-range-from");
      const toInput = row.querySelector(".location-range-to");
      if (!applyBtn || !fromInput || !toInput) return;
      applyBtn.addEventListener("click", () => {
        const accordion = row.closest(".location-accordion");
        if (!accordion) return;
        const chips = Array.from(accordion.querySelectorAll(".location-chip-table"));
        const numbers = chips
          .map((chip) => Number(chip.dataset.tableNumber))
          .filter((value) => !Number.isNaN(value));
        if (!numbers.length) return;
        let from = Number(fromInput.value);
        let to = Number(toInput.value);
        if (!from) from = Math.min(...numbers);
        if (!to) to = Math.max(...numbers);
        if (from > to) {
          const swap = from;
          from = to;
          to = swap;
        }
        const values = new Set(getAdultSelectValues());
        chips.forEach((chip) => {
          const tableNumber = Number(chip.dataset.tableNumber);
          if (
            tableNumber >= from
            && tableNumber <= to
            && !chip.disabled
            && !chip.classList.contains("is-busy")
          ) {
            values.add(chip.dataset.location);
          }
        });
        setAdultSelectValues([...values]);
        paintSelectedLocations();
      });
    });
  }

  function confirmLocations() {
    finalizeLocations();
    updateLocationSummary();
    if (locationPicker) locationPicker.classList.add("is-confirmed");
    if (locationConfirmBtn) {
      locationConfirmBtn.classList.add("is-confirmed");
      locationConfirmBtn.textContent = "Zatwierdzono ✓";
    }
  }

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

  function normalizePhoneText(value) {
    let digits = String(value || "").replace(/\\D/g, "");
    if (digits.startsWith("0048")) digits = digits.slice(4);
    else if (digits.startsWith("48") && digits.length >= 11) digits = digits.slice(2);
    if (digits.startsWith("0") && digits.length === 10) digits = digits.slice(1);
    if (!/^\\d{9}$/.test(digits) || digits[0] === "0") return null;
    return `${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6, 9)}`;
  }

  function normalizePhoneInput(input) {
    if (!input) return false;
    const raw = input.value.trim();
    if (!raw) {
      input.setCustomValidity("Podaj numer telefonu.");
      return false;
    }
    const normalized = normalizePhoneText(raw);
    if (!normalized) {
      input.setCustomValidity("Niepoprawny telefon (9 cyfr, np. 500 000 000).");
      return false;
    }
    input.value = normalized;
    input.setCustomValidity("");
    return true;
  }

  function normalizePersonNameInput(input) {
    if (!input || input.disabled) return true;
    const cleaned = String(input.value || "").trim().replace(/\\s+/g, " ");
    input.value = cleaned;
    if (!input.required && !cleaned) {
      input.setCustomValidity("");
      return true;
    }
    if (cleaned.length < 2) {
      input.setCustomValidity("Imię i nazwisko musi mieć co najmniej 2 znaki.");
      return false;
    }
    if (!/^[A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+([ '\\-][A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+)*$/.test(cleaned)) {
      input.setCustomValidity("Użyj tylko liter, spacji, myślnika lub apostrofu.");
      return false;
    }
    input.setCustomValidity("");
    return true;
  }

  function bindPhoneInput() {
    const phoneInput = document.getElementById("parent_phone") || form.querySelector("[data-phone-input]");
    if (!phoneInput) return;
    phoneInput.addEventListener("blur", () => {
      normalizePhoneInput(phoneInput);
      phoneInput.reportValidity();
    });
    phoneInput.addEventListener("input", () => {
      phoneInput.setCustomValidity("");
    });
  }

  function bindPersonNameInputs() {
    form.querySelectorAll('input[name="parent_name"], input[name="birthday_child_name"]').forEach((input) => {
      if (input.dataset.bound === "1") return;
      input.dataset.bound = "1";
      input.addEventListener("blur", () => {
        normalizePersonNameInput(input);
        input.reportValidity();
      });
      input.addEventListener("input", () => input.setCustomValidity(""));
    });
  }

  function toMinutes(value) {
    const normalized = normalizeClockText(value);
    if (!normalized) return null;
    const [hours, minutes] = normalized.split(":").map(Number);
    if (Number.isNaN(hours) || Number.isNaN(minutes)) return null;
    return hours * 60 + minutes;
  }

  function serviceWindow(input) {
    if (!input || input.disabled || !input.value) return null;
    const duration = Number(input.dataset.durationMinutes || 0);
    const startMinutes = toMinutes(input.value);
    if (startMinutes === null || !duration) return null;
    return { startMinutes, endMinutes: startMinutes + duration, duration };
  }

  function windowsOverlap(a, b) {
    if (!a || !b || !a.duration || !b.duration) return false;
    return a.startMinutes < b.endMinutes && b.startMinutes < a.endMinutes;
  }

  function validateServiceOverlaps() {
    const timeInputs = allTimeInputs();
    const windows = timeInputs
      .map((input) => serviceWindow(input))
      .filter((windowValue) => windowValue && windowValue.duration);
    timeInputs.forEach((input) => input.classList.remove("overlap-error"));
    document.querySelectorAll(".overlap-hint").forEach((hint) => hint.classList.add("is-hidden"));
    if (overlapNotice) overlapNotice.classList.add("is-hidden");

    let hasOverlap = false;
    for (let i = 0; i < windows.length; i += 1) {
      for (let j = i + 1; j < windows.length; j += 1) {
        if (windowsOverlap(windows[i], windows[j])) {
          hasOverlap = true;
        }
      }
    }

    if (hasOverlap) {
      if (overlapNotice) overlapNotice.classList.remove("is-hidden");
      timeInputs.forEach((input) => {
        const windowValue = serviceWindow(input);
        if (!windowValue || !windowValue.duration) return;
        const conflict = windows.some((other) => other !== windowValue && windowsOverlap(windowValue, other));
        if (!conflict) return;
        input.classList.add("overlap-error");
        input.closest(".service-catalog-body")?.querySelector(".overlap-hint")?.classList.remove("is-hidden");
      });
    }
    validateStageBlockConflicts();
    return !hasOverlap;
  }

  const stageBlockNotice = document.getElementById("stage-block-notice");
  const stageBlockAck = document.getElementById("stage_block_acknowledged");
  const stageBlockStart = Number(stageBlockNotice?.dataset.startMinutes || (17 * 60 + 45));
  const stageBlockEnd = Number(stageBlockNotice?.dataset.endMinutes || (18 * 60 + 15));
  const stageBlockMessage = stageBlockNotice?.dataset.message
    || "Ta atrakcja nachodzi na powitanie solenizantów (Koło Marzeń) 17:45–18:15.";
  const stageBlockConfirm = stageBlockNotice?.dataset.confirm
    || (stageBlockMessage + " Zatwierdzić mimo to i pozwolić na nakładanie się godzin?");

  function overlapsStageBlock(windowValue) {
    if (!windowValue || !windowValue.duration) return false;
    return windowValue.startMinutes < stageBlockEnd && windowValue.endMinutes > stageBlockStart;
  }

  function stageBlockConflictInputs() {
    return allTimeInputs().filter((input) => overlapsStageBlock(serviceWindow(input)));
  }

  function validateStageBlockConflicts() {
    const conflicts = stageBlockConflictInputs();
    allTimeInputs().forEach((input) => input.classList.remove("stage-block-error"));
    if (stageBlockNotice) stageBlockNotice.classList.add("is-hidden");
    if (!conflicts.length) {
      if (stageBlockAck) stageBlockAck.value = "";
      return false;
    }
    if (stageBlockNotice) stageBlockNotice.classList.remove("is-hidden");
    conflicts.forEach((input) => input.classList.add("stage-block-error"));
    return true;
  }

  function updateTimeEndLabel(input) {
    const end = input?.closest(".service-time")?.querySelector(".service-end");
    if (!input || !input.value || input.disabled) {
      if (end) end.textContent = "";
      return;
    }
    const duration = Number(input.dataset.durationMinutes || 0);
    if (!duration) return;
    const startMinutes = toMinutes(input.value);
    if (startMinutes === null) {
      if (end) end.textContent = "";
      return;
    }
    if (end) end.textContent = `koniec ${formatTime(startMinutes + duration)}`;
  }

  function updateCatalogItem(item) {
    const checkbox = item.querySelector(".service-enabled-input");
    const body = item.querySelector(".service-catalog-body");
    const toggleBtn = item.querySelector(".service-catalog-head .service-toggle-btn");
    const timeInputs = Array.from(item.querySelectorAll("[data-time-input]"));
    const extraInputs = Array.from(item.querySelectorAll(".service-extra select, .service-extra input"));
    const enabled = checkbox?.checked;
    item.classList.toggle("is-open", !!enabled);
    if (body) body.classList.toggle("is-hidden", !enabled);
    if (toggleBtn) {
      toggleBtn.classList.toggle("is-active", !!enabled);
      toggleBtn.setAttribute("aria-pressed", enabled ? "true" : "false");
      toggleBtn.setAttribute("aria-label", enabled ? "Usuń dodatek" : "Dodaj dodatek");
    }
    timeInputs.forEach((timeInput) => { timeInput.disabled = !enabled; });
    extraInputs.forEach((input) => { input.disabled = !enabled; });
    if (!enabled) {
      timeInputs.forEach((timeInput) => updateTimeEndLabel(timeInput));
      validateServiceOverlaps();
      return;
    }
    timeInputs.forEach((timeInput) => updateTimeEndLabel(timeInput));
    validateServiceOverlaps();
  }

  function applyAvailability(locations) {
    nodes.forEach((node) => {
      const info = locations[node.dataset.location] || { status: "free", label: "Wolne", color: "", tip: "" };
      node.classList.toggle("is-busy", info.status === "occupied");
      if (info.color) node.style.setProperty("--node-color", info.color);
      else node.style.removeProperty("--node-color");
      node.dataset.tip = info.tip || "";
      const title = node.querySelector("title");
      if (title) title.textContent = info.label;
    });
    locationChips.forEach((chip) => {
      const info = locations[chip.dataset.location] || { status: "free", label: "Wolne" };
      const busy = info.status === "occupied";
      chip.classList.toggle("is-busy", busy);
      chip.disabled = busy;
      chip.title = busy ? info.label : (chip.dataset.location || "");
    });
    paintSelectedLocations();
  }

  function refreshAvailability() {
    if (!dateInput?.value) return;
    const params = new URLSearchParams({ date: dateInput.value });
    const reservationId = document.getElementById("reservation_id")?.value;
    if (reservationId) params.set("exclude_id", reservationId);
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

  function bindBirthdayChildren() {
    if (!birthdayList) return;
    birthdayList.querySelectorAll(".remove-birthday-child").forEach((button) => {
      if (button.dataset.bound === "1") return;
      button.dataset.bound = "1";
      button.addEventListener("click", () => {
        button.closest(".birthday-child-row")?.remove();
      });
    });
    birthdayList.querySelectorAll('input[name="birthday_child_name"]').forEach((input) => {
      if (input.dataset.bound === "1") return;
      input.dataset.bound = "1";
      input.addEventListener("blur", () => {
        normalizePersonNameInput(input);
        input.reportValidity();
      });
      input.addEventListener("input", () => input.setCustomValidity(""));
    });
  }

  function animationOptionsHtml() {
    const existing = animationList?.querySelector("select[name='animation_type']");
    if (existing) return existing.innerHTML;
    return '<option value="">Wybierz animację</option>';
  }

  function bindAnimationRow(row) {
    row.querySelector(".remove-animation-row")?.addEventListener("click", () => {
      const rows = animationList?.querySelectorAll(".animation-row") || [];
      if (rows.length <= 1) {
        row.querySelectorAll("select").forEach((select) => { select.selectedIndex = 0; });
        row.querySelectorAll("input").forEach((input) => { input.value = ""; });
        updateTimeEndLabel(row.querySelector("[data-time-input]"));
        validateServiceOverlaps();
        return;
      }
      row.remove();
      const item = animationList?.closest(".service-catalog-item");
      if (item) updateCatalogItem(item);
      else validateServiceOverlaps();
    });
    row.querySelectorAll("[data-time-input]").forEach((input) => {
      input.addEventListener("input", () => {
        const item = input.closest(".service-catalog-item");
        if (item) updateCatalogItem(item);
        else {
          updateTimeEndLabel(input);
          validateServiceOverlaps();
        }
      });
      input.addEventListener("blur", () => {
        normalizeTimeInput(input);
        const item = input.closest(".service-catalog-item");
        if (item) updateCatalogItem(item);
      });
    });
  }

  function bindAnimations() {
    if (!animationList) return;
    animationList.querySelectorAll(".animation-row").forEach((row) => bindAnimationRow(row));
  }

  function setCakePhotoPreview(dataUrl) {
    if (!cakeImageInput || !cakePhotoPreview) return;
    const image = cakePhotoPreview.querySelector("img");
    cakeImageInput.value = dataUrl || "";
    cakePhotoPreview.classList.toggle("is-hidden", !dataUrl);
    if (image) {
      if (dataUrl) image.src = dataUrl;
      else image.removeAttribute("src");
    }
  }

  function compressCakePhoto(file) {
    return new Promise((resolve, reject) => {
      if (!file || !file.type || !file.type.startsWith("image/")) {
        reject(new Error("Wybierz zdjęcie."));
        return;
      }
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Nie udało się odczytać zdjęcia."));
      reader.onload = () => {
        const source = String(reader.result || "");
        const image = new Image();
        image.onerror = () => reject(new Error("Nie udało się przygotować zdjęcia."));
        image.onload = () => {
          const maxSide = 1100;
          const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(image.width * scale));
          canvas.height = Math.max(1, Math.round(image.height * scale));
          const context = canvas.getContext("2d");
          if (!context) {
            reject(new Error("Nie udało się przygotować zdjęcia."));
            return;
          }
          context.drawImage(image, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL("image/jpeg", 0.78));
        };
        image.src = source;
      };
      reader.readAsDataURL(file);
    });
  }

  function bindCakePhotoInput(input) {
    input?.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file) return;
      compressCakePhoto(file)
        .then((dataUrl) => {
          if (String(dataUrl).length > 1500000) {
            window.alert("Zdjęcie jest za duże. Wybierz mniejszy plik.");
            return;
          }
          setCakePhotoPreview(String(dataUrl));
        })
        .catch((error) => window.alert(error.message || "Nie udało się dodać zdjęcia."));
      input.value = "";
    });
  }

  function bindCakePhotoControls() {
    if (!cakeImageInput) return;
    cakePhotoTrigger?.addEventListener("click", () => {
      cakePhotoMenu?.classList.toggle("is-hidden");
    });
    cakeCameraBtn?.addEventListener("click", () => {
      cakePhotoMenu?.classList.add("is-hidden");
      cakeCameraInput?.click();
    });
    cakeGalleryBtn?.addEventListener("click", () => {
      cakePhotoMenu?.classList.add("is-hidden");
      cakeGalleryInput?.click();
    });
    cakePhotoRemove?.addEventListener("click", () => setCakePhotoPreview(""));
    bindCakePhotoInput(cakeCameraInput);
    bindCakePhotoInput(cakeGalleryInput);
  }

  if (addBirthdayBtn && birthdayList) {
    addBirthdayBtn.addEventListener("click", () => {
      const row = document.createElement("div");
      row.className = "birthday-child-row";
      row.innerHTML = `
        <label>Imię solenizanta<input name="birthday_child_name" required minlength="2" maxlength="80" pattern="[A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+([ '\\-][A-Za-zÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+)*" title="Tylko litery, spacje, myślnik lub apostrof" autocomplete="off"></label>
        <label>Wiek<input type="number" name="birthday_child_age" min="1" max="18" step="1" required title="Wiek od 1 do 18 lat"></label>
        <button type="button" class="button secondary remove-birthday-child" aria-label="Usuń solenizanta">Usuń</button>
      `;
      birthdayList.appendChild(row);
      bindBirthdayChildren();
      bindPersonNameInputs();
    });
    bindBirthdayChildren();
  }

  const inventoryList = document.getElementById("inventory-lines-list");
  const addInventoryBtn = document.getElementById("add-inventory-line");
  const inventoryCatalogOptions = `INVENTORY_CATALOG_OPTIONS`;

  function bindInventoryRow(row) {
    const categorySelect = row.querySelector(".inventory-category");
    const itemSelect = row.querySelector(".inventory-item-id");
    const nameInput = row.querySelector(".inventory-name");
    const descriptionInput = row.querySelector(".inventory-description");
    const removeBtn = row.querySelector(".remove-inventory-line");
    itemSelect?.addEventListener("change", () => {
      const option = itemSelect.selectedOptions[0];
      if (!option || !option.value) return;
      if (categorySelect && option.dataset.category) categorySelect.value = option.dataset.category;
      if (nameInput && option.dataset.name) nameInput.value = option.dataset.name;
      if (descriptionInput && option.dataset.description && !descriptionInput.value) {
        descriptionInput.value = option.dataset.description;
      }
    });
    categorySelect?.addEventListener("change", () => {
      if (!itemSelect) return;
      const selectedId = itemSelect.value;
      Array.from(itemSelect.options).forEach((option) => {
        if (!option.value) return;
        const match = !categorySelect.value || option.dataset.category === categorySelect.value;
        option.hidden = !match;
        if (!match && option.value === selectedId) itemSelect.value = "";
      });
    });
    categorySelect?.dispatchEvent(new Event("change"));
    removeBtn?.addEventListener("click", () => row.remove());
  }

  function bindInventoryLines() {
    inventoryList?.querySelectorAll(".inventory-line-row").forEach((row) => bindInventoryRow(row));
  }

  if (addInventoryBtn && inventoryList) {
    addInventoryBtn.addEventListener("click", () => {
      const row = document.createElement("div");
      row.className = "inventory-line-row";
      row.innerHTML = `
        <label>
          Kategoria
          <select name="inventory_category" class="inventory-category">
            <option value="">Kategoria</option>
            <option value="pinata">Piniata</option>
            <option value="balloons">Balony</option>
            <option value="themed_set">Zestaw tematyczny</option>
          </select>
        </label>
        <label>
          Katalog
          <select name="inventory_item_id" class="inventory-item-id">${inventoryCatalogOptions}</select>
        </label>
        <label>
          Nazwa
          <input type="text" name="inventory_name" class="inventory-name" maxlength="120" placeholder="np. Piniata Jednorożec" enterkeyhint="next">
        </label>
        <label>
          Ilość
          <input type="number" name="inventory_qty" class="inventory-qty" min="1" max="500" value="1" placeholder="1" inputmode="numeric" enterkeyhint="next">
        </label>
        <label class="full">
          Opis
          <input type="text" name="inventory_description" class="inventory-description" maxlength="300" placeholder="Jak ma wyglądać zestaw / motyw" enterkeyhint="done">
        </label>
        <button type="button" class="button secondary remove-inventory-line" aria-label="Usuń pozycję">Usuń</button>
      `;
      inventoryList.appendChild(row);
      bindInventoryRow(row);
    });
    bindInventoryLines();
  }

  if (addAnimationBtn && animationList) {
    addAnimationBtn.addEventListener("click", () => {
      const duration = animationList.dataset.animationDuration || "60";
      const row = document.createElement("div");
      row.className = "animation-row";
      row.innerHTML = `
        <label class="service-extra">
          Rodzaj animacji
          <select name="animation_type">${animationOptionsHtml()}</select>
        </label>
        <label class="service-extra">
          Start
          <div class="service-time">
            <input type="text" inputmode="numeric" autocomplete="off" placeholder="00:00" maxlength="5" name="animation_at" value="" data-time-input="1" data-duration-minutes="${duration}" aria-label="Start animacji">
            <span class="service-end"></span>
          </div>
        </label>
        <button type="button" class="service-toggle-btn is-active remove-animation-row" aria-label="Usuń animację">
          <span class="service-toggle-plus" aria-hidden="true">+</span>
          <span class="service-toggle-minus" aria-hidden="true">−</span>
        </button>
      `;
      const select = row.querySelector("select[name='animation_type']");
      if (select) select.selectedIndex = 0;
      animationList.appendChild(row);
      bindAnimationRow(row);
      const item = animationList.closest(".service-catalog-item");
      if (item) updateCatalogItem(item);
      row.querySelector("[data-time-input]")?.focus();
    });
    bindAnimations();
  }

  bindCakePhotoControls();

  catalogItems.forEach((item) => {
    const checkbox = item.querySelector(".service-enabled-input");
    const toggleBtn = item.querySelector(".service-catalog-head .service-toggle-btn");
    toggleBtn?.addEventListener("click", () => {
      const enabling = !(checkbox?.checked);
      if (checkbox) checkbox.checked = enabling;
      if (!enabling) {
        item.querySelectorAll("[data-time-input]").forEach((input) => { input.value = ""; });
        item.querySelectorAll(".service-extra input, .service-extra select").forEach((input) => {
          if (input.tagName === "SELECT") input.selectedIndex = 0;
          else input.value = "";
        });
        item.querySelectorAll(".overlap-hint").forEach((hint) => hint.classList.add("is-hidden"));
        if (item.dataset.service === "animation_enabled" && animationList) {
          const rows = Array.from(animationList.querySelectorAll(".animation-row"));
          rows.slice(1).forEach((row) => row.remove());
        }
        if (item.dataset.service === "cake_enabled") {
          setCakePhotoPreview("");
        }
      }
      updateCatalogItem(item);
      if (enabling) {
        const focusTarget = item.querySelector("[data-time-input]");
        focusTarget?.focus();
      }
    });
    checkbox?.addEventListener("change", () => updateCatalogItem(item));
    item.querySelectorAll("[data-time-input]").forEach((input) => {
      if (input.closest(".animation-row")) return;
      input.addEventListener("input", () => updateCatalogItem(item));
    });
    updateCatalogItem(item);
  });

  allTimeInputs().forEach((input) => {
    if (input.closest(".animation-row")) return;
    input.addEventListener("blur", () => {
      normalizeTimeInput(input);
      const item = input.closest(".service-catalog-item");
      if (item) updateCatalogItem(item);
    });
  });

  roomNodes.forEach((node) => {
    node.style.cursor = "pointer";
    node.addEventListener("click", () => {
      if (node.classList.contains("is-busy")) return;
      toggleChildLocation(node.dataset.location);
    });
  });

  tableNodes.forEach((node) => {
    node.style.cursor = "pointer";
    node.addEventListener("click", () => {
      if (node.classList.contains("is-busy")) return;
      toggleAdultTable(node.dataset.location);
    });
  });

  childSelect?.addEventListener("change", paintSelectedLocations);
  adultSelect?.addEventListener("change", paintSelectedLocations);
  reservationTypeInputs.forEach((input) => input.addEventListener("change", syncReservationType));
  locationConfirmBtn?.addEventListener("click", confirmLocations);
  bindLocationAccordions();
  bindLocationChips();
  bindLocationRanges();

  form.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.keyCode !== 13) return;
    if (event.target.tagName === "TEXTAREA") return;
    event.preventDefault();
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const inventoryBlock = target.closest(".inventory-lines-block");
    if (!inventoryBlock || !target.matches("input, select")) return;
    // Visual reading order: left → right within a row, then down to the next row.
    const rowTolerance = 16;
    const fields = Array.from(
      inventoryBlock.querySelectorAll("input:not([type='hidden']), select")
    )
      .filter((el) => !el.disabled && el.offsetParent !== null)
      .sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        if (Math.abs(ra.top - rb.top) > rowTolerance) return ra.top - rb.top;
        return ra.left - rb.left;
      });
    const index = fields.indexOf(target);
    if (index === -1) return;
    if (index < fields.length - 1) {
      const next = fields[index + 1];
      next.focus();
      if (next.matches("input") && typeof next.select === "function") {
        try { next.select(); } catch (_) {}
      }
      return;
    }
    addInventoryBtn?.focus();
  });

  form.addEventListener("submit", (event) => {
    allTimeInputs().forEach(normalizeTimeInput);
    const phoneInput = document.getElementById("parent_phone") || form.querySelector("[data-phone-input]");
    if (phoneInput && !normalizePhoneInput(phoneInput)) {
      event.preventDefault();
      phoneInput.reportValidity();
      phoneInput.focus();
      return;
    }
    let nameInvalid = null;
    form.querySelectorAll('input[name="parent_name"], input[name="birthday_child_name"]').forEach((input) => {
      if (!normalizePersonNameInput(input) && !nameInvalid) nameInvalid = input;
    });
    if (nameInvalid) {
      event.preventDefault();
      nameInvalid.reportValidity();
      nameInvalid.focus();
      return;
    }
    finalizeLocations();
    if (stageBlockAck) stageBlockAck.value = "";
    if (!validateServiceOverlaps()) {
      event.preventDefault();
      return;
    }
    if (validateStageBlockConflicts()) {
      if (!window.confirm(stageBlockConfirm)) {
        event.preventDefault();
        return;
      }
      if (stageBlockAck) stageBlockAck.value = "1";
    }
    const actionLabel = form.querySelector('button[type="submit"]')?.textContent?.trim() || "zapisać rezerwację";
    if (!window.confirm(`Czy na pewno chcesz ${actionLabel.toLowerCase()}?`)) {
      event.preventDefault();
    }
  });

  bindPhoneInput();
  bindPersonNameInputs();
  paintSelectedLocations();
  syncReservationType();
  dateInput?.addEventListener("input", scheduleRefresh);
  if (statusSelect) statusSelect.addEventListener("change", syncCancellationRequirement);
  syncCancellationRequirement();
  refreshAvailability();
})();
</script>
<style>
  input.overlap-error,
  input.stage-block-error {
    border-color: var(--danger);
    outline-color: rgba(251, 113, 133, 0.35);
  }

  .stage-block-notice {
    border-color: color-mix(in srgb, #d97706 35%, var(--line));
    background: color-mix(in srgb, #f59e0b 12%, white);
    color: #92400e;
  }
</style>
"""
    return script.replace("INVENTORY_CATALOG_OPTIONS", catalog_options)


def render_schema_page(role: str = "manager", day: str = "today") -> bytes:
    sql = """
CREATE TABLE reservations (
  id BIGSERIAL PRIMARY KEY,
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  children_count INT NOT NULL CHECK (children_count > 0),
  adults_count INT NOT NULL CHECK (adults_count >= 0),
  guest_total INT,
  reservation_type TEXT NOT NULL DEFAULT 'banquet',
  parent_name TEXT NOT NULL,
  parent_phone TEXT,
  birthday_child_name TEXT NOT NULL,
  birthday_child_age INT NOT NULL,
  child_location TEXT NOT NULL,
  adult_location TEXT NOT NULL,
  animation_enabled BOOLEAN NOT NULL DEFAULT false,
  animation_type TEXT,
  animation_at TIMESTAMPTZ,
  animations_json TEXT,
  cake_enabled BOOLEAN NOT NULL DEFAULT false,
  cake_theme TEXT,
  cake_weight TEXT,
  cake_sponge TEXT,
  cake_filling TEXT,
  cake_cream TEXT,
  cake_image_data TEXT,
  cake_candle TEXT,
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
  balloons_enabled BOOLEAN NOT NULL DEFAULT false,
  balloons_description TEXT,
  balloons_at TIMESTAMPTZ,
  notes TEXT NOT NULL DEFAULT '',
  cooperation_enabled BOOLEAN NOT NULL DEFAULT false,
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
    <pre style="white-space: pre-wrap; border: 1px solid var(--line); padding: 14px; background: #ffffff; color: #000000; overflow-x: auto;"><code>{escape(sql.strip())}</code></pre>
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
    back_link = (
        f'<a class="button secondary" href="{link_for(role, day, edit=reservation_id)}">Wróć do edycji</a>'
        if can_modify_reservations(role)
        else f'<a class="button secondary" href="{link_for(role, day)}">Wróć do panelu</a>'
    )
    content = render_nav(role, day) + f"""
<section>
  <div class="section-head">
    <div>
      <h2>Historia rezerwacji</h2>
      <p class="subtitle banquet-header-inline">{render_banquet_header(row)}</p>
    </div>
    {back_link}
  </div>
  {body}
</section>
"""
    return page_template(content, role=role, day=day)


def parse_multipart_form(content_type: str, body: bytes) -> dict[str, list[str]]:
    """Parse multipart/form-data into the same shape as urllib.parse.parse_qs."""
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8", "replace") + b"\r\n\r\n" + body
    )
    parsed: dict[str, list[str]] = {}
    if not message.is_multipart():
        return parsed
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            text = str(part.get_payload() or "")
        elif isinstance(payload, bytes):
            text = payload.decode("utf-8", errors="replace")
        else:
            text = str(payload)
        parsed.setdefault(str(name), []).append(text)
    return parsed


def parse_post(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type") or ""
    content_type_lower = content_type.lower()
    multi_keys = {
        "adult_location",
        "birthday_child_name",
        "birthday_child_age",
        "animation_type",
        "animation_at",
        "inventory_category",
        "inventory_item_id",
        "inventory_name",
        "inventory_qty",
        "inventory_description",
    }

    if "multipart/form-data" in content_type_lower:
        parsed = parse_multipart_form(content_type, raw)
    elif "application/json" in content_type_lower:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        parsed = {}
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, list):
                    parsed[str(key)] = ["" if item is None else str(item) for item in value]
                else:
                    parsed[str(key)] = ["" if value is None else str(value)]
    else:
        parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)

    return {key: values if key in multi_keys else values[-1] for key, values in parsed.items()}


def post_field(data: dict[str, object], key: str, default: str = "") -> str:
    return str(data.get(key, default) or default)


def wants_json(handler: BaseHTTPRequestHandler) -> bool:
    accept = (handler.headers.get("Accept") or "").lower()
    requested = (handler.headers.get("X-Requested-With") or "").lower()
    return "application/json" in accept or requested == "ikids-assign"


def csv_response() -> bytes:
    return build_csv_response(
        get_all_reservations(),
        is_table_reservation=is_table_reservation,
        format_date=format_date,
        format_time=format_time,
        display_locations=display_locations,
        animations_from_row=animations_from_row,
        format_service_window=format_service_window,
        is_enabled=is_enabled,
        service_durations=SERVICE_DURATIONS,
        cake_candle_labels=CAKE_CANDLE_LABELS,
        status_labels=STATUS_LABELS,
    )


def icon_spec_for_path(path: str) -> tuple[int, bool] | None:
    """Return (size, solid) for icon URL paths."""
    mapping: dict[str, tuple[int, bool]] = {}
    for size in PWA_ICON_SIZES:
        mapping[f"/app-icon-{size}.png"] = (size, False)
        mapping[f"/app-icon-{size}-solid.png"] = (size, True)
        mapping[f"/static/icon-{size}.png"] = (size, True)
    mapping["/favicon.ico"] = (192, True)
    return mapping.get(path)


class ReservationHandler(BaseHTTPRequestHandler):
    def handle(self) -> None:
        maybe_reload_dev_server()
        return super().handle()

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
        if content_type.startswith("text/html"):
            self.send_header("Cache-Control", "no-store")
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
        if parsed.path in {"/sw.js", "/static/sw.js", "/manifest.webmanifest", "/static/manifest.json", "/offline"}:
            pass
        elif parsed.path == "/api/ready":
            ready = _BOOT_READY.is_set()
            payload = json.dumps(
                {"ready": ready and not _BOOT_ERROR, "error": _BOOT_ERROR or ""},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_bytes(
                payload,
                content_type="application/json; charset=utf-8",
                extra_headers={"Cache-Control": "no-store"},
            )
            return
        elif parsed.path.startswith("/app-icon-") or parsed.path in {"/favicon.ico", "/logo.png", "/menu-logo.png"}:
            pass
        elif not _BOOT_READY.is_set():
            message = _BOOT_ERROR or "Uruchamianie iKids Park…"
            status = HTTPStatus.SERVICE_UNAVAILABLE if _BOOT_ERROR else HTTPStatus.OK
            self.send_bytes(boot_wait_page(message), status=status)
            return

        query = parse_qs(parsed.query)
        role = normalize_page_role(query.get("role", ["home"])[0])
        day = normalize_day(query.get("day", ["today"])[0])

        if parsed.path == "/":
            message = query.get("message", [""])[0]
            edit_values = query.get("edit", [""])[0]
            edit_id = int(edit_values) if edit_values.isdigit() else None
            hub = normalize_hub(query.get("hub", [""])[0])
            if hub == "grafiki":
                self.redirect(default_grafiki_href(day, role=role))
                return
            if hub == "inwentura":
                self.redirect(default_inwentura_href(day))
                return
            if hub == "urodziny" and role == "home":
                self.redirect(hub_home_href(day))
                return
            self.send_bytes(
                render_home(role=role, day=day, message=message, edit_id=edit_id, hub=hub)
            )
            return

        if parsed.path == "/logo.png":
            if LOGO_PATH.exists():
                self.send_bytes(
                    LOGO_PATH.read_bytes(),
                    content_type="image/png",
                    extra_headers={"Cache-Control": "public, max-age=31536000, immutable"},
                )
                return
            self.send_bytes(b"", status=HTTPStatus.NOT_FOUND, content_type="image/png")
            return

        if parsed.path == "/menu-logo.png":
            if MENU_LOGO_PATH.exists():
                self.send_bytes(
                    MENU_LOGO_PATH.read_bytes(),
                    content_type="image/png",
                    extra_headers={"Cache-Control": "public, max-age=31536000, immutable"},
                )
                return
            self.send_bytes(b"", status=HTTPStatus.NOT_FOUND, content_type="image/png")
            return

        if parsed.path == "/room-plan.png":
            if ROOM_PLAN_PNG_PATH.exists():
                self.send_bytes(
                    ROOM_PLAN_PNG_PATH.read_bytes(),
                    content_type="image/png",
                    extra_headers={"Cache-Control": "public, max-age=31536000, immutable"},
                )
                return
            self.send_bytes(b"", status=HTTPStatus.NOT_FOUND, content_type="image/png")
            return

        if parsed.path == "/room-plan.svg":
            if ROOM_PLAN_SVG_PATH.exists():
                self.send_bytes(
                    ROOM_PLAN_SVG_PATH.read_bytes(),
                    content_type="image/svg+xml; charset=utf-8",
                    extra_headers={"Cache-Control": "public, max-age=31536000, immutable"},
                )
                return
            self.send_bytes(b"", status=HTTPStatus.NOT_FOUND, content_type="image/svg+xml; charset=utf-8")
            return

        if parsed.path == "/ca.crt":
            ensure_local_certificate()
            self.send_bytes(
                CA_CERT_PATH.read_bytes(),
                content_type="application/x-x509-ca-cert",
                extra_headers={"Content-Disposition": 'attachment; filename="ikids-local-ca.crt"'},
            )
            return

        if parsed.path in {"/app-icon.svg", "/static/app-icon.svg"}:
            self.send_bytes(b"", status=HTTPStatus.GONE, content_type="text/plain; charset=utf-8")
            return

        icon_spec = icon_spec_for_path(parsed.path)
        if icon_spec is not None:
            size, solid = icon_spec
            self.send_bytes(
                app_icon_png(size, solid=solid),
                content_type="image/png",
                extra_headers={"Cache-Control": f"public, max-age=86400, immutable"},
            )
            return

        if parsed.path in {"/manifest.webmanifest", "/static/manifest.json"}:
            self.send_bytes(
                manifest_response(),
                content_type="application/manifest+json; charset=utf-8",
                extra_headers={"Cache-Control": "no-store"},
            )
            return

        if parsed.path in {"/sw.js", "/static/sw.js"}:
            self.send_bytes(
                service_worker_response(),
                content_type="text/javascript; charset=utf-8",
                extra_headers={
                    "Cache-Control": "no-store",
                    "Service-Worker-Allowed": "/",
                },
            )
            return

        if parsed.path == "/offline":
            self.send_bytes(offline_response())
            return

        if parsed.path == "/schema":
            self.send_bytes(render_schema_page(role=role, day=day))
            return

        if parsed.path in {"/static/grafik4600.css", "/grafik4600.css"}:
            css_path = Path(__file__).resolve().parent / "assets" / "grafik4600.css"
            if css_path.is_file():
                self.send_bytes(
                    css_path.read_bytes(),
                    content_type="text/css; charset=utf-8",
                    extra_headers={"Cache-Control": "no-cache"},
                )
                return
            self.send_error(404)
            return

        if parsed.path == "/grafiki":
            navigation = self.headers.get("X-IKids-Navigation") == "1"
            self.send_bytes(render_schedules_page(role=role, day=day, query=query, navigation=navigation))
            return

        if parsed.path == "/inwentura":
            message = query.get("message", [""])[0]
            self.send_bytes(render_inventory_page(day=day, message=message))
            return

        if parsed.path == "/api/shift-report":
            iso_date = query.get("date", [""])[0]
            try:
                date.fromisoformat(iso_date)
            except ValueError:
                self.send_bytes(
                    json.dumps({"ok": False, "error": "Nieprawidłowa data."}, ensure_ascii=False).encode("utf-8"),
                    status=HTTPStatus.BAD_REQUEST,
                    content_type="application/json; charset=utf-8",
                )
                return
            schedule = load_live_schedule()
            entries = schedule.get("entries", [])
            entries_list = entries if isinstance(entries, list) else []
            reports = build_shift_reports_for_dates([iso_date], entries_list)
            payload = json.dumps(
                {"ok": True, "date": iso_date, "text": reports.get(iso_date, "")},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_bytes(
                payload,
                content_type="application/json; charset=utf-8",
                extra_headers={"Cache-Control": "no-store"},
            )
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
        if parsed.path in {"/static/grafik4600.css", "/grafik4600.css"}:
            css_path = Path(__file__).resolve().parent / "assets" / "grafik4600.css"
            if css_path.is_file():
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/css; charset=utf-8")
                self.send_header("Content-Length", str(css_path.stat().st_size))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                return
            self.send_error(404)
            return

        if parsed.path == "/logo.png":
            self.send_response(HTTPStatus.OK if LOGO_PATH.exists() else HTTPStatus.NOT_FOUND)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            return

        if parsed.path == "/ca.crt":
            ensure_local_certificate()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.end_headers()
            return

        if parsed.path in {"/app-icon.svg", "/static/app-icon.svg"}:
            self.send_response(HTTPStatus.GONE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            return

        if icon_spec_for_path(parsed.path) is not None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "public, max-age=86400, immutable")
            self.end_headers()
            return

        if parsed.path in {"/manifest.webmanifest", "/static/manifest.json"}:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.end_headers()
            return

        if parsed.path in {"/sw.js", "/static/sw.js"}:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            return

        if parsed.path in {"/", "/export", "/schema", "/grafiki", "/inwentura", "/api/availability", "/offline"}:
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
        if not _BOOT_READY.is_set() or _BOOT_ERROR:
            message = _BOOT_ERROR or "Uruchamianie iKids Park…"
            self.send_bytes(boot_wait_page(message), status=HTTPStatus.SERVICE_UNAVAILABLE)
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        raw_role = query.get("role", ["manager"])[0]
        page_role = normalize_page_role(raw_role)
        work_role = normalize_role(raw_role)
        day = normalize_day(query.get("day", ["today"])[0])
        data = parse_post(self)

        if parsed.path == "/reservations":
            if not can_modify_reservations(work_role):
                self.redirect(link_for(page_role, day, message="Brak uprawnień do zapisu rezerwacji."))
                return
            raw_id = post_field(data, "id")
            reservation_id = int(raw_id) if raw_id.isdigit() else None
            values, errors = validate_reservation(data, reservation_id=reservation_id)
            if errors:
                self.send_bytes(
                    render_home(role=work_role, day=day, values=values, errors=errors),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            saved_id = save_reservation(values, role=work_role)
            message = "Rezerwacja została zaktualizowana." if reservation_id else "Rezerwacja została zapisana."
            self.redirect(link_for(work_role, day, edit=saved_id, message=message))
            return

        if parsed.path == "/delete":
            if not can_modify_reservations(work_role):
                self.redirect(link_for(page_role, day, message="Brak uprawnień do usuwania rezerwacji."))
                return
            raw_id = post_field(data, "id")
            if raw_id.isdigit() and delete_reservation(int(raw_id)):
                self.redirect(link_for(work_role, day, message="Rezerwacja została usunięta."))
            else:
                self.redirect(link_for(work_role, day, message="Nie znaleziono rezerwacji do usunięcia."))
            return

        if parsed.path == "/assign-waiter":
            as_json = wants_json(self)

            def respond_waiter(message: str, *, ok: bool, reservation_id: int | None = None) -> None:
                if not as_json:
                    self.redirect(link_for(work_role if ok else page_role, day, message=message))
                    return
                html = ""
                if ok and reservation_id is not None:
                    updated = get_reservation(reservation_id)
                    if updated is not None:
                        html = render_waiter_assignment(updated, work_role, day)
                payload = json.dumps(
                    {"ok": ok, "message": message, "html": html},
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_bytes(
                    payload,
                    status=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
                    content_type="application/json; charset=utf-8",
                    extra_headers={"Cache-Control": "no-store"},
                )

            if not can_assign_waiter(work_role):
                respond_waiter("Brak uprawnień do przypisania kelnera.", ok=False)
                return
            raw_id = post_field(data, "id")
            if not raw_id.isdigit():
                respond_waiter("Nie znaleziono rezerwacji.", ok=False)
                return
            reservation_id = int(raw_id)
            waiter = post_field(data, "waiter")
            remove_waiter = post_field(data, "remove_waiter")
            if assign_waiter(
                reservation_id,
                waiter or None,
                work_role,
                remove_waiter=remove_waiter or None,
            ):
                if remove_waiter.strip():
                    message = "Przypisanie kelnera zostało usunięte."
                elif waiter.strip():
                    message = "Kelner został przypisany."
                else:
                    message = "Przypisanie kelnerów zostało usunięte."
                respond_waiter(message, ok=True, reservation_id=reservation_id)
            else:
                respond_waiter("Nie udało się zaktualizować kelnera.", ok=False)
            return

        if parsed.path == "/assign-animator":
            as_json = wants_json(self)

            def respond_animator(
                message: str,
                *,
                ok: bool,
                reservation_id: int | None = None,
                slot: str = "anim:0",
            ) -> None:
                if not as_json:
                    self.redirect(link_for(work_role if ok else page_role, day, message=message))
                    return
                html = ""
                if ok and reservation_id is not None:
                    updated = get_reservation(reservation_id)
                    if updated is not None:
                        html = render_animator_assignment(updated, work_role, day, slot=slot)
                payload = json.dumps(
                    {"ok": ok, "message": message, "html": html},
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_bytes(
                    payload,
                    status=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
                    content_type="application/json; charset=utf-8",
                    extra_headers={"Cache-Control": "no-store"},
                )

            if not can_assign_animator(work_role):
                respond_animator("Brak uprawnień do przypisania animatora.", ok=False)
                return
            raw_id = post_field(data, "id")
            if not raw_id.isdigit():
                respond_animator("Nie znaleziono rezerwacji.", ok=False)
                return
            reservation_id = int(raw_id)
            animator = post_field(data, "animator")
            remove_animator = post_field(data, "remove_animator")
            slot = post_field(data, "slot", "anim:0") or "anim:0"
            if assign_animator(
                reservation_id,
                animator or None,
                work_role,
                remove_animator=remove_animator or None,
                slot=slot,
            ):
                if remove_animator.strip():
                    message = "Przypisanie animatora zostało usunięte."
                elif animator.strip():
                    message = "Animator został przypisany."
                else:
                    message = "Przypisanie animatorów zostało usunięte."
                respond_animator(message, ok=True, reservation_id=reservation_id, slot=slot)
            else:
                respond_animator("Nie udało się zaktualizować animatora.", ok=False, slot=slot)
            return

        if parsed.path == "/inventory/scan":
            as_json = wants_json(self)

            def respond_scan(payload: dict[str, object], *, ok: bool = True) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if as_json:
                    self.send_bytes(
                        body,
                        status=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
                        content_type="application/json; charset=utf-8",
                        extra_headers={"Cache-Control": "no-store"},
                    )
                    return
                message = str(payload.get("message") or "")
                self.redirect(
                    "/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": message})
                )

            if not can_manage_inventory(work_role):
                respond_scan({"ok": False, "message": "Brak uprawnień do inwentury."}, ok=False)
                return
            ean = str(data.get("ean", "") or "")
            try:
                qty = int(str(data.get("qty", "1") or "1"))
            except ValueError:
                qty = 1
            result = inventory.scan_ean_add(ean, qty=qty, role=work_role)
            status = str(result.get("status") or "")
            if status == "increased":
                item = result.get("item") if isinstance(result.get("item"), dict) else {}
                name = str(item.get("name") or "produkt")
                qty_available = item.get("qty_available")
                message = f"Dodano +{result.get('qty_added', qty)}: {name}"
                if qty_available is not None:
                    message += f" (stan: {qty_available})"
                respond_scan(
                    {
                        "ok": True,
                        "status": "increased",
                        "message": message,
                        "item": item,
                        "ean": result.get("ean"),
                        "qty_added": result.get("qty_added", qty),
                    }
                )
                return
            if status == "unknown":
                respond_scan(
                    {
                        "ok": True,
                        "status": "unknown",
                        "ean": result.get("ean"),
                        "message": "Nowy kod — podaj nazwę produktu.",
                    }
                )
                return
            respond_scan(
                {"ok": False, "status": "error", "message": str(result.get("message") or "Błąd skanu.")},
                ok=False,
            )
            return

        if parsed.path == "/inventory/item":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            category = str(data.get("category", "") or "")
            name = str(data.get("name", "") or "")
            description = str(data.get("description", "") or "")
            ean = str(data.get("ean", "") or "")
            try:
                qty = int(str(data.get("qty", "0") or "0"))
            except ValueError:
                qty = 0
            item_id = inventory.add_or_increase_item(
                category=category,
                name=name,
                qty=qty,
                description=description,
                ean=ean,
                role=work_role,
            )
            if item_id is None:
                self.redirect(
                    "/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": "Nie udało się dodać pozycji."})
                )
                return
            message = "Stan magazynu zaktualizowany."
            if inventory.normalize_ean(ean):
                message = "Produkt zapisany z kodem EAN. Stan zaktualizowany."
            self.redirect(
                "/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": message})
            )
            return

        if parsed.path == "/inventory/item/update":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            raw_id = str(data.get("item_id", "") or "")
            try:
                qty_available = int(str(data.get("qty_available", "0") or "0"))
            except ValueError:
                qty_available = -1
            ok = raw_id.isdigit() and inventory.update_inventory_item(
                int(raw_id),
                category=str(data.get("category", "") or ""),
                name=str(data.get("name", "") or ""),
                description=str(data.get("description", "") or ""),
                ean=str(data.get("ean", "") or ""),
                qty_available=qty_available,
                role=work_role,
            )
            message = "Zapisano zmiany pozycji." if ok else "Nie udało się zapisać pozycji."
            self.redirect("/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": message}))
            return

        if parsed.path == "/inventory/shopping/add":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            try:
                qty = int(str(data.get("qty", "0") or "0"))
            except ValueError:
                qty = 0
            line_id = inventory.add_manual_shopping_item(
                category=str(data.get("category", "") or ""),
                name=str(data.get("name", "") or ""),
                description=str(data.get("description", "") or ""),
                qty=qty,
                role=work_role,
            )
            message = "Dodano pozycję do listy zakupów." if line_id else "Nie udało się dodać do listy zakupów."
            self.redirect(
                "/inwentura?"
                + urlencode({"day": day_query(selected_day(day)), "message": message})
                + "#inventory-shopping"
            )
            return

        if parsed.path == "/inventory/issue/add":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            try:
                qty = int(str(data.get("qty", "0") or "0"))
            except ValueError:
                qty = 0
            line_id = inventory.add_manual_issue_item(
                category=str(data.get("category", "") or ""),
                name=str(data.get("name", "") or ""),
                description=str(data.get("description", "") or ""),
                qty=qty,
                role=work_role,
            )
            message = "Dodano pozycję do wydań." if line_id else "Nie udało się dodać do wydań."
            self.redirect(
                "/inwentura?"
                + urlencode({"day": day_query(selected_day(day)), "message": message})
                + "#inventory-issues"
            )
            return

        if parsed.path == "/inventory/shopping/delete":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            raw_id = str(data.get("line_id", "") or "")
            existing = inventory.get_line(int(raw_id)) if raw_id.isdigit() else None
            was_shopping = bool(existing and int(existing.get("qty_to_order") or 0) > 0)
            ok = raw_id.isdigit() and inventory.delete_manual_line(int(raw_id), role=work_role)
            message = "Usunięto ręczną pozycję." if ok else "Nie udało się usunąć pozycji."
            anchor = "#inventory-shopping" if was_shopping else "#inventory-issues"
            self.redirect(
                "/inwentura?"
                + urlencode({"day": day_query(selected_day(day)), "message": message})
                + anchor
            )
            return

        if parsed.path == "/inventory/line/update":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            raw_id = str(data.get("line_id", "") or "")
            source = str(data.get("source", "") or "")
            qty = None
            qty_to_order = None
            try:
                if "qty" in data and str(data.get("qty") or "").strip() != "":
                    qty = int(str(data.get("qty") or "0"))
            except ValueError:
                qty = None
            try:
                if "qty_to_order" in data and str(data.get("qty_to_order") or "").strip() != "":
                    qty_to_order = int(str(data.get("qty_to_order") or "0"))
            except ValueError:
                qty_to_order = None
            ok = raw_id.isdigit() and inventory.update_inventory_line(
                int(raw_id),
                category=str(data.get("category", "") or ""),
                name=str(data.get("name", "") or ""),
                description=str(data.get("description", "") or ""),
                qty=qty,
                qty_to_order=qty_to_order,
                role=work_role,
            )
            message = "Zapisano zmiany pozycji." if ok else "Nie udało się zapisać pozycji."
            anchor = "#inventory-shopping" if source == "shopping" else "#inventory-issues"
            self.redirect(
                "/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": message}) + anchor
            )
            return

        if parsed.path == "/inventory/purchase":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            raw_id = str(data.get("line_id", "") or "")
            purchased = str(data.get("purchased", "1") or "1") != "0"
            if not raw_id.isdigit() or not inventory.set_line_purchased(int(raw_id), purchased, role=work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Nie udało się zaktualizować zakupu."})
                )
                return
            message = "Oznaczono jako zakupione." if purchased else "Cofnięto status zakupu."
            self.redirect("/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": message}))
            return

        if parsed.path == "/inventory/issue":
            if not can_manage_inventory(work_role):
                self.redirect(
                    "/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": "Brak uprawnień do inwentury."})
                )
                return
            raw_id = str(data.get("line_id", "") or "")
            issued = str(data.get("issued", "1") or "1") != "0"
            if not raw_id.isdigit() or not inventory.set_line_issued(int(raw_id), issued, role=work_role):
                self.redirect(
                    "/inwentura?"
                    + urlencode({"day": day_query(selected_day(day)), "message": "Nie udało się zaktualizować wydania."})
                )
                return
            message = "Oznaczono jako wydane." if issued else "Cofnięto wydanie."
            self.redirect("/inwentura?" + urlencode({"day": day_query(selected_day(day)), "message": message}))
            return

        payload = json.dumps({"error": "Unsupported route"}, ensure_ascii=False).encode("utf-8")
        self.send_bytes(payload, status=HTTPStatus.NOT_FOUND, content_type="application/json; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}")


class ThreadingHTTPSServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        ssl_context: ssl.SSLContext,
    ) -> None:
        self.ssl_context = ssl_context
        super().__init__(server_address, request_handler_class)

    def get_request(self) -> tuple[ssl.SSLSocket, tuple[str, int]]:
        socket_obj, address = self.socket.accept()
        socket_obj.settimeout(8)
        return (
            self.ssl_context.wrap_socket(
                socket_obj,
                server_side=True,
                do_handshake_on_connect=False,
            ),
            address,
        )

    def handle_error(self, request: object, client_address: tuple[str, int]) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, TimeoutError)):
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] {client_address[0]} odrzucone połączenie TLS: {exc}")
            return
        super().handle_error(request, client_address)


def maybe_reload_dev_server() -> None:
    """Local-only auto-reload with debounce — avoids restart storms during boot/grafiki cold start."""
    if os.environ.get("FLY_APP_NAME") or os.environ.get("IKIDS_HTTP", "").strip() == "1":
        return
    if os.environ.get("IKIDS_DEV_RELOAD", "1").strip() == "0":
        return
    # Never kill the process while init_db / Sheets warm-up is still running.
    if not _BOOT_READY.is_set():
        return

    global SOURCE_MTIME, _RELOAD_TIMER
    try:
        current_mtime = SOURCE_PATH.stat().st_mtime
    except OSError:
        return
    if current_mtime == SOURCE_MTIME:
        return

    def _do_reload(expected_mtime: float) -> None:
        global SOURCE_MTIME, _RELOAD_TIMER
        with _RELOAD_LOCK:
            _RELOAD_TIMER = None
            try:
                latest = SOURCE_PATH.stat().st_mtime
            except OSError:
                return
            if latest != expected_mtime:
                # Editor still writing — wait for the next settle.
                _schedule_reload(latest)
                return
            if latest == SOURCE_MTIME:
                return
            SOURCE_MTIME = latest
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(
                f"\n[{timestamp}] Wykryto zmiany w {SOURCE_PATH.name}, przeładowuję serwer...",
                flush=True,
            )
            os.execv(sys.executable, [sys.executable, str(SOURCE_PATH), *sys.argv[1:]])

    def _schedule_reload(mtime: float) -> None:
        global _RELOAD_TIMER
        if _RELOAD_TIMER is not None:
            _RELOAD_TIMER.cancel()
        timer = threading.Timer(_DEV_RELOAD_DEBOUNCE_SEC, _do_reload, args=(mtime,))
        timer.daemon = True
        _RELOAD_TIMER = timer
        timer.start()

    with _RELOAD_LOCK:
        _schedule_reload(current_mtime)


def local_ipv4_addresses() -> list[str]:
    addresses = {"127.0.0.1"}
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        if "probe" in locals():
            probe.close()

    return sorted(addresses)


def local_dns_names() -> list[str]:
    configured = os.environ.get("IKIDS_DOMAINS", "")
    domains = [domain.strip() for domain in configured.split(",") if domain.strip()]
    if not domains:
        domains = list(DEFAULT_LOCAL_DOMAINS)
    return sorted({"localhost", *domains})


def certificate_matches_hosts(cert_path: Path, hosts: list[str]) -> bool:
    if not cert_path.exists():
        return False
    try:
        decoded = ssl._ssl._test_decode_cert(str(cert_path))
    except (OSError, ssl.SSLError):
        return False
    alt_names = {value for kind, value in decoded.get("subjectAltName", []) if kind in {"DNS", "IP Address"}}
    return all(host in alt_names for host in hosts)


def certificate_is_issued_by_local_ca(cert_path: Path) -> bool:
    if not cert_path.exists() or not CA_CERT_PATH.exists():
        return False
    try:
        result = subprocess.run(
            ["openssl", "verify", "-CAfile", str(CA_CERT_PATH), str(cert_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
            from cryptography.hazmat.primitives.asymmetric.ec import ECDSA

            ca_cert = x509.load_pem_x509_certificate(CA_CERT_PATH.read_bytes())
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            public_key = ca_cert.public_key()
            signature_hash = cert.signature_hash_algorithm
            if isinstance(public_key, rsa.RSAPublicKey):
                public_key.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), signature_hash)
            else:
                public_key.verify(cert.signature, cert.tbs_certificate_bytes, ECDSA(signature_hash))
            return cert.issuer == ca_cert.subject
        except Exception:
            return False


def generate_local_ca_with_cryptography() -> None:
    if CA_CERT_PATH.exists() and CA_KEY_PATH.exists():
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iKids Park Local CA")])
    now = datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False,
            content_commitment=False,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=True,
            crl_sign=True,
            encipher_only=False,
            decipher_only=False,
        ), critical=True)
        .sign(key, hashes.SHA256())
    )
    CA_KEY_PATH.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    CA_CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    CA_KEY_PATH.chmod(0o600)


def generate_local_ca() -> None:
    if CA_CERT_PATH.exists() and CA_KEY_PATH.exists():
        return
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-days",
                "3650",
                "-nodes",
                "-keyout",
                str(CA_KEY_PATH),
                "-out",
                str(CA_CERT_PATH),
                "-subj",
                "/CN=iKids Park Local CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        CA_KEY_PATH.chmod(0o600)
    except OSError:
        generate_local_ca_with_cryptography()


def generate_local_certificate_with_cryptography(ipv4_addresses: list[str], dns_names: list[str]) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_key = serialization.load_pem_private_key(CA_KEY_PATH.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(CA_CERT_PATH.read_bytes())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iKids Park Local")])
    alt_names = [
        *[x509.DNSName(name) for name in dns_names],
        *[x509.IPAddress(ipaddress.ip_address(address)) for address in ipv4_addresses],
    ]
    now = datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=True,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    KEY_PATH.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_PATH.chmod(0o600)
    return CERT_PATH, KEY_PATH


def ensure_local_certificate() -> tuple[Path, Path]:
    ipv4_addresses = local_ipv4_addresses()
    dns_names = local_dns_names()
    try:
        generate_local_ca()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Nie udało się wygenerować lokalnego CA HTTPS przez openssl.") from exc

    if (
        CERT_PATH.exists()
        and KEY_PATH.exists()
        and certificate_matches_hosts(CERT_PATH, [*dns_names, *ipv4_addresses])
        and certificate_is_issued_by_local_ca(CERT_PATH)
    ):
        return CERT_PATH, KEY_PATH

    if shutil.which("openssl") is None:
        return generate_local_certificate_with_cryptography(ipv4_addresses, dns_names)

    alt_names = [*[f"DNS:{name}" for name in dns_names], *[f"IP:{address}" for address in ipv4_addresses]]
    openssl_config = f"""
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
CN = iKids Park Local

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = {", ".join(alt_names)}
"""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as config_file:
        config_file.write(openssl_config)
        config_path = config_file.name
    csr_path = tempfile.NamedTemporaryFile(delete=False).name

    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-keyout",
                str(KEY_PATH),
                "-out",
                csr_path,
                "-config",
                config_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                csr_path,
                "-CA",
                str(CA_CERT_PATH),
                "-CAkey",
                str(CA_KEY_PATH),
                "-CAcreateserial",
                "-out",
                str(CERT_PATH),
                "-days",
                "825",
                "-sha256",
                "-extfile",
                config_path,
                "-extensions",
                "v3_req",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Nie udało się wygenerować lokalnego certyfikatu HTTPS przez openssl.") from exc
    finally:
        try:
            os.unlink(config_path)
        except OSError:
            pass
        try:
            os.unlink(csr_path)
        except OSError:
            pass

    KEY_PATH.chmod(0o600)
    return CERT_PATH, KEY_PATH


def https_context() -> ssl.SSLContext:
    cert_path, key_path = ensure_local_certificate()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context


def run() -> None:
    selected_port = PORT
    # Fly/Docker: HTTP behind platform TLS. Local HTTPS only with IKIDS_HTTPS=1.
    behind_proxy = bool(os.environ.get("FLY_APP_NAME")) or os.environ.get("IKIDS_HTTP", "").strip() == "1"
    use_https = (not behind_proxy) and os.environ.get("IKIDS_HTTPS", "").strip() == "1"
    context = https_context() if use_https else None
    server_class = ThreadingHTTPSServer if use_https else ThreadingHTTPServer
    port_candidates = [PORT] if behind_proxy else list(range(PORT, PORT + 20))
    server = None
    for candidate_port in port_candidates:
        try:
            if context is not None:
                server = server_class((HOST, candidate_port), ReservationHandler, context)
            else:
                server = server_class((HOST, candidate_port), ReservationHandler)
            selected_port = candidate_port
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
    if server is None:
        raise RuntimeError(f"Nie znaleziono wolnego portu (start {PORT}).")

    protocol = "https" if use_https else "http"
    if USE_LOCAL_SQLITE:
        print(f"Tryb bazy: lokalny testowy SQLite ({LOCAL_DB_PATH})", flush=True)
        print("Dane z tej bazy nie sa przenoszone na Supabase/Fly.", flush=True)
    else:
        print("Tryb bazy: Supabase/Postgres z DATABASE_URL", flush=True)
    print(f"{APP_TITLE} nasłuchuje na {protocol}://{HOST}:{selected_port}", flush=True)

    def boot() -> None:
        global _BOOT_ERROR
        try:
            print("Inicjalizacja bazy...", flush=True)
            init_db()
            print("Baza gotowa.", flush=True)
            # Warm Sheets cache before serving — otherwise first /grafiki after local
            # reload is a multi-second cold start and boot-page polls pile up on it.
            if GOOGLE_SHEET_ID:
                print("Ładowanie grafików (Google Sheets)...", flush=True)
                schedule = load_live_schedule(force=True)
                if schedule.get("ok"):
                    print("Grafiki gotowe.", flush=True)
                else:
                    print(f"Grafiki niedostępne: {schedule.get('error') or 'nieznany błąd'}", flush=True)
            _BOOT_ERROR = None
            _BOOT_READY.set()
            app_icon_png(192, solid=False)
            app_icon_png(512, solid=False)
            app_icon_png(192, solid=True)
            app_icon_png(512, solid=True)
            print("Ikona PWA gotowa.", flush=True)
        except Exception as exc:
            _BOOT_ERROR = f"Błąd startu: {exc}"
            print(_BOOT_ERROR, flush=True)
            _BOOT_READY.set()

    # Bind first so Fly health/proxy does not sit on "connection refused" during slow DB init.
    threading.Thread(target=boot, daemon=True, name="ikids-boot").start()
    if use_https:
        print(f"Lokalne CA do zaufania na telefonie: {CA_CERT_PATH}", flush=True)
        print(f"Certyfikat serwera: {CERT_PATH}", flush=True)
        print(f"CA można pobrać z telefonu pod adresem {protocol}://<IP-komputera>:{selected_port}/ca.crt", flush=True)
    elif not behind_proxy:
        print(f"Na telefonie otworz: http://<IP-komputera>:{selected_port}", flush=True)
        print(f"Hotspot Windows zwykle uzywa: http://192.168.137.1:{selected_port}", flush=True)
    print("Zatrzymaj serwer skrótem Ctrl+C.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymano serwer.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
