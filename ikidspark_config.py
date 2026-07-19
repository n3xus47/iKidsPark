from __future__ import annotations

from datetime import time

RESERVATION_COLORS = [
    "#e63946",
    "#f77f00",
    "#2a9d8f",
    "#457b9d",
    "#6a4c93",
    "#c9184a",
    "#3a86ff",
    "#2b9348",
    "#fb5607",
    "#0077b6",
]



PARTY_ROOMS = [
    "1. Biały Dom",
    "2. Magiczny Las",
    "3. Wróżki",
    "4. Kosmos",
    "5. Zima",
    "6. Football",
]

ROOM_CAPACITY = {
    "6. Football": 24,
}

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


def format_table_range(numbers: list[int]) -> str:
    if not numbers:
        return ""
    sorted_numbers = sorted(numbers)
    ranges: list[str] = []
    range_start = sorted_numbers[0]
    range_end = sorted_numbers[0]
    for number in sorted_numbers[1:]:
        if number == range_end + 1:
            range_end = number
            continue
        ranges.append(f"{range_start}–{range_end}" if range_start != range_end else str(range_start))
        range_start = range_end = number
    ranges.append(f"{range_start}–{range_end}" if range_start != range_end else str(range_start))
    return ", ".join(ranges)

ALL_LOCATIONS = PARTY_ROOMS + ADULT_LOCATIONS
LOCATION_SEPARATOR = " | "
EMPTY_LOCATION = "Brak"

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
CAKE_CANDLE_TYPES = ["cyfra", "zwykla", "wlasna"]
CAKE_CANDLE_LABELS = {"cyfra": "cyfra", "zwykla": "zwyk\u0142a", "wlasna": "w\u0142asna"}
MASCOT_TYPES = ["Lew", "Pan Królik", "Pani Królik", "Miś"]

# Interactive hotspots for the floor plan (asset canvas 1440 x 810; display crops empty margins).
# Tuple: (number, center_x, center_y, width, height). Numbers 1-6 are party rooms.
PLAN_HOTSPOTS = [
    (1, 370.0, 533.8, 64.7, 63.3),
    (2, 450.6, 533.8, 63.3, 63.3),
    (3, 539.0, 517.5, 63.3, 64.7),
    (4, 634.8, 517.5, 63.3, 64.7),
    (5, 730.6, 517.5, 63.3, 64.7),
    (6, 818.9, 506.4, 63.3, 64.7),
    (7, 254.7, 420.6, 28.0, 36.0),
    (8, 207.6, 420.6, 28.0, 36.0),
    (9, 124.6, 439.1, 36.0, 28.0),
    (10, 59.2, 445.5, 24.0, 24.0),
    (11, 59.2, 414.3, 24.0, 24.0),
    (12, 59.2, 383.1, 24.0, 24.0),
    (13, 59.2, 345.1, 36.0, 28.0),
    (14, 124.6, 345.1, 36.0, 28.0),
    (15, 124.6, 402.0, 36.0, 28.0),
    (18, 593.2, 389.1, 34.0, 34.0),
    (19, 667.4, 389.1, 34.0, 34.0),
    (20, 741.7, 389.1, 34.0, 34.0),
    (21, 704.8, 348.2, 24.0, 24.0),
    (22, 627.8, 348.2, 24.0, 24.0),
    (23, 584.3, 307.6, 28.0, 36.0),
    (24, 639.8, 307.6, 28.0, 36.0),
    (25, 695.2, 307.6, 28.0, 36.0),
    (26, 750.6, 307.6, 28.0, 36.0),
    (30, 593.2, 226.3, 34.0, 34.0),
    (31, 667.4, 226.3, 34.0, 34.0),
    (32, 741.7, 226.3, 34.0, 34.0),
    (33, 824.2, 335.6, 36.0, 28.0),
    (34, 877.1, 335.6, 36.0, 28.0),
    (35, 930.1, 335.6, 36.0, 28.0),
    (36, 908.6, 373.3, 34.0, 34.0),
    (37, 845.6, 373.3, 34.0, 34.0),
    (38, 808.2, 420.6, 24.0, 24.0),
    (39, 864.3, 420.6, 24.0, 24.0),
    (40, 920.6, 420.6, 24.0, 24.0),
    (41, 1106.9, 408.3, 28.0, 36.0),
    (42, 1051.4, 408.3, 28.0, 36.0),
    (43, 995.9, 408.3, 28.0, 36.0),
    (44, 1117.0, 364.1, 24.0, 24.0),
    (45, 1060.8, 364.1, 24.0, 24.0),
    (46, 1004.6, 364.1, 24.0, 24.0),
    (47, 1060.8, 303.4, 36.0, 36.0),
    (48, 1017.5, 257.6, 36.0, 36.0),
    (49, 1002.9, 150.9, 26.0, 26.0),
    (50, 1043.4, 189.7, 26.0, 26.0),
    (51, 1084.0, 228.7, 26.0, 26.0),
    (52, 1121.9, 259.9, 36.0, 36.0),
    (53, 1144.7, 281.6, 36.0, 36.0),
    (54, 1178.1, 314.1, 36.0, 36.0),
    (55, 1200.9, 335.8, 36.0, 36.0),
    (56, 1217.1, 424.1, 28.0, 36.0),
    (57, 1178.0, 423.4, 28.0, 36.0),
    (58, 1149.1, 633.5, 24.0, 24.0),
    (59, 1149.1, 587.2, 24.0, 24.0),
    (60, 1149.1, 540.9, 24.0, 24.0),
    (61, 1149.1, 492.8, 24.0, 24.0),
    (62, 1205.3, 517.5, 24.0, 24.0),
    (63, 1261.5, 517.5, 24.0, 24.0),
    (64, 1317.7, 517.5, 24.0, 24.0),
    (65, 1373.8, 517.5, 24.0, 24.0),
    (66, 1373.8, 480.2, 24.0, 24.0),
    (67, 1317.7, 480.2, 24.0, 24.0),
    (68, 1261.5, 480.2, 24.0, 24.0),
    (69, 1205.3, 480.2, 24.0, 24.0),
    (70, 1255.4, 402.0, 28.0, 36.0),
    (71, 1255.4, 360.8, 28.0, 36.0),
    (72, 1382.2, 360.8, 28.0, 36.0),
    (73, 1382.2, 402.0, 28.0, 36.0),
]

# Wall / edge segments extracted from 14.svg (absolute canvas coords).
# Tuple: (x1, y1, x2, y2).
PLAN_WALLS = [
    (149.6, 382.8, 490.6, 382.8),
    (149.6, 382.8, 149.6, 328.8),
    (149.6, 328.8, 33.9, 328.8),
    (33.9, 328.8, 33.9, 474.1),
    (33.9, 474.1, 490.6, 474.1),
    (490.6, 186.2, 490.6, 474.1),
    (490.6, 455.1, 1231.5, 455.1),
    (490.6, 186.2, 952.7, 186.2),
    (952.7, 131.9, 952.7, 455.1),
    (952.7, 131.9, 1015.0, 131.9),
    (1231.5, 340.1, 1231.5, 455.1),
    (1015.0, 131.9, 1231.5, 340.1),
    (1231.5, 340.1, 1406.8, 340.1),
    (1406.8, 340.1, 1406.8, 679.2),
    (1114.2, 679.2, 1406.8, 679.2),
    (1114.2, 455.1, 1114.2, 679.2),
    (490.6, 474.1, 490.6, 609.8),
    (328.8, 609.8, 490.6, 609.8),
    (328.8, 474.1, 328.8, 609.8),
    (409.4, 474.1, 409.4, 609.8),
    (490.6, 595.1, 776.8, 595.1),
    (586.8, 455.1, 586.8, 595.1),
    (681.8, 455.1, 681.8, 595.1),
    (776.8, 455.1, 776.8, 595.1),
    (860.5, 455.1, 860.5, 574.4),
    (776.8, 574.4, 860.5, 574.4),
]

PLAN_VIEWBOX = (30.9, 128.0, 1378.1, 554.1)


LEGACY_CHILD_LOCATION_RENAMES = {
    "Salka Piłka Nożna": "6. Football",
    "Salka Dżungla": "2. Magiczny Las",
    "Salka Kosmos": "4. Kosmos",
    "Salka Księżniczki": "3. Wróżki",
    "Salka Piraci": "1. Biały Dom",
    "Salka Kreatywna": "5. Zima",
    "Sala 1 - Biały Dom": "1. Biały Dom",
    "Sala 2 - Magiczny Las": "2. Magiczny Las",
    "Sala 3 - Wróżki": "3. Wróżki",
    "Sala 4 - Kosmos": "4. Kosmos",
    "Sala 5 - Zima": "5. Zima",
    "Sala 6 - Piłka nożna": "6. Football",
    "Loża 1 - Biały Dom": "1. Biały Dom",
    "Loża 2 - Magiczny Las": "2. Magiczny Las",
    "Loża 3 - Wróżki": "3. Wróżki",
    "Loża 4 - Kosmos": "4. Kosmos",
    "Loża 5 - Zima": "5. Zima",
    "Loża 6 - Football": "6. Football",
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
        "label": "Kierownik i recepcja",
        "hint": "Podgląd rezerwacji, statusów i dostępności sal.",
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
        "hint": "Pełny panel: nowe rezerwacje, edycja i usuwanie.",
    },
}

DAY_FILTERS = {
    "today": ("Dziś", 0),
    "tomorrow": ("Jutro", 1),
    "after_tomorrow": ("Pojutrze", 2),
}

WEEKDAY_LABELS = ("Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Ndz")
WEEKDAY_FULL_LABELS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
MONTH_FULL_LABELS = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
)

MONTH_STANDALONE_LABELS = (
    "styczeń",
    "luty",
    "marzec",
    "kwiecień",
    "maj",
    "czerwiec",
    "lipiec",
    "sierpień",
    "wrzesień",
    "październik",
    "listopad",
    "grudzień",
)



ROLE_NAV_ICONS = {
    "manager": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6.5h16M7 4h10a2 2 0 0 1 2 2v13H5V6a2 2 0 0 1 2-2Z"/><path d="M8 10h3M8 14h3M14 10h2M14 14h2"/></svg>""",
    "animators": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.2 4.5 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5-3.6-3.5 5-.7L12 3Z"/><path d="M5 20h14"/></svg>""",
    "kitchen": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v8M11 3v8M7 7h4M9 11v10"/><path d="M16 3v18M16 3c2 1.5 3 3.3 3 5.5S18 12 16 12"/></svg>""",
    "organizer": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h10a2 2 0 0 1 2 2v14H5V6a2 2 0 0 1 2-2Z"/><path d="M9 4V2M15 4V2M8 9h8M8 13h5M8 17h7"/></svg>""",
}

WAITERS = (
    "Ilya Tumilovich",
    "Hanna Hodmash",
    "Adrian Rybińczuk",
    "Nicole Piotrowiak",
    "Patrycja Zewar",
    "Julia Wojciechowka",
    "Paweł Osiałkowski",
    "Kain Niżnik",
    "Jan Ostrykiewicz",
)

SERVICE_OVERLAP_MESSAGE = "Godziny dodatków w tej rezerwacji nie mogą się nakładać."

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

