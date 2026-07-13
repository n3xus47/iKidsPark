from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_TITLE = "iKidsPark - Rezerwacje"
DB_PATH = Path(__file__).with_name("reservations.db")
HOST = "127.0.0.1"
PORT = 8000

THEME_ROOMS = [
    "Dżungla",
    "Kosmos",
    "Księżniczki",
    "Piraci",
    "Superbohaterowie",
    "Sala kreatywna",
]

YES_NO = ("Nie", "Tak")


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reservation_date TEXT NOT NULL,
                children_count INTEGER NOT NULL,
                adults_count INTEGER NOT NULL,
                parent_name TEXT NOT NULL,
                child_name TEXT NOT NULL,
                child_age INTEGER NOT NULL,
                theme_room TEXT NOT NULL,
                cake TEXT NOT NULL,
                animations TEXT NOT NULL,
                fruit TEXT NOT NULL,
                workshops TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )


def db_rows(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchall()


def execute(query: str, params: tuple = ()) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(query, params)


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def validate_reservation(data: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    errors: dict[str, str] = {}

    def text_field(name: str, label: str) -> str:
        value = data.get(name, "").strip()
        if not value:
            errors[name] = f"Pole \"{label}\" jest wymagane."
        return value

    def int_field(name: str, label: str, minimum: int, maximum: int) -> int:
        raw = data.get(name, "").strip()
        try:
            value = int(raw)
        except ValueError:
            errors[name] = f"Pole \"{label}\" musi być liczbą."
            return minimum
        if value < minimum or value > maximum:
            errors[name] = f"Pole \"{label}\" musi być w zakresie {minimum}-{maximum}."
        return value

    reservation_date = text_field("reservation_date", "Data")
    if reservation_date:
        try:
            datetime.strptime(reservation_date, "%Y-%m-%d")
        except ValueError:
            errors["reservation_date"] = "Podaj datę w poprawnym formacie."

    theme_room = data.get("theme_room", "").strip()
    if theme_room not in THEME_ROOMS:
        errors["theme_room"] = "Wybierz salę tematyczną z listy."

    cleaned: dict[str, object] = {
        "reservation_date": reservation_date,
        "children_count": int_field("children_count", "Liczba dzieci", 1, 80),
        "adults_count": int_field("adults_count", "Liczba dorosłych", 0, 80),
        "parent_name": text_field("parent_name", "Imię rodzica"),
        "child_name": text_field("child_name", "Imię dziecka"),
        "child_age": int_field("child_age", "Wiek dziecka", 1, 18),
        "theme_room": theme_room,
        "cake": "Tak" if data.get("cake") == "Tak" else "Nie",
        "animations": "Tak" if data.get("animations") == "Tak" else "Nie",
        "fruit": "Tak" if data.get("fruit") == "Tak" else "Nie",
        "workshops": "Tak" if data.get("workshops") == "Tak" else "Nie",
        "notes": data.get("notes", "").strip(),
    }
    return cleaned, errors


def save_reservation(values: dict[str, object]) -> None:
    execute(
        """
        INSERT INTO reservations (
            reservation_date, children_count, adults_count, parent_name, child_name,
            child_age, theme_room, cake, animations, fruit, workshops, notes, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            values["reservation_date"],
            values["children_count"],
            values["adults_count"],
            values["parent_name"],
            values["child_name"],
            values["child_age"],
            values["theme_room"],
            values["cake"],
            values["animations"],
            values["fruit"],
            values["workshops"],
            values["notes"],
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def get_reservations() -> list[sqlite3.Row]:
    return db_rows(
        """
        SELECT *
        FROM reservations
        ORDER BY reservation_date ASC, id DESC
        """
    )


def page_template(content: str, message: str = "", errors: dict[str, str] | None = None) -> bytes:
    errors = errors or {}
    alert = ""
    if message:
        alert = f'<div class="alert success">{escape(message)}</div>'
    elif errors:
        alert = '<div class="alert error">Popraw zaznaczone pola formularza.</div>'

    document = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1f2933;
      --muted: #607080;
      --line: #d8e0e8;
      --surface: #ffffff;
      --soft: #f4f7fa;
      --brand: #0f8f8c;
      --brand-dark: #0b6f6c;
      --accent: #f6a623;
      --danger: #bf2d2d;
      --ok: #18794e;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--soft);
    }}

    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }}

    .topbar {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 20px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }}

    .logo {{
      width: 44px;
      height: 44px;
      border-radius: 8px;
      background: linear-gradient(135deg, var(--brand), #48b66f);
      color: white;
      display: grid;
      place-items: center;
      font-weight: 800;
      flex: 0 0 auto;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(1.25rem, 2vw, 1.7rem);
      line-height: 1.15;
    }}

    .subtitle {{
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}

    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }}

    .layout {{
      display: grid;
      grid-template-columns: minmax(320px, 430px) minmax(0, 1fr);
      gap: 24px;
      align-items: start;
    }}

    section, .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}

    .section-head {{
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }}

    h2 {{
      margin: 0;
      font-size: 1.05rem;
      line-height: 1.25;
    }}

    .count {{
      color: var(--muted);
      font-size: 0.9rem;
      white-space: nowrap;
    }}

    form {{
      padding: 20px;
    }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}

    .full {{
      grid-column: 1 / -1;
    }}

    label {{
      display: grid;
      gap: 6px;
      font-size: 0.9rem;
      font-weight: 700;
    }}

    input, select, textarea {{
      width: 100%;
      min-height: 42px;
      border: 1px solid #bdc9d5;
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      color: var(--ink);
      background: white;
    }}

    textarea {{
      min-height: 82px;
      resize: vertical;
    }}

    input:focus, select:focus, textarea:focus {{
      outline: 3px solid rgba(15, 143, 140, 0.18);
      border-color: var(--brand);
    }}

    .choice-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .switch {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      font-weight: 700;
      min-height: 44px;
    }}

    .switch input {{
      width: 18px;
      height: 18px;
      min-height: 18px;
      accent-color: var(--brand);
    }}

    .actions {{
      margin-top: 18px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}

    button, .button {{
      appearance: none;
      border: 0;
      border-radius: 6px;
      padding: 10px 14px;
      background: var(--brand);
      color: white;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
    }}

    button:hover, .button:hover {{
      background: var(--brand-dark);
    }}

    .button.secondary {{
      background: #e8eef4;
      color: var(--ink);
    }}

    .button.secondary:hover {{
      background: #dbe4ec;
    }}

    .alert {{
      margin-bottom: 16px;
      border-radius: 8px;
      padding: 12px 14px;
      font-weight: 700;
      border: 1px solid;
    }}

    .alert.success {{
      color: var(--ok);
      background: #eaf7f0;
      border-color: #b9e2cc;
    }}

    .alert.error {{
      color: var(--danger);
      background: #fff0f0;
      border-color: #f1b9b9;
    }}

    .field-error {{
      color: var(--danger);
      font-size: 0.8rem;
      font-weight: 700;
    }}

    .table-wrap {{
      overflow-x: auto;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 780px;
    }}

    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 0.92rem;
    }}

    th {{
      color: #415160;
      background: #f8fafc;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #e9f6f5;
      color: var(--brand-dark);
      font-weight: 800;
      font-size: 0.78rem;
      margin: 0 4px 4px 0;
      white-space: nowrap;
    }}

    .empty {{
      padding: 34px 20px;
      color: var(--muted);
      text-align: center;
    }}

    .delete {{
      background: transparent;
      color: var(--danger);
      border: 1px solid #efc2c2;
      min-height: 34px;
      padding: 6px 10px;
      font-size: 0.85rem;
    }}

    .delete:hover {{
      background: #fff0f0;
    }}

    @media (max-width: 920px) {{
      .layout {{
        grid-template-columns: 1fr;
      }}
    }}

    @media (max-width: 560px) {{
      .topbar, main {{
        padding-left: 16px;
        padding-right: 16px;
      }}

      .grid, .choice-grid {{
        grid-template-columns: 1fr;
      }}

      .section-head {{
        align-items: flex-start;
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div class="brand">
        <div class="logo">iK</div>
        <div>
          <h1>iKidsPark Rezerwacje</h1>
          <p class="subtitle">Panel zapisu urodzin i wydarzeń.</p>
        </div>
      </div>
      <a class="button secondary" href="/export">Eksport CSV</a>
    </div>
  </header>
  <main>
    {alert}
    {content}
  </main>
</body>
</html>"""
    return document.encode("utf-8")


def error_for(errors: dict[str, str], field: str) -> str:
    if field not in errors:
        return ""
    return f'<span class="field-error">{escape(errors[field])}</span>'


def selected(current: object, value: str) -> str:
    return " selected" if str(current) == value else ""


def checked(current: object) -> str:
    return " checked" if current == "Tak" else ""


def render_form(values: dict[str, object] | None = None, errors: dict[str, str] | None = None) -> str:
    values = values or {
        "reservation_date": "",
        "children_count": 8,
        "adults_count": 2,
        "parent_name": "",
        "child_name": "",
        "child_age": 6,
        "theme_room": THEME_ROOMS[0],
        "cake": "Nie",
        "animations": "Nie",
        "fruit": "Nie",
        "workshops": "Nie",
        "notes": "",
    }
    errors = errors or {}

    rooms = "\n".join(
        f'<option value="{escape(room)}"{selected(values.get("theme_room"), room)}>{escape(room)}</option>'
        for room in THEME_ROOMS
    )

    return f"""
<section>
  <div class="section-head">
    <h2>Nowa rezerwacja</h2>
  </div>
  <form method="post" action="/reservations">
    <div class="grid">
      <label>
        Data
        <input type="date" name="reservation_date" value="{escape(values.get("reservation_date", ""))}" required>
        {error_for(errors, "reservation_date")}
      </label>
      <label>
        Sala tematyczna
        <select name="theme_room" required>{rooms}</select>
        {error_for(errors, "theme_room")}
      </label>
      <label>
        Liczba dzieci
        <input type="number" name="children_count" min="1" max="80" value="{escape(values.get("children_count", ""))}" required>
        {error_for(errors, "children_count")}
      </label>
      <label>
        Liczba dorosłych
        <input type="number" name="adults_count" min="0" max="80" value="{escape(values.get("adults_count", ""))}" required>
        {error_for(errors, "adults_count")}
      </label>
      <label>
        Imię rodzica
        <input name="parent_name" autocomplete="name" value="{escape(values.get("parent_name", ""))}" required>
        {error_for(errors, "parent_name")}
      </label>
      <label>
        Imię dziecka
        <input name="child_name" value="{escape(values.get("child_name", ""))}" required>
        {error_for(errors, "child_name")}
      </label>
      <label>
        Wiek dziecka
        <input type="number" name="child_age" min="1" max="18" value="{escape(values.get("child_age", ""))}" required>
        {error_for(errors, "child_age")}
      </label>
      <div></div>
      <div class="full">
        <div class="choice-grid">
          <label class="switch">Tort <input type="checkbox" name="cake" value="Tak"{checked(values.get("cake"))}></label>
          <label class="switch">Animacje <input type="checkbox" name="animations" value="Tak"{checked(values.get("animations"))}></label>
          <label class="switch">Owoce <input type="checkbox" name="fruit" value="Tak"{checked(values.get("fruit"))}></label>
          <label class="switch">Warsztaty <input type="checkbox" name="workshops" value="Tak"{checked(values.get("workshops"))}></label>
        </div>
      </div>
      <label class="full">
        Notatki
        <textarea name="notes" placeholder="Dodatkowe ustalenia, alergie, godzina, kontakt...">{escape(values.get("notes", ""))}</textarea>
      </label>
    </div>
    <div class="actions">
      <button type="submit">Zapisz rezerwację</button>
      <a class="button secondary" href="/">Wyczyść formularz</a>
    </div>
  </form>
</section>
"""


def render_reservations() -> str:
    rows = get_reservations()
    if not rows:
        body = '<div class="empty">Brak zapisanych rezerwacji.</div>'
    else:
        table_rows = []
        for row in rows:
            additions = "".join(
                f'<span class="pill">{label}</span>'
                for field, label in (
                    ("cake", "Tort"),
                    ("animations", "Animacje"),
                    ("fruit", "Owoce"),
                    ("workshops", "Warsztaty"),
                )
                if row[field] == "Tak"
            )
            if not additions:
                additions = '<span class="pill">Bez dodatków</span>'

            notes = f'<div class="subtitle">{escape(row["notes"])}</div>' if row["notes"] else ""
            table_rows.append(
                f"""
                <tr>
                  <td><strong>{escape(row["reservation_date"])}</strong></td>
                  <td>{escape(row["parent_name"])}<br><span class="subtitle">dziecko: {escape(row["child_name"])}, {escape(row["child_age"])} lat</span>{notes}</td>
                  <td>{escape(row["children_count"])} dzieci<br>{escape(row["adults_count"])} dorosłych</td>
                  <td>{escape(row["theme_room"])}</td>
                  <td>{additions}</td>
                  <td>
                    <form method="post" action="/delete" onsubmit="return confirm('Usunąć tę rezerwację?')">
                      <input type="hidden" name="id" value="{escape(row["id"])}">
                      <button class="delete" type="submit">Usuń</button>
                    </form>
                  </td>
                </tr>
                """
            )
        body = f"""
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Data</th>
                <th>Rodzina</th>
                <th>Goście</th>
                <th>Sala</th>
                <th>Dodatki</th>
                <th>Akcje</th>
              </tr>
            </thead>
            <tbody>
              {"".join(table_rows)}
            </tbody>
          </table>
        </div>
        """

    return f"""
<section>
  <div class="section-head">
    <h2>Lista rezerwacji</h2>
    <span class="count">{len(rows)} zapisanych</span>
  </div>
  {body}
</section>
"""


def render_home(message: str = "", values: dict[str, object] | None = None, errors: dict[str, str] | None = None) -> bytes:
    content = f"""
<div class="layout">
  {render_form(values, errors)}
  {render_reservations()}
</div>
"""
    return page_template(content, message=message, errors=errors)


def parse_post(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def csv_response() -> bytes:
    headers = [
        "Data",
        "Liczba dzieci",
        "Liczba dorosłych",
        "Imię rodzica",
        "Imię dziecka",
        "Wiek dziecka",
        "Sala tematyczna",
        "Tort",
        "Animacje",
        "Owoce",
        "Warsztaty",
        "Notatki",
    ]
    lines = [";".join(headers)]
    for row in get_reservations():
        values = [
            row["reservation_date"],
            row["children_count"],
            row["adults_count"],
            row["parent_name"],
            row["child_name"],
            row["child_age"],
            row["theme_room"],
            row["cake"],
            row["animations"],
            row["fruit"],
            row["workshops"],
            row["notes"],
        ]
        lines.append(";".join(f'"{str(value).replace(chr(34), chr(34) + chr(34))}"' for value in values))
    return ("\ufeff" + "\n".join(lines)).encode("utf-8")


class ReservationHandler(BaseHTTPRequestHandler):
    def send_bytes(
        self,
        payload: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "text/html; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            message = parse_qs(parsed.query).get("message", [""])[0]
            self.send_bytes(render_home(message=message))
            return

        if parsed.path == "/export":
            self.send_bytes(
                csv_response(),
                content_type="text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="ikidspark-rezerwacje.csv"'},
            )
            return

        self.send_bytes(
            page_template('<div class="panel empty">Nie znaleziono strony.</div>'),
            status=HTTPStatus.NOT_FOUND,
        )

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/export"}:
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                "text/csv; charset=utf-8" if parsed.path == "/export" else "text/html; charset=utf-8",
            )
            self.end_headers()
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        data = parse_post(self)

        if parsed.path == "/reservations":
            values, errors = validate_reservation(data)
            if errors:
                self.send_bytes(render_home(values=values, errors=errors), status=HTTPStatus.BAD_REQUEST)
                return
            save_reservation(values)
            self.redirect("/?message=Rezerwacja%20zosta%C5%82a%20zapisana.")
            return

        if parsed.path == "/delete":
            try:
                reservation_id = int(data.get("id", ""))
            except ValueError:
                self.redirect("/")
                return
            execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
            self.redirect("/?message=Rezerwacja%20zosta%C5%82a%20usuni%C4%99ta.")
            return

        payload = json.dumps({"error": "Unsupported route"}).encode("utf-8")
        self.send_bytes(payload, status=HTTPStatus.NOT_FOUND, content_type="application/json")

    def log_message(self, format: str, *args: object) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}")


def run() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), ReservationHandler)
    print(f"{APP_TITLE} działa pod adresem http://{HOST}:{PORT}")
    print("Zatrzymaj serwer skrótem Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymano serwer.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
