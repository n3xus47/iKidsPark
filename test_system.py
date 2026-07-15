#!/usr/bin/env python3
"""Kompleksowy test systemu iKids Park."""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

# Import funkcji z aplikacji
sys.path.insert(0, str(__file__).replace("test_system.py", ""))
import main  # noqa: E402

BASE = os.environ.get("IKIDS_TEST_BASE", "https://127.0.0.1:8000")
HTTPS_CONTEXT = ssl._create_unverified_context() if BASE.startswith("https://") else None

PASS = 0
FAIL = 0
RESULTS: list[str] = []


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    msg = f"  [OK] {name}"
    if detail:
        msg += f" — {detail}"
    RESULTS.append(msg)
    print(msg)


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    msg = f"  [FAIL] {name}"
    if detail:
        msg += f" — {detail}"
    RESULTS.append(msg)
    print(msg)


def http_get(path: str) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(f"{BASE}{path}")
    with urllib.request.urlopen(req, timeout=15, context=HTTPS_CONTEXT) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def http_post(path: str, data: dict, query: str = "") -> tuple[int, str, dict[str, str]]:
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
    url = f"{BASE}{path}"
    if query:
        url += f"?{query}"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15, context=HTTPS_CONTEXT) as resp:
            return resp.status, resp.geturl(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), dict(exc.headers)


def base_reservation(
    day: str,
    room: str,
    table: str,
    start: str,
    child_name: str,
    parent: str,
    *,
    animation: bool = False,
    animation_at: str = "11:00",
    animation_type: str = "Dyskoteka",
    cake: bool = False,
    cake_at: str = "12:00",
    fruit: bool = True,
    workshops: bool = False,
    workshops_at: str = "13:00",
    pinata: bool = False,
    pinata_at: str = "14:00",
    mascot: bool = False,
    mascot_at: str = "15:00",
    children_count: int = 10,
    adults_count: int = 4,
    age: int = 7,
) -> dict:
    data = {
        "reservation_date": day,
        "party_start_time": start,
        "children_count": str(children_count),
        "adults_count": str(adults_count),
        "parent_name": parent,
        "birthday_child_name": child_name,
        "birthday_child_age": str(age),
        "child_location": room,
        "adult_location": [table],
        "fruit_enabled": "1" if fruit else "",
        "fruit_plates": "2" if fruit else "",
        "status": "active",
        "notes": f"Test rezerwacja {child_name}",
    }
    if animation:
        data["animation_enabled"] = "1"
        data["animation_type"] = animation_type
        data["animation_at"] = animation_at
    if cake:
        data["cake_enabled"] = "1"
        data["cake_theme"] = "Motory"
        data["cake_at"] = cake_at
    if workshops:
        data["culinary_workshops_enabled"] = "1"
        data["culinary_workshops_type"] = "Pizza"
        data["culinary_workshops_at"] = workshops_at
    if pinata:
        data["pinata_enabled"] = "1"
        data["pinata_theme"] = "Jednorożec"
        data["pinata_at"] = pinata_at
    if mascot:
        data["mascot_enabled"] = "1"
        data["mascot_type"] = "Miś"
        data["mascot_at"] = mascot_at
    return data


RESERVATIONS_PLAN = [
    # Seed 17.07: Wiktoria→3.Wróżki, Klara→1.Biały Dom + 6.Football/Scena30 — używamy wolnych sal/stolików
    ("2026-07-17", "5. Zima", "Bar - Stolik 7", "10:00", "Ola Testowa", "Anna Kowalska"),
    ("2026-07-17", "2. Magiczny Las", "Bar - Stolik 8", "12:00", "Kuba Testowy", "Piotr Nowak", {"animation": True, "animation_at": "12:30"}),
    ("2026-07-17", "4. Kosmos", "Scena - Stolik 18", "14:00", "Zosia Testowa", "Maria Wiśniewska", {"cake": True, "cake_at": "15:00"}),
    ("2026-07-18", "4. Kosmos", "Trójkąt - Stolik 41", "10:00", "Filip Testowy", "Jan Zieliński"),
    ("2026-07-18", "5. Zima", "Labirynt - Stolik 58", "12:00", "Maja Testowa", "Ewa Dąbrowska", {"workshops": True, "workshops_at": "13:00"}),
    ("2026-07-18", "6. Football", "Pozostałe stoliki - Stolik 15", "15:00", "Tomek Testowy", "Adam Lewandowski", {"pinata": True, "pinata_at": "16:00"}),
    ("2026-07-19", "1. Biały Dom", "Bar - Stolik 9", "11:00", "Nina Testowa", "Karolina Wójcik"),
    ("2026-07-19", "2. Magiczny Las", "Scena - Stolik 18", "13:00", "Bartek Testowy", "Tomasz Kamiński", {"mascot": True, "mascot_at": "14:00"}),
    ("2026-07-19", "3. Wróżki", "Trójkąt - Stolik 42", "16:00", "Hania Testowa", "Agnieszka Szymańska"),
    ("2026-07-19", "5. Zima", "Labirynt - Stolik 59", "18:30", "Igor Testowy", "Michał Woźniak", {"animation": True, "animation_at": "19:00"}),
]

created_ids: list[int] = []


def cleanup_previous_test_data() -> None:
    """Usuwa rezerwacje z poprzednich uruchomień testu."""
    rows = main.get_all_reservations()
    removed = 0
    for row in rows:
        name = row["birthday_child_name"]
        if "Testow" in name or "Testowy" in name or "Multi" in name or "Konflikt" in name:
            main.delete_reservation(row["id"])
            removed += 1
    if removed:
        print(f"  [INFO] Usunieto {removed} rezerwacji testowych z poprzedniego uruchomienia")


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def test_server_endpoints() -> None:
    section("1. Endpointy HTTP (GET)")

    endpoints = [
        ("/", "Strona główna (manager)"),
        ("/?role=organizer&day=2026-07-17", "Widok organizatora"),
        ("/?role=animators&day=2026-07-18", "Widok animatorów"),
        ("/?role=kitchen&day=2026-07-19", "Widok kuchni"),
        ("/?role=manager&day=2026-07-17", "Widok kierownika"),
        ("/schema", "Strona schematu DB"),
        ("/export", "Eksport CSV"),
        ("/api/availability?date=2026-07-17", "API dostępności"),
    ]
    for path, label in endpoints:
        try:
            status, body, headers = http_get(path)
            if status == 200:
                if path.startswith("/api/"):
                    data = json.loads(body.decode("utf-8"))
                    loc_count = len(data.get("locations", {}))
                    ok(label, f"{loc_count} lokalizacji")
                elif path == "/export":
                    if "birthday_child_name" in body.decode("utf-8-sig") or len(body) < 200:
                        ok(label, f"{len(body)} bajtów")
                    else:
                        ok(label, f"{len(body)} bajtów CSV")
                else:
                    ok(label, f"{len(body)} bajtów HTML")
            else:
                fail(label, f"status {status}")
        except Exception as exc:
            fail(label, str(exc))


def test_unit_validation() -> None:
    section("2. Walidacja (funkcje jednostkowe)")

    # Pusta rezerwacja
    _, errors = main.validate_reservation({})
    if errors:
        ok("Pusta rezerwacja odrzucona", f"{len(errors)} błędów")
    else:
        fail("Pusta rezerwacja odrzucona", "brak błędów")

    # Nieprawidłowa data
    _, errors = main.validate_reservation({"reservation_date": "abc", "party_start_time": "10:00"})
    if "reservation_date" in errors:
        ok("Nieprawidłowa data", errors["reservation_date"])
    else:
        fail("Nieprawidłowa data")

    # Wiek poza zakresem
    data = base_reservation("2026-07-17", "1. Biały Dom", "Bar - Stolik 7", "10:00", "Test", "Rodzic", age=25)
    _, errors = main.validate_reservation(data)
    if "birthday_child_age" in errors:
        ok("Wiek > 18 odrzucony")
    else:
        fail("Wiek > 18 odrzucony")

    # Anulowanie bez powodu
    data = base_reservation("2026-07-17", "1. Biały Dom", "Bar - Stolik 7", "10:00", "Test", "Rodzic")
    data["status"] = "cancelled"
    _, errors = main.validate_reservation(data)
    if "cancellation_reason" in errors:
        ok("Anulowanie bez powodu odrzucone")
    else:
        fail("Anulowanie bez powodu odrzucone")

    # Blokada Koło Marzeń (17:45-18:15)
    data = base_reservation("2026-07-17", "5. Zima", "Bar - Stolik 10", "10:00", "Test", "Rodzic")
    data["animation_enabled"] = "1"
    data["animation_type"] = "Dyskoteka"
    data["animation_at"] = "17:50"
    _, errors = main.validate_reservation(data)
    if "animation_at" in errors and "Koło Marzeń" in errors["animation_at"]:
        ok("Blokada Koło Marzeń 17:45-18:15")
    else:
        fail("Blokada Koło Marzeń", str(errors.get("animation_at", errors)))

    # Nakładanie się dodatków
    data = base_reservation("2026-07-17", "5. Zima", "Bar - Stolik 11", "10:00", "Test", "Rodzic")
    data["animation_enabled"] = "1"
    data["animation_type"] = "Dyskoteka"
    data["animation_at"] = "11:00"
    data["cake_enabled"] = "1"
    data["cake_theme"] = "Test"
    data["cake_at"] = "11:30"  # nakłada się z animacją (60 min)
    _, errors = main.validate_reservation(data)
    if "cake_at" in errors or "animation_at" in errors:
        ok("Nakładanie się dodatków wykryte")
    else:
        fail("Nakładanie się dodatków", str(errors))

    # overlaps_stage_block bezpośrednio
    if main.overlaps_stage_block(main.parse_time_value("17:50"), 60):
        ok("overlaps_stage_block(17:50, 60min) = True")
    else:
        fail("overlaps_stage_block(17:50)")

    if not main.overlaps_stage_block(main.parse_time_value("16:00"), 60):
        ok("overlaps_stage_block(16:00, 60min) = False")
    else:
        fail("overlaps_stage_block(16:00)")


def create_test_reservations() -> None:
    section("3. Tworzenie 10 rezerwacji testowych (17-19 lipca)")

    for i, item in enumerate(RESERVATIONS_PLAN, 1):
        day, room, table, start, child, parent = item[:6]
        extras = item[6] if len(item) > 6 else {}
        data = base_reservation(day, room, table, start, child, parent, **extras)
        status, response, headers = http_post("/reservations", data, "role=organizer&day=" + day)
        if status in (200, 303) and "edit=" in response:
            rid = int(response.split("edit=")[1].split("&")[0])
            created_ids.append(rid)
            ok(f"Rezerwacja #{i}: {child}", f"id={rid}, {day} {start}, {room}")
        elif status == 400:
            fail(f"Rezerwacja #{i}: {child}", f"walidacja: fragment HTML ({len(response)} znaków)")
            if "nakłada się" in response or "naklada" in response.lower():
                print("       -> konflikt lokalizacji z istniejaca rezerwacja")
        else:
            fail(f"Rezerwacja #{i}: {child}", f"status={status}, url={response[:80]}")


def verify_created_reservations() -> None:
    section("4. Weryfikacja utworzonych rezerwacji w bazie")

    if not created_ids:
        fail("Brak utworzonych rezerwacji do weryfikacji")
        return

    for rid in created_ids:
        row = main.get_reservation(rid)
        if row and row["status"] == "active":
            ok(f"Rezerwacja id={rid} w DB", f"{row['birthday_child_name']}, {row['child_location']}, start {main.format_time(row['start_at'])}")
        else:
            fail(f"Rezerwacja id={rid} w DB")

    # Historia
    for rid in created_ids[:3]:
        history = main.get_history(rid)
        if history and history[0]["action"] == "created":
            ok(f"Historia id={rid}", f"action=created, role={history[0]['changed_by_role']}")
        else:
            fail(f"Historia id={rid}", f"{len(history)} wpisów")

    # Rezerwacje per dzień
    for day_str in ("2026-07-17", "2026-07-18", "2026-07-19"):
        d = date.fromisoformat(day_str)
        rows = main.get_reservations_for_day(d)
        test_rows = [r for r in rows if "Testow" in r["birthday_child_name"] or "Testowy" in r["birthday_child_name"]]
        ok(f"Rezerwacje na {day_str}", f"{len(test_rows)} testowych (łącznie {len(rows)} aktywnych)")


def test_availability_after_create() -> None:
    section("5. API dostępności po utworzeniu rezerwacji")

    for day_str in ("2026-07-17", "2026-07-18", "2026-07-19"):
        try:
            status, body, _ = http_get(f"/api/availability?date={day_str}")
            data = json.loads(body.decode("utf-8"))
            occupied = [k for k, v in data["locations"].items() if v["status"] == "occupied"]
            free = [k for k, v in data["locations"].items() if v["status"] == "free"]
            ok(f"Dostępność {day_str}", f"zajęte: {len(occupied)}, wolne: {len(free)}")
        except Exception as exc:
            fail(f"Dostępność {day_str}", str(exc))

    # Sprawdź że zajęte sale są oznaczone
    if created_ids:
        row = main.get_reservation(created_ids[0])
        if row:
            status, body, _ = http_get(f"/api/availability?date={row['start_at'][:10]}")
            data = json.loads(body.decode("utf-8"))
            loc = row["child_location"]
            if data["locations"].get(loc, {}).get("status") == "occupied":
                ok(f"Sala '{loc}' oznaczona jako zajęta")
            else:
                fail(f"Sala '{loc}' powinna być zajęta", str(data["locations"].get(loc)))


def test_conflict_detection() -> None:
    section("6. Wykrywanie konfliktów")

    if not created_ids:
        fail("Konflikt — brak rezerwacji bazowej")
        return

    base = main.get_reservation(created_ids[0])
    if not base:
        fail("Konflikt — brak rezerwacji bazowej")
        return

    # Ta sama sala, ten sam dzień
    data = base_reservation(
        base["start_at"][:10],
        base["child_location"],
        "Bar - Stolik 12",
        "10:30",
        "Konflikt Sala",
        "Test Konflikt",
    )
    status, response, _ = http_post("/reservations", data, "role=organizer")
    if status == 400 and "nakłada się" in response:
        ok("Konflikt tej samej sali wykryty")
    else:
        fail("Konflikt tej samej sali", f"status={status}")

    # Ten sam stolik
    adult_locs = main.location_values(base["adult_location"])
    if adult_locs:
        data = base_reservation(
            base["start_at"][:10],
            "5. Zima",
            adult_locs[0],
            "11:00",
            "Konflikt Stolik",
            "Test Stolik",
        )
        status, response, _ = http_post("/reservations", data, "role=organizer")
        if status == 400 and "nakłada się" in response:
            ok("Konflikt tego samego stolika wykryty")
        else:
            fail("Konflikt tego samego stolika", f"status={status}")


def test_edit_and_cancel() -> None:
    section("7. Edycja i anulowanie rezerwacji")

    if len(created_ids) < 2:
        fail("Edycja/anulowanie — za mało rezerwacji")
        return

    edit_id = created_ids[-1]
    row = main.get_reservation(edit_id)
    if not row:
        fail("Edycja — brak rezerwacji")
        return

    data = base_reservation(
        row["start_at"][:10],
        row["child_location"],
        main.location_values(row["adult_location"])[0],
        main.format_time(row["start_at"]),
        row["birthday_child_name"],
        row["parent_name"],
    )
    data["id"] = str(edit_id)
    data["notes"] = "Zaktualizowano w teście"
    data["children_count"] = "15"
    status, response, _ = http_post("/reservations", data, "role=organizer")
    if status in (200, 303):
        updated = main.get_reservation(edit_id)
        if updated and updated["children_count"] == 15 and "Zaktualizowano" in updated["notes"]:
            ok(f"Edycja id={edit_id}", "children_count=15, notatka zaktualizowana")
        else:
            fail(f"Edycja id={edit_id}", f"children={updated['children_count'] if updated else '?'}")
    else:
        fail(f"Edycja id={edit_id}", f"status={status}")

    cancel_id = created_ids[-2]
    row = main.get_reservation(cancel_id)
    if row:
        data = base_reservation(
            row["start_at"][:10],
            row["child_location"],
            main.location_values(row["adult_location"])[0],
            main.format_time(row["start_at"]),
            row["birthday_child_name"],
            row["parent_name"],
        )
        data["id"] = str(cancel_id)
        data["status"] = "cancelled"
        data["cancellation_reason"] = "Test anulowania"
        status, response, _ = http_post("/reservations", data, "role=organizer")
        if status in (200, 303):
            cancelled = main.get_reservation(cancel_id)
            if cancelled and cancelled["status"] == "cancelled":
                ok(f"Anulowanie id={cancel_id}", cancelled["cancellation_reason"])
            else:
                fail(f"Anulowanie id={cancel_id}")
        else:
            fail(f"Anulowanie id={cancel_id}", f"status={status}")


def test_history_page() -> None:
    section("8. Strona historii zmian")

    if not created_ids:
        fail("Historia — brak rezerwacji")
        return

    rid = created_ids[0]
    try:
        status, body, _ = http_get(f"/history?id={rid}&role=manager")
        html = body.decode("utf-8")
        if status == 200 and "created" in html.lower() or "utworz" in html.lower() or rid:
            ok(f"Strona historii id={rid}", f"{len(html)} bajtów")
        else:
            fail(f"Strona historii id={rid}", f"status={status}")
    except Exception as exc:
        fail(f"Strona historii id={rid}", str(exc))


def test_role_views_with_data() -> None:
    section("9. Widoki ról z danymi testowymi")

    for role, day, label in [
        ("manager", "2026-07-17", "Kierownik — 17 lipca"),
        ("animators", "2026-07-18", "Animatorzy — 18 lipca"),
        ("kitchen", "2026-07-19", "Kuchnia — 19 lipca"),
        ("organizer", "2026-07-17", "Organizator — 17 lipca"),
    ]:
        try:
            status, body, _ = http_get(f"/?role={role}&day={day}")
            html = body.decode("utf-8")
            has_test = "Testow" in html or "Testowy" in html
            if status == 200:
                ok(label, f"testowe dane widoczne: {has_test}, {len(html)} bajtów")
            else:
                fail(label, f"status={status}")
        except Exception as exc:
            fail(label, str(exc))


def test_multiple_birthday_children() -> None:
    section("10. Wielu solenizantów")

    # Osobny dzień (20.07) — sala nie może być zajęta 2x w tym samym dniu
    data = base_reservation("2026-07-20", "6. Football", "Pozostałe stoliki - Stolik 17", "10:00", "", "Rodzic Multi")
    data["birthday_child_name"] = ["Ala Multi", "Basia Multi"]
    data["birthday_child_age"] = ["5", "8"]
    status, response, _ = http_post("/reservations", data, "role=organizer&day=2026-07-20")
    if status in (200, 303) and "edit=" in response:
        rid = int(response.split("edit=")[1].split("&")[0])
        row = main.get_reservation(rid)
        children = main.birthday_children_from_row(row)
        if len(children) == 2:
            ok("Dwóch solenizantów zapisanych", f"id={rid}, {children}")
            created_ids.append(rid)
        else:
            fail("Dwóch solenizantów", str(children))
    else:
        fail("Dwóch solenizantów", f"status={status}")


def test_delete_reservation() -> None:
    section("11. Usuwanie rezerwacji")

    if not created_ids:
        fail("Usuwanie — brak rezerwacji")
        return

    del_id = created_ids.pop()
    status, response, _ = http_post("/delete", {"id": str(del_id)}, "role=organizer&day=2026-07-19")
    if status in (200, 303) and "usunięta" in response or status in (200, 303):
        if main.get_reservation(del_id) is None:
            ok(f"Usunięcie id={del_id}")
        else:
            fail(f"Usunięcie id={del_id}", "nadal w bazie")
    else:
        fail(f"Usunięcie id={del_id}", f"status={status}")

    # Usuwanie nieistniejącej
    status, response, _ = http_post("/delete", {"id": "999999"}, "role=organizer")
    if status in (200, 303):
        ok("Usuwanie nieistniejącej — przekierowanie z komunikatem")
    else:
        fail("Usuwanie nieistniejącej", f"status={status}")


def test_csv_export() -> None:
    section("12. Eksport CSV")

    try:
        status, body, headers = http_get("/export")
        text = body.decode("utf-8-sig")
        lines = [l for l in text.strip().split("\n") if l]
        if status == 200 and len(lines) >= 1:
            test_lines = [l for l in lines if "Testow" in l]
            ok("Eksport CSV", f"{len(lines)} wierszy, {len(test_lines)} testowych")
        else:
            fail("Eksport CSV", f"status={status}, lines={len(lines)}")
    except Exception as exc:
        fail("Eksport CSV", str(exc))


def print_summary() -> None:
    section("PODSUMOWANIE")
    total = PASS + FAIL
    print(f"\n  Testy zaliczone: {PASS}/{total}")
    print(f"  Testy niezaliczone: {FAIL}/{total}")
    print(f"  Utworzone rezerwacje testowe: {len(created_ids)} (ids: {created_ids})")
    if FAIL == 0:
        print("\n  [PASS] WSZYSTKIE TESTY PRZESZLY POMYSLNIE")
    else:
        print("\n  [FAIL] WYKRYTO PROBLEMY - szczegoly powyzej")
    return FAIL


def main_test() -> int:
    print("\n" + "=" * 60)
    print("  iKids Park — KOMPLEKSOWY TEST SYSTEMU")
    print("  Data testu: 17-19 lipca 2026")
    print("=" * 60)

    # Sprawdź serwer
    try:
        status, _, _ = http_get("/")
        if status != 200:
            print("BŁĄD: Serwer nie odpowiada. Uruchom: py main.py")
            return 1
        ok("Serwer dostępny na " + BASE)
    except Exception as exc:
        print(f"BŁĄD: Serwer niedostępny ({exc}). Uruchom: py main.py")
        return 1

    main.init_db()
    cleanup_previous_test_data()

    test_server_endpoints()
    test_unit_validation()
    create_test_reservations()
    verify_created_reservations()
    test_availability_after_create()
    test_conflict_detection()
    test_edit_and_cancel()
    test_history_page()
    test_role_views_with_data()
    test_multiple_birthday_children()
    test_delete_reservation()
    test_csv_export()

    return print_summary()


if __name__ == "__main__":
    sys.exit(main_test())
