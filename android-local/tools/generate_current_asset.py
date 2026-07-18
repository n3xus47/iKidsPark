#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "android-local" / "app" / "src" / "main" / "assets"
sys.path.insert(0, str(ROOT))

import main  # noqa: E402


def row_payload(row: object) -> dict[str, object]:
    birthday_children = []
    try:
        birthday_children = json.loads(row["birthday_children_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        birthday_children = []
    if not birthday_children:
        birthday_children = [
            {
                "name": row["birthday_child_name"],
                "age": row["birthday_child_age"],
            }
        ]

    return {
        "id": row["id"],
        "reservation_date": main.format_date(row["start_at"]),
        "party_start_time": main.format_time(row["start_at"]),
        "children_count": row["children_count"],
        "adults_count": row["adults_count"],
        "guest_total": row.get("guest_total") or "",
        "reservation_type": row.get("reservation_type") or "banquet",
        "parent_name": row["parent_name"],
        "parent_phone": row.get("parent_phone") or "",
        "birthday_child_name": row["birthday_child_name"],
        "birthday_child_age": row["birthday_child_age"],
        "birthday_children": birthday_children,
        "child_location": row["child_location"],
        "adult_location": main.location_values(row["adult_location"]),
        "animation_enabled": bool(row["animation_enabled"]),
        "animation_type": row["animation_type"] or "",
        "animation_at": main.format_time(row["animation_at"]),
        "cake_enabled": bool(row["cake_enabled"]),
        "cake_theme": row["cake_theme"] or "",
        "cake_weight": row.get("cake_weight") or "",
        "cake_sponge": row.get("cake_sponge") or "",
        "cake_filling": row.get("cake_filling") or "",
        "cake_cream": row.get("cake_cream") or "",
        "cake_at": main.format_time(row["cake_at"]),
        "fruit_enabled": bool(row["fruit_enabled"]),
        "fruit_plates": row["fruit_plates"] or "",
        "culinary_workshops_enabled": bool(row["culinary_workshops_enabled"]),
        "culinary_workshops_type": row["culinary_workshops_type"] or "",
        "culinary_workshops_at": main.format_time(row["culinary_workshops_at"]),
        "pinata_enabled": bool(row["pinata_enabled"]),
        "pinata_theme": row["pinata_theme"] or "",
        "pinata_at": main.format_time(row["pinata_at"]),
        "mascot_enabled": bool(row["mascot_enabled"]),
        "mascot_type": row["mascot_type"] or "",
        "mascot_at": main.format_time(row["mascot_at"]),
        "balloons_enabled": bool(row["balloons_enabled"]),
        "balloons_description": row["balloons_description"] or "",
        "notes": row["notes"] or "",
        "status": row["status"],
        "cancellation_reason": row["cancellation_reason"] or "",
        "assigned_waiter": row["assigned_waiter"] or "",
    }


def androidize_html(html: str, initial_rows: list[dict[str, object]]) -> str:
    html = html.replace('href="/manifest.webmanifest"', 'href="#" data-local-disabled="manifest"')
    html = re.sub(r'src="/(?:menu-logo|logo)\.png[^"]*"', 'src="logo.png"', html)
    html = re.sub(r'href="/app-icon-[0-9]+\.png"', 'href="logo.png"', html)
    html = html.replace(
        "</head>",
        """
  <style>
    .install-button { display: none !important; }
    .ikids-local-toast {
      position: fixed;
      left: 14px;
      right: 14px;
      bottom: max(14px, env(safe-area-inset-bottom));
      z-index: 9999;
      transform: translateY(140%);
      transition: transform .2s ease;
      background: #000;
      color: #fff;
      border-radius: 10px;
      padding: 12px 14px;
      box-shadow: 0 12px 36px rgba(0,0,0,.24);
      font-weight: 800;
    }
    .ikids-local-toast.is-visible { transform: translateY(0); }
  </style>
</head>""",
    )
    injected = LOCAL_SCRIPT.replace(
        "__INITIAL_ROWS__",
        json.dumps(initial_rows, ensure_ascii=False, separators=(",", ":")),
    )
    return html.replace("</body>", injected + "\n</body>")


LOCAL_SCRIPT = r"""
<script>
(() => {
  "use strict";

  const STORAGE_KEY = "ikids-main-local-reservations-v2";
  const ROLE_KEY = "ikids-main-local-role-v2";
  const DAY_KEY = "ikids-main-local-day-v2";
  const EMPTY = "Brak";
  const DAY_MS = 86400000;
  const initialRows = __INITIAL_ROWS__;
  const serviceDurations = {
    animation_at: 60,
    cake_at: 20,
    culinary_workshops_at: 60,
    pinata_at: 20,
    mascot_at: 20,
  };
  const roleLabels = {
    manager: "Kierownik i recepcja",
    animators: "Animatorzy",
    kitchen: "Kuchnia",
    organizer: "Organizator urodzin",
    home: "Strona główna",
  };

  let state = {
    role: localStorage.getItem(ROLE_KEY) || "organizer",
    day: localStorage.getItem(DAY_KEY) || "today",
    rows: loadRows(),
  };

  function loadRows() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (error) {
      console.warn(error);
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(initialRows));
    return initialRows.slice();
  }

  function saveRows() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.rows));
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function isoDate(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function dayToDate(day) {
    const now = new Date();
    if (!day || day === "today") return isoDate(now);
    if (day === "tomorrow") return isoDate(new Date(now.getTime() + DAY_MS));
    if (day === "after_tomorrow") return isoDate(new Date(now.getTime() + DAY_MS * 2));
    return day;
  }

  function displayDate(day) {
    return new Intl.DateTimeFormat("pl-PL", { weekday: "long", day: "2-digit", month: "long" })
      .format(new Date(dayToDate(day) + "T12:00:00"));
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    })[char]);
  }

  function minutes(value) {
    if (!/^\d{2}:\d{2}$/.test(String(value || ""))) return null;
    const [h, m] = value.split(":").map(Number);
    return h * 60 + m;
  }

  function addMinutes(value, amount) {
    const total = minutes(value);
    if (total === null) return "";
    return `${pad(Math.floor((total + amount) / 60) % 24)}:${pad((total + amount) % 60)}`;
  }

  function formatWindow(value, duration) {
    return value ? `${value}-${addMinutes(value, duration)}` : "";
  }

  function childNames(row) {
    const children = Array.isArray(row.birthday_children) ? row.birthday_children : [];
    const names = children.map((child) => child.name).filter(Boolean);
    return names.length ? names.join(", ") : row.birthday_child_name || "";
  }

  function ageLabel(row) {
    const children = Array.isArray(row.birthday_children) ? row.birthday_children : [];
    if (children.length > 1) return `${children.length} solenizantów`;
    const age = children[0]?.age || row.birthday_child_age;
    return age ? `${age} lat` : "";
  }

  function adultLocations(row) {
    if (Array.isArray(row.adult_location)) return row.adult_location.filter((item) => item && item !== EMPTY);
    return String(row.adult_location || "").split("|").map((item) => item.trim()).filter((item) => item && item !== EMPTY);
  }

  function locationText(row) {
    const locations = adultLocations(row);
    return locations.length ? locations.join(", ") : EMPTY;
  }

  function guestText(row) {
    if ((row.reservation_type || "banquet") === "table") {
      return `${Number(row.guest_total || row.children_count || 0)} os.`;
    }
    const children = Number(row.children_count || 0);
    const adults = Number(row.adults_count || 0);
    return `${children + adults} os. (${children} dzieci, ${adults} dorosłych)`;
  }

  function workshopChildrenText(row) {
    return `${Number(row.children_count || 0)} dzieci`;
  }

  function cakeDetails(row) {
    return [
      ["Waga", row.cake_weight],
      ["Biszkopt", row.cake_sponge],
      ["Nadzienie", row.cake_filling],
      ["Krem", row.cake_cream],
    ]
      .filter(([, value]) => String(value || "").trim())
      .map(([label, value]) => `${label}: ${value}`)
      .join(" · ");
  }

  function selectedRows() {
    const date = dayToDate(state.day);
    return state.rows
      .filter((row) => row.reservation_date === date)
      .sort((a, b) => String(a.party_start_time).localeCompare(String(b.party_start_time)));
  }

  function visibleRows() {
    const rows = selectedRows();
    if (state.role === "animators") {
      return rows.filter((row) => row.animation_enabled || row.pinata_enabled || row.mascot_enabled || row.balloons_enabled);
    }
    if (state.role === "kitchen") {
      return rows.filter((row) => row.fruit_enabled || row.cake_enabled || row.culinary_workshops_enabled);
    }
    return rows;
  }

  function serviceChips(row, role = state.role) {
    const left = [];
    const right = [];
    if (locationText(row) !== EMPTY) left.push(chip("location", locationText(row), ""));
    if (row.fruit_enabled) left.push(chip("kitchen", `Owoce (${row.fruit_plates || 0} tal.)`, ""));
    if (row.cake_enabled) {
      const cakeMeta = [formatWindow(row.cake_at, serviceDurations.cake_at), cakeDetails(row)].filter(Boolean).join(" · ");
      left.push(chip("cake", `Tort: ${row.cake_theme || "(brak)"}`, cakeMeta));
    }
    if (row.culinary_workshops_enabled) {
      left.push(chip("kitchen", `Warsztaty: ${row.culinary_workshops_type || "(brak)"} (${workshopChildrenText(row)})`, formatWindow(row.culinary_workshops_at, serviceDurations.culinary_workshops_at)));
    }
    if (row.animation_enabled) right.push(chip("attraction", `Animacja: ${row.animation_type || "(brak)"}`, formatWindow(row.animation_at, serviceDurations.animation_at)));
    if (row.pinata_enabled) right.push(chip("attraction", `Piniata: ${row.pinata_theme || "(brak)"}`, formatWindow(row.pinata_at, serviceDurations.pinata_at)));
    if (row.mascot_enabled) right.push(chip("attraction", `Maskotka: ${row.mascot_type || "(brak)"}`, formatWindow(row.mascot_at, serviceDurations.mascot_at)));
    if (row.balloons_enabled) right.push(chip("attraction", `Balony: ${row.balloons_description || "(brak)"}`, ""));

    if (role === "animators") return right.join("") || chip("attraction", "Brak zadań animatorów", "");
    if (role === "kitchen") return left.join("") || chip("kitchen", "Brak zadań kuchni", "");
    return `<div class="logistics-column">${left.join("")}</div><div class="logistics-column">${right.join("")}</div>`;
  }

  function chip(kind, text, sub) {
    return `
      <div class="logistics-chip logistics-chip-${kind}">
        <span class="logistics-chip-content">
          <span class="logistics-chip-text">${escapeHtml(text)}</span>
          ${sub ? `<span class="logistics-chip-sub">${escapeHtml(sub)}</span>` : ""}
        </span>
      </div>`;
  }

  function statusBadge(row) {
    if (row.status !== "cancelled") return "";
    return `<div class="timeline-status"><span class="status-badge status-badge-cancelled">Anulowana</span></div>`;
  }

  function rowCard(row) {
    const cancelled = row.status === "cancelled";
    return `
      <article class="timeline-card ${cancelled ? "is-cancelled" : ""}" data-local-id="${escapeHtml(row.id)}">
        <header class="timeline-header">
          <time class="timeline-start" datetime="${escapeHtml(row.reservation_date)}T${escapeHtml(row.party_start_time)}">${escapeHtml(row.party_start_time)}</time>
          <div class="profile-identity">
            <h3 class="profile-name">${escapeHtml(childNames(row))}</h3>
            <div class="profile-tags">
              ${ageLabel(row) ? `<span class="profile-tag profile-tag-age">${escapeHtml(ageLabel(row))}</span>` : ""}
              ${row.child_location && row.child_location !== EMPTY ? `<span class="profile-tag profile-tag-room profile-tag-room-default">${escapeHtml(row.child_location)}</span>` : ""}
              <span class="profile-tag profile-tag-guests">${escapeHtml(guestText(row))}</span>
            </div>
          </div>
          <div class="profile-guardian"><span>${escapeHtml(row.parent_name)}</span></div>
          ${statusBadge(row)}
        </header>
        <div class="timeline-logistics">${serviceChips(row)}</div>
        ${row.notes ? `<div class="reservation-callout reservation-callout-warning" role="note"><p class="reservation-callout-text">${escapeHtml(row.notes)}</p></div>` : ""}
        ${row.cancellation_reason ? `<div class="reservation-callout reservation-callout-danger" role="note"><p class="reservation-callout-text">${escapeHtml(row.cancellation_reason)}</p></div>` : ""}
        <footer class="timeline-footer">
          <div class="inline-actions">
            <button class="button secondary" type="button" data-local-edit="${escapeHtml(row.id)}">Edytuj</button>
            <button class="button secondary" type="button" data-local-cancel="${escapeHtml(row.id)}">Anuluj</button>
            <button class="button danger" type="button" data-local-delete="${escapeHtml(row.id)}">Usuń</button>
          </div>
        </footer>
      </article>`;
  }

  function roleCard(row) {
    return `
      <article class="role-card" data-local-id="${escapeHtml(row.id)}">
        <header class="role-card-head">
          <span class="role-card-kicker">${escapeHtml(row.party_start_time)}</span>
          <div class="profile-identity">
            <h3 class="profile-name">${escapeHtml(childNames(row))}</h3>
            <div class="profile-tags">${row.child_location !== EMPTY ? `<span class="profile-tag profile-tag-room profile-tag-room-default">${escapeHtml(row.child_location)}</span>` : ""}</div>
          </div>
        </header>
        <div class="timeline-logistics">${serviceChips(row, state.role)}</div>
        ${row.notes ? `<p class="banquet-notes">${escapeHtml(row.notes)}</p>` : ""}
      </article>`;
  }

  function metrics(rows) {
    const active = rows.filter((row) => row.status !== "cancelled");
    const guests = active.reduce((sum, row) => sum + Number(row.children_count || 0) + Number(row.adults_count || 0), 0);
    const animations = active.filter((row) => row.animation_enabled).length;
    const cakes = active.filter((row) => row.cake_enabled).length;
    return `
      <div class="home-summary">
        <div class="metric"><strong>${active.length}</strong><span>bankiety</span></div>
        <div class="metric"><strong>${guests}</strong><span>liczba gości</span></div>
        <div class="metric"><strong>${animations}</strong><span>animacje</span></div>
        <div class="metric"><strong>${cakes}</strong><span>torty</span></div>
      </div>`;
  }

  function renderList() {
    const stacks = document.querySelectorAll("main .stack");
    const formStack = stacks[0];
    const list = stacks[stacks.length - 1];
    if (!list) return;
    const organizerMode = state.role === "organizer";
    const organizerTools = document.querySelector(".organizer-tools");
    if (formStack && formStack !== list) {
      formStack.hidden = !organizerMode;
      formStack.style.display = organizerMode ? "" : "none";
    }
    if (organizerTools) {
      organizerTools.hidden = !organizerMode;
      organizerTools.style.display = organizerMode ? "" : "none";
    }
    const rows = visibleRows();
    const title = state.role === "home" ? "Podsumowanie dnia" : state.role === "animators" ? "Zadania animatorów" : state.role === "kitchen" ? "Zadania kuchni" : "Rezerwacje dnia";
    const cards = rows.length
      ? rows.map((row) => state.role === "animators" || state.role === "kitchen" ? roleCard(row) : rowCard(row)).join("")
      : `<div class="empty">Brak pozycji dla wybranego dnia i roli.</div>`;
    list.innerHTML = `
      <div class="section-head">
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p class="subtitle">${escapeHtml(displayDate(state.day))}. Dane działają lokalnie w APK.</p>
        </div>
        <span class="count">${rows.length} pozycji</span>
      </div>
      ${state.role === "home" ? metrics(selectedRows()) : ""}
      <div class="${state.role === "animators" || state.role === "kitchen" ? "role-board" : ""}">
        ${state.role === "animators" || state.role === "kitchen" ? `<div class="banquet-grid">${cards}</div>` : cards}
      </div>`;
  }

  function renderNav() {
    document.querySelectorAll(".tabs .tab").forEach((tab) => {
      const role = new URL(tab.getAttribute("href") || "http://local/?role=manager", "http://local").searchParams.get("role") || "manager";
      if (role === state.role) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });
    const date = dayToDate(state.day);
    document.querySelectorAll(".date-day").forEach((link) => {
      const day = new URL(link.getAttribute("href") || "http://local/", "http://local").searchParams.get("day") || "today";
      const active = dayToDate(day) === date;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const dateInput = document.getElementById("reservation_date");
    if (dateInput) dateInput.value = date;
  }

  function renderAvailability() {
    const rows = selectedRows().filter((row) => row.status !== "cancelled");
    const busy = new Map();
    rows.forEach((row) => {
      const label = `Zajęte: ${childNames(row)}`;
      if (row.child_location && row.child_location !== EMPTY) busy.set(row.child_location, label);
      adultLocations(row).forEach((location) => busy.set(location, label));
    });
    document.querySelectorAll("[data-location]").forEach((node) => {
      const label = busy.get(node.dataset.location);
      const occupied = Boolean(label);
      node.classList.toggle("is-busy", occupied);
      node.classList.toggle("is-occupied", occupied);
      const title = node.querySelector("title");
      if (title) title.textContent = label || "Wolne";
    });
  }

  function render() {
    localStorage.setItem(ROLE_KEY, state.role);
    localStorage.setItem(DAY_KEY, state.day);
    renderNav();
    renderList();
    renderAvailability();
  }

  function formRows(form) {
    const fd = new FormData(form);
    const names = fd.getAll("birthday_child_name").map((item) => String(item || "").trim());
    const ages = fd.getAll("birthday_child_age").map((item) => Number(item || 0));
    return names.map((name, index) => ({ name, age: ages[index] || "" })).filter((child) => child.name);
  }

  function getChecked(form, name) {
    return Boolean(form.querySelector(`[name="${CSS.escape(name)}"]`)?.checked);
  }

  function getValue(form, name) {
    return String(new FormData(form).get(name) || "").trim();
  }

  function collectForm(form) {
    const fd = new FormData(form);
    const children = formRows(form);
    const adult = fd.getAll("adult_location").map((item) => String(item || "").trim()).filter((item) => item && item !== EMPTY);
    const first = children[0] || { name: "", age: "" };
    return {
      id: getValue(form, "id") || String(Date.now()),
      reservation_date: getValue(form, "reservation_date"),
      party_start_time: getValue(form, "party_start_time"),
      reservation_type: getValue(form, "reservation_type") || "banquet",
      children_count: Number(getValue(form, "children_count") || 0),
      adults_count: Number(getValue(form, "adults_count") || 0),
      guest_total: Number(getValue(form, "guest_total") || 0),
      parent_name: getValue(form, "parent_name"),
      parent_phone: getValue(form, "parent_phone"),
      birthday_child_name: first.name,
      birthday_child_age: first.age,
      birthday_children: children,
      child_location: getValue(form, "child_location") || EMPTY,
      adult_location: adult,
      animation_enabled: getChecked(form, "animation_enabled"),
      animation_type: getValue(form, "animation_type"),
      animation_at: getValue(form, "animation_at"),
      cake_enabled: getChecked(form, "cake_enabled"),
      cake_theme: getValue(form, "cake_theme") || "(brak)",
      cake_weight: getValue(form, "cake_weight"),
      cake_sponge: getValue(form, "cake_sponge"),
      cake_filling: getValue(form, "cake_filling"),
      cake_cream: getValue(form, "cake_cream"),
      cake_at: getValue(form, "cake_at"),
      fruit_enabled: getChecked(form, "fruit_enabled"),
      fruit_plates: getValue(form, "fruit_plates"),
      culinary_workshops_enabled: getChecked(form, "culinary_workshops_enabled"),
      culinary_workshops_type: getValue(form, "culinary_workshops_type"),
      culinary_workshops_at: getValue(form, "culinary_workshops_at"),
      pinata_enabled: getChecked(form, "pinata_enabled"),
      pinata_theme: getValue(form, "pinata_theme") || "(brak)",
      pinata_at: getValue(form, "pinata_at"),
      mascot_enabled: getChecked(form, "mascot_enabled"),
      mascot_type: getValue(form, "mascot_type"),
      mascot_at: getValue(form, "mascot_at"),
      balloons_enabled: getChecked(form, "balloons_enabled"),
      balloons_description: getValue(form, "balloons_description") || "(brak)",
      notes: getValue(form, "notes"),
      status: getValue(form, "status") || "active",
      cancellation_reason: getValue(form, "cancellation_reason"),
      assigned_waiter: "",
    };
  }

  function stageBlocked(row) {
    return [
      ["animation_enabled", "animation_at", 60],
      ["cake_enabled", "cake_at", 20],
      ["culinary_workshops_enabled", "culinary_workshops_at", 60],
      ["pinata_enabled", "pinata_at", 20],
      ["mascot_enabled", "mascot_at", 20],
    ].some(([enabled, field, duration]) => {
      if (!row[enabled]) return false;
      const start = minutes(row[field]);
      if (start === null) return false;
      return start < minutes("18:15") && start + duration > minutes("17:45");
    });
  }

  function serviceOverlap(row) {
    const slots = [
      ["animation_enabled", "animation_at", 60],
      ["cake_enabled", "cake_at", 20],
      ["culinary_workshops_enabled", "culinary_workshops_at", 60],
      ["pinata_enabled", "pinata_at", 20],
      ["mascot_enabled", "mascot_at", 20],
    ].filter(([enabled, field]) => row[enabled] && minutes(row[field]) !== null)
      .map(([, field, duration]) => [minutes(row[field]), minutes(row[field]) + duration]);
    return slots.some((slot, index) => slots.slice(index + 1).some((other) => slot[0] < other[1] && other[0] < slot[1]));
  }

  function locationConflict(row) {
    const requested = new Set(adultLocations(row));
    if (row.child_location && row.child_location !== EMPTY) requested.add(row.child_location);
    if (!requested.size || row.status === "cancelled") return null;
    return state.rows.find((existing) => {
      if (String(existing.id) === String(row.id)) return false;
      if (existing.status === "cancelled") return false;
      if (existing.reservation_date !== row.reservation_date) return false;
      const existingLocations = new Set(adultLocations(existing));
      if (existing.child_location && existing.child_location !== EMPTY) existingLocations.add(existing.child_location);
      return [...requested].some((location) => existingLocations.has(location));
    }) || null;
  }

  function validate(row) {
    if (!row.reservation_date || !row.party_start_time || !row.parent_name || !row.birthday_children.length) {
      return "Uzupełnij termin, rodzica i co najmniej jednego solenizanta.";
    }
    if (row.status === "cancelled" && !row.cancellation_reason) {
      return "Powód anulowania jest wymagany przy statusie Anulowana.";
    }
    if (stageBlocked(row)) return "Atrakcja nakłada się na blokadę Koła Marzeń 17:45-18:15.";
    if (serviceOverlap(row)) return "Godziny dodatków nakładają się na siebie.";
    const conflict = locationConflict(row);
    if (conflict) return `Wybrana sala lub stolik jest zajęty przez: ${childNames(conflict)}.`;
    return "";
  }

  function resetForm(form) {
    form.reset();
    const id = document.getElementById("reservation_id");
    if (id) id.value = "";
    const date = document.getElementById("reservation_date");
    if (date) date.value = dayToDate(state.day);
    document.querySelectorAll(".service-catalog-body").forEach((body) => body.classList.add("is-hidden"));
    document.querySelectorAll(".service-enabled-input").forEach((input) => { input.checked = false; });
    document.querySelectorAll(".service-add-btn").forEach((button) => { button.textContent = "Dodaj"; });
    toast("Formularz wyczyszczony.");
  }

  function saveForm(form) {
    const row = collectForm(form);
    const error = validate(row);
    if (error) {
      toast(error);
      return;
    }
    const index = state.rows.findIndex((item) => String(item.id) === String(row.id));
    if (index >= 0) state.rows[index] = row;
    else state.rows.push(row);
    saveRows();
    resetForm(form);
    state.day = row.reservation_date;
    render();
    toast("Rezerwacja zapisana lokalnie w APK.");
  }

  function fillForm(row) {
    const form = document.getElementById("reservation-form");
    if (!form) return;
    const set = (name, value) => {
      const field = form.querySelector(`[name="${CSS.escape(name)}"]`);
      if (field) field.value = value ?? "";
    };
    set("id", row.id);
    set("reservation_date", row.reservation_date);
    set("party_start_time", row.party_start_time);
    const type = row.reservation_type || "banquet";
    document.querySelectorAll('[name="reservation_type"]').forEach((input) => {
      input.checked = input.value === type;
    });
    set("children_count", row.children_count);
    set("adults_count", row.adults_count);
    set("guest_total", row.guest_total);
    set("parent_name", row.parent_name);
    set("parent_phone", row.parent_phone);
    set("child_location", row.child_location || EMPTY);
    set("animation_type", row.animation_type);
    set("animation_at", row.animation_at);
    set("cake_theme", row.cake_theme);
    set("cake_weight", row.cake_weight);
    set("cake_sponge", row.cake_sponge);
    set("cake_filling", row.cake_filling);
    set("cake_cream", row.cake_cream);
    set("cake_at", row.cake_at);
    set("fruit_plates", row.fruit_plates);
    set("culinary_workshops_type", row.culinary_workshops_type);
    set("culinary_workshops_at", row.culinary_workshops_at);
    set("pinata_theme", row.pinata_theme);
    set("pinata_at", row.pinata_at);
    set("mascot_type", row.mascot_type);
    set("mascot_at", row.mascot_at);
    set("balloons_description", row.balloons_description);
    set("notes", row.notes);
    set("cancellation_reason", row.cancellation_reason);
    document.querySelectorAll('[name="adult_location"] option').forEach((option) => {
      option.selected = adultLocations(row).includes(option.value);
    });
    const list = document.getElementById("birthday-children-list");
    if (list) {
      list.innerHTML = row.birthday_children.map((child, index) => `
        <div class="birthday-child-row">
          <label>Imię solenizanta<input name="birthday_child_name" value="${escapeHtml(child.name)}" required></label>
          <label>Wiek<input type="number" name="birthday_child_age" min="1" max="18" value="${escapeHtml(child.age)}" required></label>
          ${index ? '<button type="button" class="button secondary remove-birthday-child" aria-label="Usuń solenizanta">Usuń</button>' : ""}
        </div>`).join("");
    }
    [
      "animation_enabled",
      "cake_enabled",
      "fruit_enabled",
      "culinary_workshops_enabled",
      "pinata_enabled",
      "mascot_enabled",
      "balloons_enabled",
    ].forEach((name) => {
      const input = form.querySelector(`[name="${CSS.escape(name)}"]`);
      const item = input?.closest(".service-catalog-item");
      const body = item?.querySelector(".service-catalog-body");
      const button = item?.querySelector(".service-add-btn");
      if (!input) return;
      input.checked = Boolean(row[name]);
      body?.classList.toggle("is-hidden", !row[name]);
      if (button) button.textContent = row[name] ? "Dodano" : "Dodaj";
    });
    document.querySelector("section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    toast("Załadowano rezerwację do edycji.");
  }

  function exportCsv() {
    const header = ["ID","Data","Start","Rodzic","Solenizanci","Sala","Stoliki","Status","Notatki"];
    const lines = state.rows.map((row) => [row.id,row.reservation_date,row.party_start_time,row.parent_name,childNames(row),row.child_location,locationText(row),row.status,row.notes]
      .map((value) => `"${String(value ?? "").replaceAll('"', '""')}"`).join(";"));
    const blob = new Blob(["\ufeff" + [header.join(";"), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "ikidspark-lokalnie.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  function toast(message) {
    let node = document.querySelector(".ikids-local-toast");
    if (!node) {
      node = document.createElement("div");
      node.className = "ikids-local-toast";
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.classList.add("is-visible");
    window.setTimeout(() => node.classList.remove("is-visible"), 2600);
  }

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form");
    if (!form) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (form.id === "reservation-form") saveForm(form);
  }, true);

  document.addEventListener("click", (event) => {
    const edit = event.target.closest("[data-local-edit]");
    const del = event.target.closest("[data-local-delete]");
    const cancel = event.target.closest("[data-local-cancel]");
    const clear = event.target.closest('a.button[href^="/?role=organizer"]');
    const link = event.target.closest("a[href]");

    if (edit) {
      event.preventDefault();
      const row = state.rows.find((item) => String(item.id) === String(edit.dataset.localEdit));
      if (row) fillForm(row);
      return;
    }
    if (cancel) {
      event.preventDefault();
      const row = state.rows.find((item) => String(item.id) === String(cancel.dataset.localCancel));
      if (!row) return;
      const reason = prompt("Powód anulowania:", row.cancellation_reason || "");
      if (!reason) return;
      row.status = "cancelled";
      row.cancellation_reason = reason;
      saveRows();
      render();
      toast("Rezerwacja anulowana lokalnie.");
      return;
    }
    if (del) {
      event.preventDefault();
      if (!confirm("Usunąć tę rezerwację lokalnie?")) return;
      state.rows = state.rows.filter((item) => String(item.id) !== String(del.dataset.localDelete));
      saveRows();
      render();
      toast("Rezerwacja usunięta lokalnie.");
      return;
    }
    if (clear) {
      event.preventDefault();
      resetForm(document.getElementById("reservation-form"));
      return;
    }
    if (!link) return;
    const href = link.getAttribute("href") || "";
    if (href === "/export") {
      event.preventDefault();
      exportCsv();
      return;
    }
    if (href.startsWith("/schema") || href.startsWith("/history")) {
      event.preventDefault();
      toast("Ten ekran w APK działa lokalnie bez serwera. Historia i schema zostają w wersji webowej.");
      return;
    }
    if (href.startsWith("/?")) {
      event.preventDefault();
      const params = new URL(href, "http://local").searchParams;
      if (link.classList.contains("date-day")) {
        state.day = params.get("day") || state.day;
      } else if (link.classList.contains("tab")) {
        state.role = params.get("role") || state.role;
      } else {
        state.role = params.get("role") || state.role;
        state.day = params.get("day") || state.day;
      }
      render();
      if (state.role !== "organizer") window.scrollTo({ top: 0, left: 0, behavior: "smooth" });
    }
  }, true);

  const originalFetch = window.fetch;
  window.fetch = (input, init) => {
    const url = String(input);
    if (url.includes("/api/availability")) {
      const locations = {};
      selectedRows().filter((row) => row.status !== "cancelled").forEach((row) => {
        const label = `Zajęte: ${childNames(row)}`;
        if (row.child_location && row.child_location !== EMPTY) locations[row.child_location] = { status: "occupied", label };
        adultLocations(row).forEach((location) => { locations[location] = { status: "occupied", label }; });
      });
      return Promise.resolve(new Response(JSON.stringify({ locations }), { headers: { "Content-Type": "application/json" } }));
    }
    return originalFetch(input, init);
  };

  window.addEventListener("DOMContentLoaded", render);
  render();
})();
</script>
"""


def main_cli() -> None:
    main.init_db()
    initial_rows = [row_payload(row) for row in main.get_all_reservations()]
    if not initial_rows:
        target_day = main.date.today()
        values = main.default_form_values(target_day)
        values.update(
            {
                "party_start_time": "10:00",
                "children_count": 10,
                "adults_count": 4,
                "parent_name": "Demo Rodzic",
                "birthday_child_name": "Demo",
                "birthday_child_age": 7,
                "birthday_children": [{"name": "Demo", "age": 7}],
                "child_location": "1. Biały Dom",
                "adult_location": ["Bar - Stolik 7"],
                "status": "active",
            }
        )
        initial_rows = [values]
    html = main.render_home(role="organizer", day="today").decode("utf-8")
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "index.html").write_text(androidize_html(html, initial_rows), encoding="utf-8")
    print(ASSETS / "index.html")


if __name__ == "__main__":
    main_cli()
