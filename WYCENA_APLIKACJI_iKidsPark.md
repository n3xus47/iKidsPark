# Wycena aplikacji iKids Park

Data wyceny: 19 lipca 2026

## Szybka odpowiedz

Realistyczna wartosc aplikacji iKids Park, oceniana na podstawie samego repozytorium i zakresu funkcji, to:

| Scenariusz sprzedazy | Realna cena |
| --- | ---: |
| Sprzedaz samego kodu bez wdrozenia, gwarancji i wsparcia | 8 000 - 20 000 PLN |
| Sprzedaz dzialajacej aplikacji dla jednego obiektu, z przekazaniem kodu i konfiguracji | 20 000 - 45 000 PLN |
| Sprzedaz z wdrozeniem, szkoleniem, poprawkami startowymi i 1-3 miesiacami wsparcia | 35 000 - 70 000 PLN |
| Sprzedaz jako produkt/SaaS z klientami i przychodem | osobna wycena: zwykle wielokrotnosc rocznego zysku lub przychodu |

Moja rekomendowana cena wywolawcza: 49 000 PLN netto.

Minimalna cena, ponizej ktorej nie warto sprzedawac pelnych praw do kodu: 25 000 PLN netto.

Najbardziej prawdopodobna cena transakcyjna przy rozmowie z lokalnym parkiem zabaw, sala urodzinowa lub podobnym obiektem: 30 000 - 45 000 PLN netto.

## Co jest wyceniane

Repozytorium zawiera aplikacje webowa do obslugi rezerwacji urodzinowych w iKids Park. Zakres techniczny:

- backend w Pythonie w pliku `main.py`,
- okolo 10 400 linii kodu aplikacyjnego,
- lokalna baza SQLite oraz wariant produkcyjny PostgreSQL/Supabase,
- wdrozenie Docker/Fly.io,
- PWA: manifest, service worker, tryb offline, ikony,
- widoki dla rol: kierownik/recepcja, animatorzy, kuchnia/cukiernia, organizatorzy,
- formularz rezerwacji,
- walidacja konfliktow terminow, lokalizacji i atrakcji,
- plan sali z lokalizacjami,
- historia zmian rezerwacji,
- anulowanie rezerwacji z powodem,
- przypisywanie obslugi,
- eksport CSV,
- API dostepnosci,
- test systemowy obejmujacy endpointy i logike rezerwacji,
- dokumentacja schematu bazy danych.

To nie jest tylko makieta. To dzialajacy system operacyjny dla konkretnego procesu w firmie.

## Metoda wyceny

Wycena opiera sie na trzech perspektywach:

1. Koszt odtworzenia

Gdyby klient zamowil podobna aplikacje od programisty lub malego software house'u, koszt obejmowalby analize procesu, UX/UI, backend, walidacje, baze danych, wdrozenie, testy i poprawki po uruchomieniu.

Orientacyjnie:

- 180 - 350 godzin pracy przy aplikacji tej klasy,
- 150 - 250 PLN/h przy wycenie freelancerskiej lub malego zespolu,
- koszt odtworzenia: 27 000 - 87 500 PLN.

Zewnetrzne punkty odniesienia rynkowego:

- ITCraft podaje, ze proste aplikacje web/mobile zwykle mieszcza sie w zakresie 30 000 - 100 000 PLN.
- The Story podaje orientacyjne stawki: developer 140 - 240 PLN/h, senior developer 200 - 400 PLN/h.
- UniqueDevs podaje, ze MVP aplikacji czesto zaczyna sie od 30 000 - 100 000 PLN.

2. Wartosc uzytkowa dla kupujacego

Dla parku zabaw, sali urodzinowej albo centrum rodzinnego aplikacja moze dawac realna oszczednosc:

- mniej pomylek w rezerwacjach,
- mniej konfliktow sal/stolikow/atrakcji,
- szybsza praca recepcji,
- jasne informacje dla kuchni, cukierni i animatorow,
- historia zmian i lepsza kontrola operacyjna,
- mniej pracy na papierze, w Excelu albo w komunikatorach.

Jesli aplikacja oszczedza firmie nawet 10-20 godzin pracy miesiecznie i ogranicza kilka kosztownych pomylek rocznie, kwota 30 000 - 50 000 PLN jest biznesowo uzasadniona.

3. Wartosc inwestycyjna

Jako sam kod, bez klientow i bez przychodu, aplikacja ma nizsza wartosc od kosztu jej stworzenia. Kupujacy bierze na siebie ryzyko:

- utrzymania,
- dopasowania do swoich procesow,
- poprawy bezpieczenstwa,
- rozwoju,
- migracji danych,
- hostingu,
- wsparcia uzytkownikow.

Dlatego sprzedaz samego kodu zwykle bedzie tansza niz stworzenie aplikacji od zera.

## Czynniki podnoszace wartosc

- Aplikacja jest dopasowana do realnego procesu rezerwacji urodzin.
- Ma kilka rol pracownikow, a nie tylko jeden ekran administratora.
- Obsluguje konflikty terminow i lokalizacji.
- Ma eksport danych i historie zmian.
- Ma wariant produkcyjny z Supabase i Fly.io.
- Ma PWA/offline, wiec moze dzialac wygodniej na tabletach/telefonach.
- Ma test systemowy i dokumentacje bazy.
- Jest duzo logiki domenowej, ktorej nie da sie odtworzic jednym prostym szablonem.

## Czynniki obnizajace wartosc

- Kod jest mocno monolityczny: jeden plik `main.py` ma ponad 10 tys. linii.
- Brak pelnego frameworka typu Django/FastAPI, panelu admina i standardowej struktury projektu.
- Brak pelnej automatyzacji testow jednostkowych i CI/CD.
- Brak logowania uzytkownikow z kontami, uprawnieniami i haslami.
- Brak platnosci online, CRM, faktur, maili/SMS i integracji kalendarzy.
- Aplikacja jest mocno szyta pod jeden obiekt, wiec dla innego klienta moze wymagac zmian.
- Sama aplikacja bez przychodow nie jest jeszcze skalowalnym biznesem.

## Rekomendowane strategie sprzedazy

Najlepsza strategia nie jest sprzedaz samego kodu, tylko sprzedaz wdrozenia.

### Opcja A: licencja dla jednego obiektu

Cena: 19 000 - 29 000 PLN netto

Co obejmuje:

- dostep do aplikacji,
- konfiguracje pod jeden obiekt,
- podstawowe wdrozenie,
- brak przekazania pelnych praw autorskich do kodu.

To dobra opcja, jesli chcesz sprzedac aplikacje kilku podobnym firmom.

### Opcja B: wdrozenie z konfiguracja

Cena: 35 000 - 55 000 PLN netto

Co obejmuje:

- uruchomienie aplikacji,
- dopasowanie nazw sal/stolikow/atrakcji,
- migracje poczatkowych danych,
- szkolenie pracownikow,
- 1 miesiac wsparcia,
- poprawki startowe.

To najbardziej sensowna oferta handlowa.

### Opcja C: sprzedaz pelnych praw do kodu

Cena: 45 000 - 80 000 PLN netto

Warto bronic ceny, bo po sprzedazy pelnych praw tracisz mozliwosc latwego sprzedawania tego samego systemu innym klientom.

Minimalna sensowna cena przy pelnym przeniesieniu praw: 40 000 PLN netto.

### Opcja D: abonament SaaS

Cena: 399 - 999 PLN netto miesiecznie za obiekt

Mozliwy model:

- 499 PLN/miesiac: podstawowa rezerwacja i widoki rol,
- 799 PLN/miesiac: historia, eksport, wsparcie, konfiguracja,
- 999+ PLN/miesiac: indywidualne dopasowania, kopie zapasowe, SLA.

Przy 10 klientach po 699 PLN/miesiac aplikacja generowalaby 6 990 PLN miesiecznego przychodu. Wtedy jej wartosc biznesowa bylaby znacznie wyzsza niz wartosc samego kodu.

## Ile mozesz za nia dostac

Najuczciwsza odpowiedz:

- jesli chcesz sprzedac szybko sam kod: 10 000 - 20 000 PLN,
- jesli sprzedajesz gotowe wdrozenie dla jednej firmy: 30 000 - 45 000 PLN,
- jesli sprzedajesz z pelnymi prawami i wsparciem: 45 000 - 70 000 PLN,
- jesli zrobisz z tego abonament i zdobedziesz klientow: wartosc moze przekroczyc 100 000 PLN, ale dopiero gdy pojawia sie realne przychody.

Moja praktyczna rekomendacja negocjacyjna:

- wystaw oferte na 49 000 PLN netto za wdrozenie,
- zejdz maksymalnie do 35 000 PLN netto,
- nie sprzedawaj pelnych praw autorskich ponizej 40 000 PLN netto,
- jesli klient chce tylko uzywac aplikacji, proponuj abonament 699 PLN netto miesiecznie plus oplata wdrozeniowa 5 000 - 12 000 PLN.

## Co zrobic, zeby podniesc wycene

Przed sprzedaza warto dodac albo uporzadkowac:

- logowanie uzytkownikow i role kont pracownikow,
- panel konfiguracji sal, stolikow i atrakcji,
- lepszy podzial kodu na moduly,
- automatyczne kopie zapasowe,
- instrukcje wdrozenia dla klienta,
- demo z przykladowymi danymi,
- prosta strone ofertowa,
- cennik licencji,
- umowe licencyjna zamiast sprzedazy calego kodu,
- podstawowy monitoring bledow,
- testy automatyczne uruchamiane przed wdrozeniem.

Po tych zmianach oferta wdrozeniowa moze byc bardziej wiarygodna w zakresie 50 000 - 90 000 PLN.

## Proponowany tekst dla kupujacego

System iKids Park to gotowa aplikacja do zarzadzania rezerwacjami urodzinowymi w parku zabaw lub centrum rodzinnej rozrywki. Obejmuje obsluge wielu rol pracownikow, plan sali, konflikt terminow, szczegoly tortow i atrakcji, historie zmian, eksport danych oraz wdrozenie produkcyjne. Aplikacja moze zastapic prace w Excelu, na papierze i w komunikatorach, ograniczajac pomylki organizacyjne i skracajac czas obslugi rezerwacji.

Cena wdrozenia systemu: 49 000 PLN netto.

W cenie mozna ujac konfiguracje pod obiekt, uruchomienie, szkolenie zespolu i miesiac wsparcia po starcie.

## Zrodla rynkowe

- ITCraft: proste aplikacje web/mobile: 30 000 - 100 000 PLN: https://itcraftapps.com/pl/cennik-aplikacje-mobilne/
- The Story: orientacyjne stawki specjalistow IT, m.in. developer 140 - 240 PLN/h i senior developer 200 - 400 PLN/h: https://thestory.is/pl/journal/koszt-aplikacji-webowej/
- UniqueDevs: MVP aplikacji czesto 30 000 - 100 000 PLN: https://uniquedevs.com/blog/ile-kosztuje-stworzenie-aplikacji-mobilnej/

## Zastrzezenie

To jest wycena orientacyjna przygotowana na podstawie kodu i widocznego zakresu funkcji, a nie formalna wycena rzeczoznawcy. Finalna cena zalezy od tego, czy sprzedajesz kod, licencje, wdrozenie, wsparcie, baze klientow, marke, domeny, dokumentacje i prawa autorskie.
