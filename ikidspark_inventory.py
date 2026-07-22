"""Inwentura: katalog stanów, linie bankietów, lista zakupów i wydania."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from ikidspark_config import INVENTORY_CATEGORIES, INVENTORY_CATEGORY_LABELS

DbRow = dict[str, Any]
DbRowsFn = Callable[[str, tuple], list[DbRow]]
DbOneFn = Callable[[str, tuple], DbRow | None]
ExecuteFn = Callable[[str, tuple], int]
NowIsoFn = Callable[[], str]

_db_rows: DbRowsFn | None = None
_db_one: DbOneFn | None = None
_execute: ExecuteFn | None = None
_now_iso: NowIsoFn | None = None


def bind_db(*, db_rows: DbRowsFn, db_one: DbOneFn, execute: ExecuteFn, now_iso: NowIsoFn) -> None:
    global _db_rows, _db_one, _execute, _now_iso
    _db_rows = db_rows
    _db_one = db_one
    _execute = execute
    _now_iso = now_iso


def _rows(query: str, params: tuple = ()) -> list[DbRow]:
    assert _db_rows is not None
    return _db_rows(query, params)


def _one(query: str, params: tuple = ()) -> DbRow | None:
    assert _db_one is not None
    return _db_one(query, params)


def _exec(query: str, params: tuple = ()) -> int:
    assert _execute is not None
    return _execute(query, params)


def _ts() -> str:
    assert _now_iso is not None
    return _now_iso()


def category_label(category: str) -> str:
    return INVENTORY_CATEGORY_LABELS.get(category, category)


def normalize_category(value: object) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in INVENTORY_CATEGORIES else ""


def inventory_schema_sql(*, use_sqlite: bool) -> list[str]:
    item_id = "INTEGER PRIMARY KEY AUTOINCREMENT" if use_sqlite else "BIGSERIAL PRIMARY KEY"
    line_id = "INTEGER PRIMARY KEY AUTOINCREMENT" if use_sqlite else "BIGSERIAL PRIMARY KEY"
    move_id = "INTEGER PRIMARY KEY AUTOINCREMENT" if use_sqlite else "BIGSERIAL PRIMARY KEY"
    refs = "ON DELETE CASCADE" if use_sqlite else "ON DELETE CASCADE"
    item_ref = "ON DELETE SET NULL" if use_sqlite else "ON DELETE SET NULL"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id {item_id},
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            ean TEXT NOT NULL DEFAULT '',
            qty_available INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS inventory_lines (
            id {line_id},
            reservation_id BIGINT NOT NULL REFERENCES reservations(id) {refs},
            item_id BIGINT REFERENCES inventory_items(id) {item_ref},
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
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS inventory_movements (
            id {move_id},
            kind TEXT NOT NULL,
            item_id BIGINT,
            line_id BIGINT,
            qty INTEGER NOT NULL DEFAULT 0,
            changed_by_role TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_inventory_items_category
        ON inventory_items(category, name)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_inventory_lines_reservation
        ON inventory_lines(reservation_id, cancelled)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_inventory_lines_shopping
        ON inventory_lines(cancelled, purchased, qty_to_order)
        """,
    ]


def normalize_ean(value: object) -> str:
    """Keep digits from barcode / EAN / UPC (8–14 typowo)."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if 8 <= len(digits) <= 14:
        return digits
    return ""


def record_movement(
    kind: str,
    *,
    item_id: int | None = None,
    line_id: int | None = None,
    qty: int = 0,
    role: str = "",
    note: str = "",
) -> None:
    _exec(
        """
        INSERT INTO inventory_movements (kind, item_id, line_id, qty, changed_by_role, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (kind, item_id, line_id, qty, role or "", note or "", _ts()),
    )


def list_inventory_items() -> list[DbRow]:
    return _rows(
        """
        SELECT *
        FROM inventory_items
        ORDER BY category ASC, name ASC, id ASC
        """
    )


def get_inventory_item(item_id: int) -> DbRow | None:
    return _one("SELECT * FROM inventory_items WHERE id = ?", (item_id,))


def find_inventory_item(category: str, name: str) -> DbRow | None:
    return _one(
        """
        SELECT *
        FROM inventory_items
        WHERE category = ? AND lower(name) = lower(?)
        ORDER BY id ASC
        LIMIT 1
        """,
        (category, name.strip()),
    )


def find_inventory_item_by_ean(ean: str) -> DbRow | None:
    code = normalize_ean(ean)
    if not code:
        return None
    return _one(
        """
        SELECT *
        FROM inventory_items
        WHERE ean = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (code,),
    )


def adjust_item_available(item_id: int, delta: int, *, role: str, kind: str, note: str = "", line_id: int | None = None) -> bool:
    item = get_inventory_item(item_id)
    if item is None:
        return False
    current = int(item.get("qty_available") or 0)
    next_qty = current + delta
    if next_qty < 0:
        next_qty = 0
    _exec(
        "UPDATE inventory_items SET qty_available = ?, updated_at = ? WHERE id = ?",
        (next_qty, _ts(), item_id),
    )
    record_movement(kind, item_id=item_id, line_id=line_id, qty=abs(delta), role=role, note=note)
    return True


def add_or_increase_item(
    *,
    category: str,
    name: str,
    qty: int,
    description: str = "",
    ean: str = "",
    role: str = "",
) -> int | None:
    category = normalize_category(category)
    name = str(name or "").strip()
    if not category or not name or qty < 1:
        return None
    description = str(description or "").strip()
    code = normalize_ean(ean)
    timestamp = _ts()

    if code:
        by_ean = find_inventory_item_by_ean(code)
        if by_ean:
            item_id = int(by_ean["id"])
            if description and not str(by_ean.get("description") or "").strip():
                _exec(
                    "UPDATE inventory_items SET description = ?, updated_at = ? WHERE id = ?",
                    (description, timestamp, item_id),
                )
            adjust_item_available(item_id, qty, role=role, kind="manual_add", note="Doliczenie stanu (EAN)")
            return item_id

    existing = find_inventory_item(category, name)
    if existing:
        item_id = int(existing["id"])
        updates: list[str] = []
        params: list[object] = []
        if description and not str(existing.get("description") or "").strip():
            updates.append("description = ?")
            params.append(description)
        if code and not str(existing.get("ean") or "").strip():
            updates.append("ean = ?")
            params.append(code)
        if updates:
            updates.append("updated_at = ?")
            params.append(timestamp)
            params.append(item_id)
            _exec(
                f"UPDATE inventory_items SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
        adjust_item_available(item_id, qty, role=role, kind="manual_add", note="Doliczenie stanu")
        return item_id

    item_id = _exec(
        """
        INSERT INTO inventory_items (category, name, description, ean, qty_available, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (category, name, description, code, 0, timestamp, timestamp),
    )
    adjust_item_available(item_id, qty, role=role, kind="manual_add", note="Nowa pozycja katalogu")
    return item_id


def scan_ean_add(
    ean: str,
    *,
    qty: int = 1,
    role: str = "",
) -> dict[str, Any]:
    """Skan: jeśli EAN w bazie — dolicz qty; inaczej status unknown (wymaga nazwy)."""
    code = normalize_ean(ean)
    if not code:
        return {"status": "error", "message": "Nieprawidłowy kod kreskowy."}
    if qty < 1 or qty > 500:
        return {"status": "error", "message": "Ilość musi być w zakresie 1–500."}
    existing = find_inventory_item_by_ean(code)
    if existing is None:
        return {"status": "unknown", "ean": code}
    item_id = int(existing["id"])
    adjust_item_available(item_id, qty, role=role, kind="manual_add", note="Skan EAN")
    item = get_inventory_item(item_id) or existing
    return {
        "status": "increased",
        "ean": code,
        "item": {
            "id": item_id,
            "category": str(item.get("category") or ""),
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or ""),
            "ean": str(item.get("ean") or code),
            "qty_available": int(item.get("qty_available") or 0),
        },
        "qty_added": qty,
    }


def list_lines_for_reservation(reservation_id: int, *, include_cancelled: bool = False) -> list[DbRow]:
    if include_cancelled:
        return _rows(
            """
            SELECT *
            FROM inventory_lines
            WHERE reservation_id = ?
            ORDER BY id ASC
            """,
            (reservation_id,),
        )
    return _rows(
        """
        SELECT *
        FROM inventory_lines
        WHERE reservation_id = ? AND cancelled = 0
        ORDER BY id ASC
        """,
        (reservation_id,),
    )


def list_shopping_lines() -> list[DbRow]:
    return _rows(
        """
        SELECT l.*, r.start_at AS reservation_start_at, r.birthday_child_name, r.parent_name, r.status AS reservation_status
        FROM inventory_lines l
        JOIN reservations r ON r.id = l.reservation_id
        WHERE l.cancelled = 0 AND l.qty_to_order > 0 AND r.status = 'active'
        ORDER BY l.purchased ASC, r.start_at ASC, l.id ASC
        """
    )


def list_issue_lines(*, today: date) -> list[DbRow]:
    today_iso = today.isoformat()
    return _rows(
        """
        SELECT l.*, r.start_at AS reservation_start_at, r.birthday_child_name, r.parent_name, r.status AS reservation_status
        FROM inventory_lines l
        JOIN reservations r ON r.id = l.reservation_id
        WHERE l.cancelled = 0 AND r.status = 'active'
          AND substr(r.start_at, 1, 10) <= ?
        ORDER BY r.start_at ASC, l.id ASC
        """,
        (today_iso,),
    )


def list_upcoming_lines(*, today: date, days: int = 14) -> list[DbRow]:
    today_iso = today.isoformat()
    end = date.fromordinal(today.toordinal() + days).isoformat()
    return _rows(
        """
        SELECT l.*, r.start_at AS reservation_start_at, r.birthday_child_name, r.parent_name, r.status AS reservation_status
        FROM inventory_lines l
        JOIN reservations r ON r.id = l.reservation_id
        WHERE l.cancelled = 0 AND r.status = 'active'
          AND substr(r.start_at, 1, 10) >= ?
          AND substr(r.start_at, 1, 10) <= ?
        ORDER BY r.start_at ASC, l.id ASC
        """,
        (today_iso, end),
    )


def get_line(line_id: int) -> DbRow | None:
    return _one("SELECT * FROM inventory_lines WHERE id = ?", (line_id,))


def _release_line(line: DbRow, *, role: str, cancel: bool) -> None:
    line_id = int(line["id"])
    item_id = line.get("item_id")
    reserved = int(line.get("qty_reserved") or 0)
    if item_id and reserved > 0:
        adjust_item_available(
            int(item_id),
            reserved,
            role=role,
            kind="release",
            note="Zwrot rezerwacji stanu",
            line_id=line_id,
        )
    if cancel:
        _exec(
            """
            UPDATE inventory_lines
            SET qty_reserved = 0, cancelled = 1, issued = 0, updated_at = ?
            WHERE id = ?
            """,
            (_ts(), line_id),
        )
    else:
        _exec("DELETE FROM inventory_lines WHERE id = ?", (line_id,))


def release_reservation_inventory(reservation_id: int, *, role: str, keep_purchased: bool = True) -> None:
    lines = list_lines_for_reservation(reservation_id, include_cancelled=False)
    for line in lines:
        purchased = int(line.get("purchased") or 0)
        to_order = int(line.get("qty_to_order") or 0)
        if keep_purchased and purchased and to_order > 0:
            # Zwrot zarezerwowanego stanu; zakupione sztuki zostają anulowane do decyzji ręcznej.
            item_id = line.get("item_id")
            reserved = int(line.get("qty_reserved") or 0)
            line_id = int(line["id"])
            if item_id and reserved > 0:
                adjust_item_available(
                    int(item_id),
                    reserved,
                    role=role,
                    kind="release",
                    note="Zwrot przy anulowaniu (zakupione zostają)",
                    line_id=line_id,
                )
            _exec(
                """
                UPDATE inventory_lines
                SET qty_reserved = 0, cancelled = 1, issued = 0, updated_at = ?
                WHERE id = ?
                """,
                (_ts(), line_id),
            )
        else:
            _release_line(line, role=role, cancel=True)


def _create_line(
    *,
    reservation_id: int,
    category: str,
    name: str,
    description: str,
    qty: int,
    item_id: int | None,
    role: str,
) -> int:
    available = 0
    if item_id:
        item = get_inventory_item(item_id)
        if item:
            available = int(item.get("qty_available") or 0)
    reserved = min(qty, available)
    to_order = qty - reserved
    timestamp = _ts()
    if item_id and reserved > 0:
        adjust_item_available(
            item_id,
            -reserved,
            role=role,
            kind="reserve",
            note="Rezerwacja na bankiet",
        )
    line_id = _exec(
        """
        INSERT INTO inventory_lines (
            reservation_id, item_id, category, name, description, qty,
            qty_reserved, qty_to_order, purchased, issued, cancelled, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
        """,
        (
            reservation_id,
            item_id,
            category,
            name,
            description,
            qty,
            reserved,
            to_order,
            timestamp,
            timestamp,
        ),
    )
    return line_id


def parse_inventory_lines_payload(data: dict[str, object]) -> list[dict[str, object]]:
    """Parse inventory_* repeated fields or inventory_lines_json."""
    raw_json = str(data.get("inventory_lines_json") or "").strip()
    if raw_json:
        try:
            import json

            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                lines: list[dict[str, object]] = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    lines.append(
                        {
                            "category": str(item.get("category") or ""),
                            "item_id": str(item.get("item_id") or ""),
                            "name": str(item.get("name") or ""),
                            "qty": str(item.get("qty") or ""),
                            "description": str(item.get("description") or ""),
                        }
                    )
                return lines
        except Exception:
            pass

    def as_list(key: str) -> list[str]:
        value = data.get(key, "")
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    categories = as_list("inventory_category")
    item_ids = as_list("inventory_item_id")
    names = as_list("inventory_name")
    qtys = as_list("inventory_qty")
    descriptions = as_list("inventory_description")
    length = max(len(categories), len(item_ids), len(names), len(qtys), len(descriptions), 0)
    lines = []
    for index in range(length):
        category = categories[index] if index < len(categories) else ""
        item_id = item_ids[index] if index < len(item_ids) else ""
        name = names[index] if index < len(names) else ""
        qty = qtys[index] if index < len(qtys) else ""
        description = descriptions[index] if index < len(descriptions) else ""
        if not str(category).strip() and not str(name).strip() and not str(qty).strip():
            continue
        lines.append(
            {
                "category": category,
                "item_id": item_id,
                "name": name,
                "qty": qty,
                "description": description,
            }
        )
    return lines


def validate_inventory_form_lines(
    raw_lines: list[dict[str, object]],
    *,
    is_table: bool,
) -> tuple[list[dict[str, object]], dict[str, str]]:
    errors: dict[str, str] = {}
    if is_table:
        return [], errors
    cleaned: list[dict[str, object]] = []
    for raw in raw_lines:
        category = normalize_category(raw.get("category"))
        name = str(raw.get("name") or "").strip()
        description = str(raw.get("description") or "").strip()[:300]
        item_id_raw = str(raw.get("item_id") or "").strip()
        item_id: int | None = int(item_id_raw) if item_id_raw.isdigit() else None
        qty_raw = str(raw.get("qty") or "").strip()
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            qty = 0
        if not category and not name and not qty_raw:
            continue
        if not category:
            errors["inventory_lines"] = "Wybierz kategorię pozycji inwentury."
            continue
        if item_id:
            item = get_inventory_item(item_id)
            if item is None or str(item.get("category")) != category:
                errors["inventory_lines"] = "Wybrana pozycja katalogu nie istnieje."
                continue
            name = str(item.get("name") or name).strip()
        if not name:
            errors["inventory_lines"] = "Podaj nazwę pozycji inwentury."
            continue
        if qty < 1 or qty > 500:
            errors["inventory_lines"] = "Ilość pozycji inwentury musi być w zakresie 1–500."
            continue
        if len(name) > 120:
            errors["inventory_lines"] = "Nazwa pozycji inwentury jest za długa."
            continue
        cleaned.append(
            {
                "category": category,
                "item_id": item_id,
                "name": name,
                "description": description,
                "qty": qty,
            }
        )
    return cleaned, errors


def sync_reservation_inventory(
    reservation_id: int,
    desired_lines: list[dict[str, object]],
    *,
    role: str,
    status: str,
    party_day: date | None = None,
    today: date | None = None,
) -> None:
    if status == "cancelled":
        release_reservation_inventory(reservation_id, role=role, keep_purchased=True)
        return

    # Przy edycji zawsze resetuj aktywne linie i załóż na nowo (prosty, spójny model).
    existing = list_lines_for_reservation(reservation_id, include_cancelled=False)
    for line in existing:
        _release_line(line, role=role, cancel=False)

    for raw in desired_lines:
        category = normalize_category(raw.get("category"))
        name = str(raw.get("name") or "").strip()
        description = str(raw.get("description") or "").strip()
        qty = int(raw.get("qty") or 0)
        item_id = raw.get("item_id")
        item_id_int = int(item_id) if isinstance(item_id, int) or (isinstance(item_id, str) and str(item_id).isdigit()) else None
        if item_id_int is None:
            # Nowa pozycja „tylko zamów” — opcjonalnie utwórz katalog z qty_available=0.
            existing_item = find_inventory_item(category, name)
            if existing_item:
                item_id_int = int(existing_item["id"])
            else:
                timestamp = _ts()
                item_id_int = _exec(
                    """
                    INSERT INTO inventory_items (category, name, description, ean, qty_available, created_at, updated_at)
                    VALUES (?, ?, ?, '', 0, ?, ?)
                    """,
                    (category, name, description, timestamp, timestamp),
                )
                record_movement(
                    "manual_add",
                    item_id=item_id_int,
                    qty=0,
                    role=role,
                    note="Pozycja utworzona z bankietu (do zamówienia)",
                )
        _create_line(
            reservation_id=reservation_id,
            category=category,
            name=name,
            description=description,
            qty=qty,
            item_id=item_id_int,
            role=role,
        )

    if party_day is not None and today is not None and party_day <= today and status == "active":
        auto_issue_for_reservation(reservation_id, role=role, today=today)


def set_line_purchased(line_id: int, purchased: bool, *, role: str) -> bool:
    line = get_line(line_id)
    if line is None or int(line.get("cancelled") or 0):
        return False
    if int(line.get("qty_to_order") or 0) <= 0:
        return False
    flag = 1 if purchased else 0
    _exec(
        "UPDATE inventory_lines SET purchased = ?, updated_at = ? WHERE id = ?",
        (flag, _ts(), line_id),
    )
    record_movement(
        "purchase" if purchased else "unpurchase",
        item_id=int(line["item_id"]) if line.get("item_id") else None,
        line_id=line_id,
        qty=int(line.get("qty_to_order") or 0),
        role=role,
    )
    return True


def set_line_issued(line_id: int, issued: bool, *, role: str) -> bool:
    line = get_line(line_id)
    if line is None or int(line.get("cancelled") or 0):
        return False
    flag = 1 if issued else 0
    _exec(
        "UPDATE inventory_lines SET issued = ?, updated_at = ? WHERE id = ?",
        (flag, _ts(), line_id),
    )
    record_movement(
        "issue" if issued else "unissue",
        item_id=int(line["item_id"]) if line.get("item_id") else None,
        line_id=line_id,
        qty=int(line.get("qty") or 0),
        role=role,
    )
    return True


def auto_issue_due_lines(*, today: date, role: str = "system") -> int:
    today_iso = today.isoformat()
    due = _rows(
        """
        SELECT l.id
        FROM inventory_lines l
        JOIN reservations r ON r.id = l.reservation_id
        WHERE l.cancelled = 0 AND l.issued = 0 AND r.status = 'active'
          AND substr(r.start_at, 1, 10) <= ?
        """,
        (today_iso,),
    )
    count = 0
    for row in due:
        if set_line_issued(int(row["id"]), True, role=role):
            count += 1
    return count


def auto_issue_for_reservation(reservation_id: int, *, role: str, today: date) -> None:
    reservation = _one("SELECT start_at, status FROM reservations WHERE id = ?", (reservation_id,))
    if reservation is None:
        return
    if str(reservation.get("status")) != "active":
        return
    start_at = str(reservation.get("start_at") or "")
    if len(start_at) < 10:
        return
    try:
        party_day = date.fromisoformat(start_at[:10])
    except ValueError:
        return
    if party_day > today:
        return
    for line in list_lines_for_reservation(reservation_id):
        if not int(line.get("issued") or 0):
            set_line_issued(int(line["id"]), True, role=role)


def form_lines_from_reservation(reservation_id: int) -> list[dict[str, object]]:
    return [
        {
            "category": str(line.get("category") or ""),
            "item_id": str(line.get("item_id") or ""),
            "name": str(line.get("name") or ""),
            "qty": str(line.get("qty") or ""),
            "description": str(line.get("description") or ""),
        }
        for line in list_lines_for_reservation(reservation_id)
    ]
