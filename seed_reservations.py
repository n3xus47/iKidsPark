#!/usr/bin/env python3
"""Uzupełnia bazę przykładowymi rezerwacjami wokół 15 lipca."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import main
from main import (
    ADULT_LOCATIONS,
    ANIMATION_TYPES,
    MASCOT_TYPES,
    PARTY_ROOMS,
    WORKSHOP_TYPES,
    init_db,
    save_reservation,
    validate_reservation,
)

TARGET_COUNT = 20
BASE_DAY = date(2026, 7, 15)

PARENT_NAMES = [
    "Anna Kowalska",
    "Piotr Nowak",
    "Maria Wiśniewska",
    "Tomasz Wójcik",
    "Katarzyna Kamińska",
    "Marcin Lewandowski",
    "Agnieszka Zielińska",
    "Jakub Szymański",
    "Ewa Dąbrowska",
    "Michał Kozłowski",
    "Joanna Jankowska",
    "Krzysztof Mazur",
    "Monika Krawczyk",
    "Robert Piotrowski",
    "Natalia Grabowska",
    "Łukasz Pawłowski",
    "Karolina Michalska",
    "Damian Król",
    "Paulina Jasińska",
    "Bartosz Wieczorek",
]

CHILD_NAMES = [
    "Zosia",
    "Kuba",
    "Maja",
    "Filip",
    "Lena",
    "Antoni",
    "Hania",
    "Oskar",
    "Julia",
    "Nikodem",
    "Alicja",
    "Tymon",
    "Nadia",
    "Alan",
    "Wiktoria",
    "Bruno",
    "Laura",
    "Igor",
    "Klara",
    "Marcel",
]

PARTY_HOURS = ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00"]


def existing_day_rooms() -> set[tuple[str, str]]:
    rows = main.db_rows(
        """
        SELECT start_at, child_location
        FROM reservations
        WHERE status = 'active'
        """
    )
    occupied: set[tuple[str, str]] = set()
    for row in rows:
        day = str(row["start_at"])[:10]
        occupied.add((day, str(row["child_location"])))
    return occupied


def service_times(party_start: str, enabled: dict[str, bool]) -> dict[str, str]:
    hour, minute = map(int, party_start.split(":"))
    cursor = datetime.combine(date.today(), time(hour, minute))
    times: dict[str, str] = {}

    offset = 30
    if enabled.get("animation"):
        times["animation_at"] = (cursor + timedelta(minutes=offset)).strftime("%H:%M")
        offset += 75
    if enabled.get("cake"):
        times["cake_at"] = (cursor + timedelta(minutes=offset)).strftime("%H:%M")
        offset += 30
    if enabled.get("workshops"):
        times["culinary_workshops_at"] = (cursor + timedelta(minutes=offset)).strftime("%H:%M")
        offset += 75
    if enabled.get("pinata"):
        times["pinata_at"] = (cursor + timedelta(minutes=offset)).strftime("%H:%M")
        offset += 30
    if enabled.get("mascot"):
        times["mascot_at"] = (cursor + timedelta(minutes=offset)).strftime("%H:%M")
    return times


def build_reservation(
    reservation_day: date,
    room: str,
    adult_location: str,
    party_start: str,
    parent_name: str,
    child_name: str,
    child_age: int,
    *,
    animation: bool,
    cake: bool,
    fruit: bool,
    workshops: bool,
    pinata: bool,
    mascot: bool,
) -> dict[str, str]:
    enabled = {
        "animation": animation,
        "cake": cake,
        "workshops": workshops,
        "pinata": pinata,
        "mascot": mascot,
    }
    service_at = service_times(party_start, enabled)

    data: dict[str, str] = {
        "reservation_date": reservation_day.isoformat(),
        "party_start_time": party_start,
        "children_count": str(random.randint(8, 18)),
        "adults_count": str(random.randint(4, 12)),
        "parent_name": parent_name,
        "birthday_child_name": child_name,
        "birthday_child_age": str(child_age),
        "child_location": room,
        "adult_location": adult_location,
        "status": "active",
        "notes": random.choice(
            [
                "",
                "Prośba o dekoracje balonowe.",
                "Gość alergiczny na orzechy.",
                "Przyjecha rodzina z daleka - prosimy o wcześniejsze przygotowanie sali.",
                "Dzieci proszą o dodatkową muzykę.",
            ]
        ),
    }

    if animation:
        data["animation_enabled"] = "1"
        data["animation_type"] = random.choice(ANIMATION_TYPES)
        data["animation_at"] = service_at["animation_at"]

    if cake:
        data["cake_enabled"] = "1"
        data["cake_theme"] = random.choice(["Motory", "Jednorożce", "Superbohaterowie", "Piłka nożna", "Księżniczki"])
        data["cake_at"] = service_at["cake_at"]

    if fruit:
        data["fruit_enabled"] = "1"
        data["fruit_plates"] = str(random.randint(2, 6))

    if workshops:
        data["culinary_workshops_enabled"] = "1"
        data["culinary_workshops_type"] = random.choice(WORKSHOP_TYPES)
        data["culinary_workshops_at"] = service_at["culinary_workshops_at"]

    if pinata:
        data["pinata_enabled"] = "1"
        data["pinata_theme"] = random.choice(["Piraci", "Kosmos", "Sport", "Kraina lodu"])
        data["pinata_at"] = service_at["pinata_at"]

    if mascot:
        data["mascot_enabled"] = "1"
        data["mascot_type"] = random.choice(MASCOT_TYPES)
        data["mascot_at"] = service_at["mascot_at"]

    return data


def seed_reservations(target_count: int = TARGET_COUNT) -> int:
    init_db()
    current = main.db_one("SELECT COUNT(*) AS count FROM reservations")
    existing_count = int(current["count"]) if current else 0
    if existing_count >= target_count:
        print(f"Baza ma już {existing_count} rezerwacji (cel: {target_count}). Pomijam seed.")
        return 0

    to_create = target_count - existing_count
    occupied = existing_day_rooms()
    created = 0
    rng = random.Random(20260715)

    days = [BASE_DAY + timedelta(days=offset) for offset in range(-3, 4)]
    parent_pool = PARENT_NAMES.copy()
    child_pool = CHILD_NAMES.copy()
    rng.shuffle(parent_pool)
    rng.shuffle(child_pool)

    slots: list[tuple[date, str, str, str]] = []
    for reservation_day in days:
        available_rooms = [
            room
            for room in PARTY_ROOMS
            if (reservation_day.isoformat(), room) not in occupied
        ]
        rng.shuffle(available_rooms)
        adult_tables = ADULT_LOCATIONS.copy()
        rng.shuffle(adult_tables)
        for index, room in enumerate(available_rooms):
            party_start = PARTY_HOURS[index % len(PARTY_HOURS)]
            adult_location = adult_tables[index % len(adult_tables)]
            slots.append((reservation_day, room, adult_location, party_start))

    rng.shuffle(slots)

    for reservation_day, room, adult_location, party_start in slots:
        if created >= to_create:
            break

        parent_name = parent_pool[created % len(parent_pool)]
        child_name = child_pool[created % len(child_pool)]
        child_age = rng.randint(4, 12)

        data = build_reservation(
            reservation_day,
            room,
            adult_location,
            party_start,
            parent_name,
            child_name,
            child_age,
            animation=rng.random() < 0.65,
            cake=rng.random() < 0.7,
            fruit=rng.random() < 0.85,
            workshops=rng.random() < 0.3,
            pinata=rng.random() < 0.2,
            mascot=rng.random() < 0.15,
        )

        values, errors = validate_reservation(data)
        if errors:
            data = build_reservation(
                reservation_day,
                room,
                adult_location,
                party_start,
                parent_name,
                child_name,
                child_age,
                animation=False,
                cake=rng.random() < 0.5,
                fruit=True,
                workshops=False,
                pinata=False,
                mascot=False,
            )
            values, errors = validate_reservation(data)
        if errors:
            print(f"Pominięto {reservation_day} / {room}: {errors}")
            continue

        save_reservation(values, role="manager")
        occupied.add((reservation_day.isoformat(), room))
        created += 1
        print(f"Dodano: {reservation_day.isoformat()} {party_start} · {room} · {child_name}")

    print(f"\nUtworzono {created} rezerwacji. Łącznie w bazie: {existing_count + created}.")
    return created


if __name__ == "__main__":
    seed_reservations()
