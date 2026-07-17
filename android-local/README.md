# iKids Park Local APK

Lokalny prototyp Android bez serwera i bez internetu. Aplikacja uruchamia natywny `WebView` z plikiem `assets/index.html`; rezerwacje są zapisywane w pamięci aplikacji przez `localStorage`.

## Build

```bash
cd android-local
./build-apk.sh
```

Wynik:

```text
android-local/dist/ikids-park-local.apk
```

## Instalacja przez USB

```bash
adb install -r android-local/dist/ikids-park-local.apk
```

Jeśli telefon pyta o zgodę, włącz debugowanie USB i zaakceptuj odcisk klucza komputera.

## Zakres prototypu

- działa w pełni lokalnie na telefonie,
- role: Kierownik, Animatorzy, Kuchnia, Organizator,
- filtry: dziś, jutro, pojutrze,
- dodawanie, edycja, anulowanie i usuwanie rezerwacji,
- lokalna walidacja konfliktów salek i stolików,
- blokada atrakcji w oknie 17:45-18:15,
- dostępność salek i stref stolików,
- eksport/import danych demo jako JSON.
