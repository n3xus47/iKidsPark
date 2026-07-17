# iKids Park - Rezerwacje urodzin

Aplikacja webowa do obsługi rezerwacji urodzinowych. Backend: Python HTTP Server + **PostgreSQL (Supabase)** + hosting **Fly.io** (Docker).

## Architektura

- **Baza:** Supabase Postgres (`DATABASE_URL`)
- **Aplikacja (live):** kontener Docker na Fly.io → https://ikidspark.fly.dev
- **Lokalnie:** `python3 main.py` (HTTP) albo `IKIDS_HTTPS=1` z lokalnym CA

## Lokalnie (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # wklej DATABASE_URL (pooler Supabase)
python3 main.py
```

Domyślnie: `http://127.0.0.1:8000` (kolejny wolny port, jeśli 8000 zajęty).

Z telefonu w tej samej sieci: `http://<IP-komputera>:8000`.

Opcjonalny HTTPS lokalny: `IKIDS_HTTPS=1 python3 main.py` (+ zaufanie `ikids-local-ca.crt` na telefonie).

## Fly.io (produkcja / testy publiczne)

Adres: **https://ikidspark.fly.dev**

```bash
export PATH="$HOME/.fly/bin:$PATH"
./scripts/fly-deploy.sh
```

Przydatne:

```bash
fly status -a ikidspark
fly logs -a ikidspark
fly deploy -a ikidspark
```

Kod lokalny **nie** aktualizuje się sam na Fly — po zmianach trzeba `fly deploy`.

## Pliki deploy

- `Dockerfile`, `fly.toml`, `scripts/fly-deploy.sh`
- `supabase/schema.sql`, `supabase/seed_from_sqlite.sql`

## Zakres funkcji

- role: Kierownik/Recepcja, Animatorzy, Cukiernia, Kuchnia,
- filtry dni, formularz rezerwacji, anulowanie, historia,
- plan sali SVG, API `/api/availability`,
- blokady nakładających się lokalizacji i atrakcji.
