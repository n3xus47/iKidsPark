# iKids Park - Rezerwacje urodzin

Wewnętrzna aplikacja webowa do obsługi rezerwacji urodzinowych. Prototyp działa bez zależności zewnętrznych na Python HTTP Server + SQLite, ale model danych jest przygotowany pod łatwe przeniesienie do PostgreSQL/Supabase.

## Uruchomienie

```bash
python3 main.py
```

Aplikacja domyślnie działa w przeglądarce pod adresem `http://127.0.0.1:8000`. Jeśli port 8000 jest zajęty, serwer automatycznie wybierze kolejny wolny port i wypisze go w terminalu.

Żeby wejść z telefonu, podłącz telefon i komputer do tej samej sieci i otwórz `http://<IP-komputera>:8000`. Przy hotspocie Windows tworzonym z komputera adresem komputera jest zwykle `192.168.137.1`, więc telefon powinien otworzyć `http://192.168.137.1:8000`.

Opcjonalny tryb HTTPS można włączyć poleceniem `IKIDS_HTTPS=1 python3 main.py`. W tym trybie na telefonie trzeba zainstalować i zaufać certyfikatowi CA `ikids-local-ca.crt`; inaczej przeglądarka może pokazać `ERR_CERT_AUTHORITY_INVALID`.

## Działanie w sieci lokalnej

Aplikacja działa jako PWA/przeglądarkowy panel na jednym lokalnym serwerze. Komputer, na którym uruchomiono `python3 main.py`, jest serwerem i trzyma lokalną bazę `reservations.db`. Telefony, tablety i inne komputery nie mają osobnej kopii danych - otwierają tę samą aplikację przez przeglądarkę i zapisują do tej samej bazy na serwerze.

Przykład dla hotspotu Windows z komputera:

```text
http://192.168.137.1:8000
```

Przykład dla zwykłej domowej sieci Wi-Fi:

```text
http://192.168.0.60:8000
```

Adres `192.168.0.60` jest tylko przykładem - w domu trzeba użyć aktualnego adresu IP komputera w tej samej sieci co telefon. Rezerwacja dodana na laptopie jest widoczna na telefonie po wejściu na ten sam serwer i odświeżeniu widoku. Analogicznie rezerwacja dodana z telefonu zapisuje się w tej samej bazie na laptopie.

Jeśli telefon nie otwiera strony mimo poprawnego adresu, najczęściej blokuje ją Zapora Windows. Trzeba wtedy zezwolić na połączenia przychodzące TCP na port `8000` dla Pythona lub dodać regułę:

```powershell
New-NetFirewallRule -DisplayName "iKids Park HTTP 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Any
```

## PWA i skrót na ekranie

Przycisk `Pobierz` korzysta z mechanizmu PWA w przeglądarce. Nie instaluje pliku APK. Skrót na telefonie używa ikon `/app-icon-*.png`, które są generowane z `logo.png` na białym tle, z marginesem dopasowanym do masek ikon Androida/iOS. Jeśli ikona skrótu się nie odświeża, trzeba usunąć stary skrót/PWA z telefonu, wyczyścić dane strony w przeglądarce i dodać skrót ponownie.

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
