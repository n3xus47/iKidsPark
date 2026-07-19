from __future__ import annotations

import csv
import io


def build_csv_response(
    rows,
    *,
    is_table_reservation,
    format_date,
    format_time,
    display_locations,
    animations_from_row,
    format_service_window,
    is_enabled,
    service_durations,
    cake_candle_labels,
    status_labels,
) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Typ",
            "Data",
            "Start imprezy",
            "Goście razem",
            "Dzieci",
            "Dorośli",
            "Rodzic",
            "Telefon",
            "Solenizant",
            "Wiek",
            "Sala dzieci",
            "Lokalizacja dorosłych",
            "Animacja",
            "Rodzaj animacji",
            "Animacja czas",
            "Tort",
            "Motyw tortu",
            "Waga tortu",
            "Smak biszkoptu",
            "Nadzienie tortu",
            "Krem tortu",
            "\u015awieczka",
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
            "Balony",
            "Opis balonów",
            "Godzina balonów",
            "Status",
            "Powód anulowania",
            "Notatki",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["id"],
                "Rezerwacja stolika" if is_table_reservation(row) else "Bankiet",
                format_date(row["start_at"]),
                format_time(row["start_at"]),
                row.get("guest_total") or int(row["children_count"] or 0) + int(row["adults_count"] or 0),
                row["children_count"],
                row["adults_count"],
                row["parent_name"],
                row.get("parent_phone") or "",
                row["birthday_child_name"],
                row["birthday_child_age"],
                row["child_location"],
                display_locations(row["adult_location"]),
                "Tak" if animations_from_row(row) else "Nie",
                "; ".join(str(item.get("type") or "") for item in animations_from_row(row)),
                "; ".join(
                    format_service_window(item.get("at"), service_durations["animation_at"])
                    for item in animations_from_row(row)
                ),
                "Tak" if is_enabled(row, "cake_enabled") else "Nie",
                row["cake_theme"] or "",
                row["cake_weight"] or "",
                row["cake_sponge"] or "",
                row["cake_filling"] or "",
                row["cake_cream"] or "",
                cake_candle_labels.get(str(row.get("cake_candle") or ""), row.get("cake_candle") or ""),
                format_service_window(row["cake_at"], service_durations["cake_at"]),
                "Tak" if is_enabled(row, "fruit_enabled") else "Nie",
                row["fruit_plates"] or "",
                format_time(row["fruit_at"]),
                "Tak" if is_enabled(row, "culinary_workshops_enabled") else "Nie",
                row["culinary_workshops_type"] or "",
                format_service_window(row["culinary_workshops_at"], service_durations["culinary_workshops_at"]),
                "Tak" if is_enabled(row, "pinata_enabled") else "Nie",
                row["pinata_theme"] or "",
                format_service_window(row["pinata_at"], service_durations["pinata_at"]),
                "Tak" if is_enabled(row, "mascot_enabled") else "Nie",
                row["mascot_type"] or "",
                format_service_window(row["mascot_at"], service_durations["mascot_at"]),
                "Tak" if is_enabled(row, "balloons_enabled") else "Nie",
                row["balloons_description"] or "",
                format_time(row["balloons_at"]),
                status_labels[row["status"]],
                row["cancellation_reason"],
                row["notes"],
            ]
        )
    return ("\ufeff" + output.getvalue()).encode("utf-8")

