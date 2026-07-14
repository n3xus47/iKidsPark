# iKids Park - Rezerwacje urodzin

Wewnętrzna aplikacja webowa do obsługi rezerwacji urodzinowych. Prototyp działa bez zależności zewnętrznych na Python HTTP Server + SQLite, ale model danych jest przygotowany pod łatwe przeniesienie do PostgreSQL/Supabase.

## Uruchomienie

```bash
python3 main.py
```

Aplikacja domyślnie działa pod adresem http://127.0.0.1:8000. Jeśli port 8000 jest zajęty, serwer automatycznie wybierze kolejny wolny port i wypisze go w terminalu.

## Zakres

- role: Kierownik/Recepcja, Animatorzy, Cukiernia, Kuchnia,
- szybkie filtry: Dziś, Jutro, Pojutrze,
- formularz dodawania i pełnej edycji rezerwacji,
- status Aktywna/Anulowana z wymaganym powodem anulowania,
- historia zmian rezerwacji,
- plan sali SVG z podglądem wolne/zajęte,
- API dostępności na żywo: `/api/availability`,
- blokada nakładających się rezerwacji tej samej salki lub stolika,
- blokada atrakcji w oknie 17:50-18:10 wokół godziny scenicznej 18:00.

## Struktura bazy

W aplikacji dostępna jest strona `/schema` z proponowanym schematem PostgreSQL/Supabase. Najważniejsze tabele:

- `reservations` - jeden agregat rezerwacji z zakresem czasu, lokalizacjami, usługami, statusem i anulowaniem,
- `reservation_history` - historia append-only z pełnym snapshotem JSON po każdej zmianie.
