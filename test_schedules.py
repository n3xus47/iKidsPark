#!/usr/bin/env python3
"""Testy widoku grafiku miesiąc/tydzień (układ grafik4600)."""
from __future__ import annotations

import calendar
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402


def sample_entries(month: str = "2026-07") -> list[dict[str, object]]:
    return [
        {
            "name": "Alicja Fitzner",
            "position": "Animator",
            "department": "animatorzy",
            "week_start": "2026-07-06",
            "shifts": [
                {
                    "day": "Piątek",
                    "date": "2026-07-10",
                    "date_label": "10.07",
                    "month": month,
                    "shift": "15-21",
                    "hours": "6",
                },
                {
                    "day": "Niedziela",
                    "date": "2026-07-12",
                    "date_label": "12.07",
                    "month": month,
                    "shift": "9-21",
                    "hours": "12",
                },
            ],
            "total_hours": "18",
        },
        {
            "name": "Karol Pańczyk",
            "position": "Animator",
            "department": "animatorzy",
            "week_start": "2026-07-06",
            "shifts": [
                {
                    "day": "Poniedziałek",
                    "date": "2026-07-06",
                    "date_label": "06.07",
                    "month": month,
                    "shift": "10-12",
                    "hours": "2",
                },
                {
                    "day": "Środa",
                    "date": "2026-07-08",
                    "date_label": "08.07",
                    "month": month,
                    "shift": "-",
                    "hours": "",
                },
            ],
            "total_hours": "2",
        },
    ]


def test_build_grafik_month_model_days_and_mapping() -> None:
    month = "2026-07"
    model = main.build_grafik_month_model(sample_entries(month), month)
    days_in_month = calendar.monthrange(2026, 7)[1]
    assert len(model["schedule_rows"]) == days_in_month
    assert len(model["employees"]) == 2
    names = [emp["name"] for emp in model["employees"]]
    assert names == sorted(names, key=lambda value: main.normalize_search_text(value))

    shifts = model["shifts_by_date"]["2026-07-10"]
    assert "Alicja Fitzner" in shifts
    assert shifts["Alicja Fitzner"]["shift"] == "15-21"
    assert shifts["Alicja Fitzner"]["hours"] == "6"

    # wolne / "-" nie trafia do shifts_by_date
    assert "Karol Pańczyk" not in model["shifts_by_date"].get("2026-07-08", {})

    totals = model["day_totals"]["2026-07-12"]
    assert totals["people"] == 1
    assert totals["hours"] == 12.0

    weekend = next(row for row in model["schedule_rows"] if row["date"] == "2026-07-11")
    assert weekend["is_off"] is True


def test_build_grafik_week_model() -> None:
    week = "2026-07-06"
    model = main.build_grafik_week_model(sample_entries(), week, month="2026-07")
    assert len(model["schedule_rows"]) == 7
    assert model["week"] == week
    assert model["schedule_rows"][0]["date"] == "2026-07-06"
    assert model["schedule_rows"][-1]["date"] == "2026-07-12"
    assert "Karol Pańczyk" in model["shifts_by_date"]["2026-07-06"]
    assert "Alicja Fitzner" in model["shifts_by_date"]["2026-07-10"]


def test_shift_period_bucket() -> None:
    assert main.shift_period_bucket("9-21") == "morning"
    assert main.shift_period_bucket("12-20") == "afternoon"
    assert main.shift_period_bucket("15:30-21") == "afternoon"
    assert main.shift_period_bucket("16-21") == "evening"
    assert main.shift_period_bucket("-") == "afternoon"
    assert main.shift_period_bucket("10- 20") == "morning"
    assert main.shift_period_bucket("9-15.30") == "morning"


def test_normalize_and_nonwork_shifts() -> None:
    assert main.normalize_schedule_shift("10- 20") == "10-20"
    assert main.normalize_schedule_shift("9-15.30") == "9-15:30"
    assert main.normalize_schedule_shift("11-21B") == "11-21"
    assert main.schedule_shift_is_work("15-21") is True
    assert main.schedule_shift_is_work("U") is False
    assert main.schedule_shift_is_work("?") is False
    assert main.schedule_shift_is_work("wolne") is False


def test_estimate_hours_from_shift() -> None:
    assert main.estimate_hours_from_shift("9-21") == 12.0
    assert main.estimate_hours_from_shift("15-21") == 6.0
    assert main.estimate_hours_from_shift("15:30-21") == 5.5
    assert main.cell_hours_value("9-21", "12") == 12.0
    assert main.cell_hours_value("9-21", "") == 12.0


def test_report_role_for_mapping() -> None:
    assert main.report_role_for("Animator") == "Animatorzy"
    assert main.report_role_for("Organizator Urodzin") == "Organizator urodzin"
    assert main.report_role_for("Kierownik Zmiany") == "Administrator"
    assert main.report_role_for("Barman") == "Bar"
    assert main.report_role_for("Kelner") == "Kelnerzy"
    assert main.report_role_for("Pracownia Twórcza") == "Pracownia kreatywna"
    assert main.report_role_for("Osoba sprzątająca") == "Sprzątaczki + zmywak"
    assert main.report_role_for("Kierownik animatorów") == "Kierownik animatorów"
    assert main.report_role_for("HR") == "Kierownik HR"
    assert main.report_role_for("Nieznane stanowisko") == "Inne"
    assert main.report_role_for_shift("Agata Krzyżanowska", "", "9-17") == "Kierownik HR"
    assert main.report_role_for_shift("Adam Tur", "", "10-20") == "Kierownik animatorów"
    assert main.report_role_for_shift("Maciej Pacholak", "", "10-21") == "Kuchnia"
    assert main.report_role_for_shift("Weronika Walkowiak", "", "9-21") == "Administrator"
    assert main.report_role_for_shift("Hanna Hodmash", "Kelner", "11-21B") == "Bar"
    assert main.shift_has_bar_marker("11-21B") is True
    assert main.normalize_schedule_shift("11-21B") == "11-21"


def test_format_staff_count_half_and_full() -> None:
    assert main.format_staff_count(0, 0, 0) == "0"
    assert main.format_staff_count(3, 3, 0) == "3"
    assert main.format_staff_count(2, 0, 2) == "2 (2 1/2)"
    assert main.format_staff_count(5, 1, 4) == "5 (4 1/2, 1 cały)"


def test_half_shift_boundary() -> None:
    assert main.is_half_shift(6.0) is True
    assert main.is_half_shift(6.01) is False
    assert main.is_half_shift(7.0) is False
    assert main.cell_hours_value("15-21", "6") == 6.0
    assert main.is_half_shift(main.cell_hours_value("15-21", "6")) is True
    assert main.is_half_shift(main.cell_hours_value("13-21", "8")) is False


def test_standing_konservator() -> None:
    report = main.build_shift_report("2026-07-08", [], reservation_rows=[])
    assert report["roles"]["Konserwator"] == {"full": 1, "half": 0}
    assert report["total_people"] == 1


def test_build_shift_report_counts() -> None:
    entries = sample_entries()
    entries.append(
        {
            "name": "Jan Kowalski",
            "position": "Organizator Urodzin",
            "department": "animatorzy",
            "week_start": "2026-07-06",
            "shifts": [
                {
                    "day": "Piątek",
                    "date": "2026-07-10",
                    "date_label": "10.07",
                    "month": "2026-07",
                    "shift": "9-15",
                    "hours": "6",
                },
            ],
            "total_hours": "6",
        }
    )
    report = main.build_shift_report("2026-07-10", entries, reservation_rows=[])
    roles = report["roles"]
    assert roles["Animatorzy"]["half"] == 1
    assert roles["Animatorzy"]["full"] == 0
    assert roles["Organizator urodzin"]["half"] == 1
    assert report["total_people"] == 3
    assert report["roles"]["Konserwator"]["full"] == 1
    assert report["metrics"]["banquets"] == 0
    assert report["metrics"]["tables"] == 0


def test_format_shift_report_text_layout() -> None:
    report = {
        "metrics": {
            "banquets": 3,
            "tables": 0,
            "animations": 2,
            "pinatas": 0,
            "workshops": 0,
        },
        "roles": {
            label: {"full": 0, "half": 0} for label in main.SHIFT_REPORT_ROLE_ORDER
        },
        "total_people": 2,
    }
    report["roles"]["Administrator"]["full"] = 1
    report["roles"]["Bar"]["full"] = 1
    report["roles"]["Bar"]["half"] = 1
    text = main.format_shift_report_text(report)
    assert text.startswith("Dzień dobry 👋\n")
    assert "🎂 Bankiety – 3" in text
    assert "🪑 Rezerwacje – 0" in text
    assert "🎭 Animacje – 2" in text
    assert "👔 Administrator - 1" in text
    assert "🍸 Bar - 2 (1 1/2, 1 cały)" in text
    assert text.endswith("👥 Razem: 2 osób na zmianie")


def test_render_schedule_grafik_grid_contains_table() -> None:
    month = "2026-07"
    model = main.build_grafik_month_model(sample_entries(month), month)
    html = main.render_schedule_grafik_grid(
        model,
        title="lipiec 2026",
        subtitle="Animatorzy",
        role="home",
        day="today",
        department="animatorzy",
        months=[month],
        view="month",
        shift_reports=main.build_shift_reports_for_dates(
            [str(row["date"]) for row in model["schedule_rows"][:3]],
            sample_entries(month),
        ),
    )
    assert 'id="grafik"' in html
    assert 'data-view="month"' in html
    assert "Alicja Fitzner" in html
    assert "Karol Pańczyk" in html
    assert "grafiki-roster" in html
    assert "shifts-roster" in html
    assert "shifts-count" in html
    assert "Na zmianie" in html
    assert "shift-prev-day" in html
    assert "shift-report-copy" in html
    assert "Raport zmiany" in html
    assert "shift-report-text" not in html
    assert "window.grafikShiftReports" in html
    assert "window.grafikShiftsData" in html
    assert "15-21" in html
    assert "Imię i nazwisko" in html
    assert 'data-employee="' in html
    assert "hours-total-row" in html
    assert "col-summary" in html


def test_render_schedule_grafik_week_grid() -> None:
    week = "2026-07-06"
    model = main.build_grafik_week_model(sample_entries(), week, month="2026-07")
    html = main.render_schedule_grafik_grid(
        model,
        title="06.07 - 12.07",
        subtitle="Animatorzy",
        role="home",
        day="today",
        department="animatorzy",
        months=["2026-07"],
        weeks=[week],
        week=week,
        view="week",
    )
    assert 'id="grafik"' in html
    assert 'data-view="week"' in html
    assert "schedule-table" not in html
    assert "Alicja Fitzner" in html
    assert "15-21" in html
    assert "shifts-roster" in html
    assert "grafiki-period-nav" not in html


def test_schedule_adjacent_month() -> None:
    assert main.schedule_adjacent_month("2026-07", -1) == "2026-06"
    assert main.schedule_adjacent_month("2026-07", 1) == "2026-08"
    assert main.schedule_adjacent_month("2026-12", 1) == "2027-01"
    assert main.schedule_adjacent_month("2026-01", -1) == "2025-12"


def test_schedule_adjacent_week() -> None:
    weeks = ["2026-07-06", "2026-07-13", "2026-07-20"]
    assert main.schedule_adjacent_week("2026-07-13", weeks, -1) == "2026-07-06"
    assert main.schedule_adjacent_week("2026-07-13", weeks, 1) == "2026-07-20"
    assert main.schedule_adjacent_week("2026-07-06", weeks, -1) == "2026-07-06"


def test_schedule_month_from_block_dates() -> None:
    from datetime import date

    july_week = [date(2026, 7, d) for d in range(20, 27)]
    assert main.dominant_schedule_month(july_week) == "2026-07"
    assert main.schedule_dates_form_contiguous_week(july_week) is True

    broken = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3), date(2026, 8, 1), date(2026, 8, 2)]
    assert main.schedule_dates_form_contiguous_week(broken) is False

    boundary = [
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 30),
        date(2026, 7, 31),
        date(2026, 8, 1),
        date(2026, 8, 2),
    ]
    assert main.schedule_dates_form_contiguous_week(boundary) is True
    assert main.dominant_schedule_month(boundary) == "2026-07"


def test_parse_schedule_maps_july_dates_even_on_august_sheet() -> None:
    date_row = ["", "", "", "20.07", "", "21.07", "", "22.07", "", "23.07", "", "24.07", "", "25.07", "", "26.07"]
    header = [
        "Lp.",
        "Imię i Nazwisko",
        "Stanowisko",
        "Poniedziałek",
        "H",
        "Wtorek",
        "H",
        "Środa",
        "H",
        "Czwartek",
        "H",
        "Piątek",
        "H",
        "Sobota",
        "H",
        "Niedziela",
        "H",
        "Ilość godzin",
    ]
    person = ["1", "Sandra Rutkowska", "Animator", "", "", "15-21", "6", "", "", "9-15", "6", "15-21", "6", "9-21", "12", "13-21", "8", "38"]
    rows = [date_row, header, person]

    august_copy = main.parse_schedule_sheet("Animatorzy 08.2026", rows)
    assert len(august_copy) == 1
    assert august_copy[0]["sheet_month"] == "2026-07"
    assert august_copy[0]["week_start"] == "2026-07-20"
    assert main.schedule_entry_has_month(august_copy[0], "2026-07") is True
    assert main.schedule_entry_has_month(august_copy[0], "2026-08") is False

    july_original = main.parse_schedule_sheet("Animatorzy 07.2026", rows)
    assert len(july_original) == 1
    assert july_original[0]["sheet_month"] == "2026-07"

    deduped = main.dedupe_schedule_entries(august_copy + july_original)
    assert len(deduped) == 1
    assert "07.2026" in str(deduped[0]["sheet"])


def main_cli() -> int:
    tests = [
        test_build_grafik_month_model_days_and_mapping,
        test_build_grafik_week_model,
        test_shift_period_bucket,
        test_normalize_and_nonwork_shifts,
        test_estimate_hours_from_shift,
        test_report_role_for_mapping,
        test_format_staff_count_half_and_full,
        test_half_shift_boundary,
        test_standing_konservator,
        test_build_shift_report_counts,
        test_format_shift_report_text_layout,
        test_render_schedule_grafik_grid_contains_table,
        test_render_schedule_grafik_week_grid,
        test_schedule_adjacent_month,
        test_schedule_adjacent_week,
        test_schedule_month_from_block_dates,
        test_parse_schedule_maps_july_dates_even_on_august_sheet,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  [OK] {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\nPassed: {len(tests) - failed}/{len(tests)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
