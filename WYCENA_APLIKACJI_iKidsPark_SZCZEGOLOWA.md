# Szczegolowa wycena aplikacji iKids Park

Data: 19 lipca 2026

## Wniosek glowny

Propozycja 12 000 PLN za calosc, czyli aplikacje, wdrozenie, szkolenia, biezace edycje i zmiany, jest za niska, jezeli oznacza przekazanie pelnej aplikacji i realna odpowiedzialnosc za jej utrzymanie.

Kwota 12 000 PLN moze miec sens tylko jako:

- oplata startowa za ograniczone wdrozenie,
- bez przeniesienia pelnych praw autorskich,
- bez nieograniczonych zmian,
- z jasno okreslonym zakresem prac,
- przy dodatkowym abonamencie 950 PLN miesiecznie.

Najrozsadniejszy model:

- 12 000 PLN netto: start, konfiguracja, wdrozenie, pierwsze szkolenie,
- 950 PLN netto miesiecznie: utrzymanie i pakiet do 25 godzin miesiecznie,
- prace ponad 25 godzin: minimum 80-120 PLN/h albo osobna wycena,
- wieksze funkcje: osobna oferta, nie w ramach abonamentu.

## Co klient realnie kupuje

Aplikacja iKids Park nie jest prosta strona internetowa. To system operacyjny do prowadzenia rezerwacji urodzin i pracy kilku dzialow.

Zakres aplikacji:

- panel rezerwacji urodzin,
- role dla kierownika/recepcji, animatorow, kuchni/cukierni i organizatora,
- formularz tworzenia oraz edycji rezerwacji,
- statusy rezerwacji,
- anulowanie z powodem,
- historia zmian,
- eksport CSV,
- API dostepnosci,
- plan sali z interaktywnymi lokalizacjami,
- kontrola konfliktow sal, stolikow i atrakcji,
- obsluga tortow, owocow, warsztatow, piniaty, maskotki i balonow,
- lokalna baza SQLite do testow,
- produkcyjna baza PostgreSQL/Supabase,
- Docker i wdrozenie Fly.io,
- PWA: manifest, service worker, ikony, strona offline,
- test systemowy sprawdzajacy glowne sciezki aplikacji.

To oznacza, ze klient nie placi tylko za kod. Placi za uporzadkowanie procesu pracy recepcji, kuchni, animatorow i organizatorow.

## Koszt odtworzenia

Gdyby podobny system budowac od zera, realny zakres pracy wygladalby mniej wiecej tak:

| Obszar | Szacunek godzin |
| --- | ---: |
| Analiza procesu rezerwacji i wymagan | 10-25 h |
| Projekt ekranow i przeplywow pracy | 15-40 h |
| Backend, baza danych, zapis rezerwacji | 35-70 h |
| Walidacje i konflikty terminow/lokalizacji | 25-55 h |
| Widoki rol pracownikow | 30-70 h |
| Plan sali i dostepnosc | 20-50 h |
| PWA, ikony, offline, responsywnosc | 15-35 h |
| Eksporty, historia zmian, narzedzia operacyjne | 15-35 h |
| Wdrozenie, Docker, Fly.io, Supabase | 10-25 h |
| Testy, poprawki, stabilizacja | 20-45 h |
| Razem | 195-450 h |

Przy bardzo niskiej stawce 38 PLN/h daje to 7 410 - 17 100 PLN, ale to jest stawka bardziej za proste wpisywanie danych do ewidencji pracy, a nie za odpowiedzialne tworzenie, wdrazanie i utrzymywanie aplikacji produkcyjnej.

Przy bardziej rynkowej stawce technicznej:

- 100 PLN/h: 19 500 - 45 000 PLN,
- 150 PLN/h: 29 250 - 67 500 PLN,
- 200 PLN/h: 39 000 - 90 000 PLN.

Dlatego realna wartosc wdrozenia tej aplikacji jest blizej 30 000 - 60 000 PLN niz 12 000 PLN, jesli w cenie ma byc calosc odpowiedzialnosci.

## Ocena propozycji 12 000 PLN

### Kiedy 12 000 PLN jest za malo

12 000 PLN jest za malo, jezeli klient oczekuje:

- pelnych praw do kodu,
- wdrozenia produkcyjnego,
- szkolenia pracownikow,
- dowolnych zmian w przyszlosci,
- napraw bledow bez limitu,
- dostosowywania aplikacji pod nowe procesy,
- odpowiedzialnosci za dane i ciaglosc pracy,
- kontaktu "na juz" przy problemach,
- rozwoju systemu bez dodatkowych platnosci.

W takim wariancie bierzesz na siebie ryzyko firmy za kwote, ktora moze zostac zjedzona przez kilka tygodni poprawek.

### Kiedy 12 000 PLN jest akceptowalne

12 000 PLN moze byc akceptowalne, jezeli oferta jest zapisana tak:

> Oplata startowa 12 000 PLN netto obejmuje uruchomienie aktualnej wersji aplikacji, podstawowa konfiguracje, jedno szkolenie zespolu i poprawki startowe do ustalonego zakresu. Kod pozostaje wlasnoscia wykonawcy, a klient otrzymuje licencje na korzystanie z aplikacji.

Wtedy 12 000 PLN nie jest sprzedaza calego produktu. To oplata wdrozeniowa.

## Ocena abonamentu 950 PLN miesiecznie

950 PLN miesiecznie wynika z kalkulacji:

25 godzin x 38 PLN/h = 950 PLN.

Matematycznie to sie zgadza, ale biznesowo trzeba uwazac. 38 PLN/h jest dobra stawka dla prostych prac administracyjnych, wpisywania danych albo ewidencji pracy. Dla utrzymania aplikacji, zmian w kodzie, reagowania na problemy i odpowiedzialnosci za system produkcyjny to bardzo nisko.

950 PLN miesiecznie moze byc dobre jako abonament podstawowy, ale z warunkami:

- obejmuje maksymalnie 25 godzin miesiecznie,
- niewykorzystane godziny nie przechodza na kolejny miesiac,
- czas reakcji jest rozsadny, np. 1-2 dni robocze,
- pilne awarie sa rozliczane osobno albo maja wyzszy pakiet,
- wieksze funkcje sa wyceniane oddzielnie,
- abonament nie obejmuje odpowiedzialnosci za utrate danych spowodowana przez klienta, hosting albo zewnetrzne uslugi,
- klient nie otrzymuje nielimitowanego rozwoju aplikacji.

## Moja rekomendacja cenowa

### Wariant bezpieczny i uczciwy

Najlepsza propozycja:

- 12 000 PLN netto jednorazowo za wdrozenie startowe,
- 950 PLN netto miesiecznie za utrzymanie i drobne prace do 25 godzin,
- 120 PLN/h netto za prace ponad pakiet,
- wieksze funkcje osobno wyceniane,
- brak przekazania pelnych praw do kodu,
- licencja na korzystanie z aplikacji tak dlugo, jak abonament jest aktywny.

To jest dobra oferta dla klienta i jeszcze nie zabija wartosci aplikacji.

### Wariant, jesli klient chce pelne prawa

Jesli klient chce kupic wszystko na wlasnosc, rekomendacja:

- minimum 40 000 PLN netto za kod i prawa,
- dodatkowo platne wdrozenie/szkolenie albo wlaczone dopiero od 50 000 PLN netto,
- utrzymanie 950 PLN miesiecznie jako osobna umowa,
- zmiany rozwojowe osobno.

12 000 PLN za pelne prawa do aplikacji to zdecydowanie za malo.

### Wariant abonamentowy

Mozna tez sprzedawac aplikacje jako usluge:

- 5 000 - 12 000 PLN netto oplaty wdrozeniowej,
- 699 - 1 499 PLN netto miesiecznie abonamentu,
- brak przekazania kodu,
- konfiguracja pod obiekt,
- wsparcie i male zmiany w limicie godzin.

Przy tym modelu aplikacja zostaje Twoim produktem, a nie jednorazowo sprzedanym kodem.

## Jak zapisac zakres 25 godzin

Proponowany zapis:

> Abonament 950 PLN netto miesiecznie obejmuje do 25 godzin miesiecznie prac utrzymaniowych, administracyjnych i drobnych zmian w aplikacji. Prace ponad limit sa rozliczane wedlug stawki 120 PLN netto za godzine albo wyceniane indywidualnie, jesli dotycza nowych funkcji. Niewykorzystane godziny nie przechodza na kolejne miesiace.

Warto tez dopisac:

> Abonament nie obejmuje nieograniczonego rozwoju aplikacji, przebudowy architektury, nowych duzych modulow, integracji z systemami zewnetrznymi, migracji danych poza uzgodnionym zakresem ani pracy w trybie natychmiastowym poza godzinami ustalonymi w umowie.

## Co powinno byc w cenie 12 000 PLN

W cenie 12 000 PLN mozna uczciwie zawrzec:

- uruchomienie aktualnej wersji aplikacji,
- konfiguracje podstawowych danych obiektu,
- sprawdzenie dzialania na produkcji,
- jedno szkolenie online lub na miejscu,
- przygotowanie krotkiej instrukcji obslugi,
- poprawki startowe do 10-15 godzin,
- miesiac spokojnego wsparcia technicznego.

Nie powinno byc w tej cenie:

- pelnego przeniesienia praw autorskich,
- nielimitowanych zmian,
- tworzenia duzych nowych modulow,
- integracji z platnosciami, SMS, mailami, kalendarzami,
- przeprojektowania calego UI,
- gwarancji pracy 24/7,
- odpowiedzialnosci za wszystkie przyszle potrzeby firmy.

## Proponowana oferta dla klienta

Mozesz wyslac taka wersje:

> Proponuje wdrozenie systemu iKids Park w modelu: 12 000 PLN netto jednorazowo za uruchomienie, konfiguracje i szkolenie zespolu oraz 950 PLN netto miesiecznie za biezace utrzymanie i drobne prace do limitu 25 godzin miesiecznie. Kod pozostaje moja wlasnoscia, a firma otrzymuje licencje na korzystanie z systemu. Prace powyzej 25 godzin miesiecznie oraz wieksze nowe funkcje sa wyceniane osobno.

To brzmi uczciwie i profesjonalnie. Klient ma niski prog wejscia, a Ty nie oddajesz calej wartosci aplikacji za 12 000 PLN.

## Najwazniejsza granica negocjacyjna

Nie zgadzalbym sie na:

- 12 000 PLN za pelna sprzedaz kodu,
- 12 000 PLN z nielimitowanymi zmianami,
- 950 PLN miesiecznie bez limitu godzin,
- odpowiedzialnosc za produkcyjna baze danych bez jasnych zasad backupu i dostepu,
- prace pilne i poza godzinami bez osobnego rozliczenia.

Zgodzilbym sie na:

- 12 000 PLN jako oplata wdrozeniowa,
- 950 PLN miesiecznie jako retainer do 25 godzin,
- kod zostaje Twoj,
- wieksze zmiany platne osobno,
- umowa opisuje zakres, limit godzin i odpowiedzialnosc.

## Krotka opinia

12 000 PLN plus 950 PLN miesiecznie to dobra propozycja tylko wtedy, gdy traktujesz to jako wejscie w stala wspolprace, a nie jako sprzedaz calej aplikacji. Jezeli klient ma dostac aplikacje, wdrozenie, szkolenia, biezace edycje i przyszle zmiany bez granic, to 12 000 PLN jest zdecydowanie za malo.

Najlepsza odpowiedz negocjacyjna:

> Tak, moge wejsc w 12 000 PLN za wdrozenie, ale aplikacja zostaje licencjonowana, a abonament 950 PLN obejmuje maksymalnie 25 godzin miesiecznie. Wieksze funkcje i prace ponad limit rozliczamy osobno.
